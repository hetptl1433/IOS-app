import Foundation
import Combine
@preconcurrency import CoreBluetooth

enum ControllerWiFiCredentials {
    static func validate(ssid: String, password: String) throws {
        let hexPSK = password.utf8.count == 64 && password.utf8.allSatisfy {
            (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0)
        }
        guard (1...32).contains(ssid.utf8.count), !ssid.contains("\0"), !password.contains("\0"),
              (8...63).contains(password.utf8.count) || hexPSK else {
            throw ControllerConnectionError.unavailable("Enter a Wi-Fi name and a WPA2/WPA3 personal password (8–63 bytes or 64 hex digits).")
        }
    }
}

/// What the Pi reports for the Status screen: its health, Bluetooth and Wi-Fi.
struct ControllerStatus: Equatable, Sendable {
    struct WiFi: Equatable, Sendable {
        let connected: Bool
        let ssid: String?
        /// The Pi accepts a new network while it is offline, or in the first ten minutes after it starts.
        let canSetUp: Bool
    }
    let uptimeSeconds: Int
    let temperatureC: Double?
    let bluetooth: Bool
    /// Nil when this controller keeps its own network settings.
    let wifi: WiFi?

    init?(_ body: [String: WireValue]) {
        guard case .number(let uptime)? = body["uptimeSeconds"], case .bool(let bluetooth)? = body["bluetooth"] else { return nil }
        uptimeSeconds = Int(uptime)
        self.bluetooth = bluetooth
        if case .number(let celsius)? = body["temperatureC"] { temperatureC = celsius } else { temperatureC = nil }
        if case .object(let fields)? = body["wifi"], case .bool(let connected)? = fields["connected"],
           case .bool(let canSetUp)? = fields["canSetUp"] {
            wifi = WiFi(connected: connected, ssid: fields["ssid"]?.string, canSetUp: canSetUp)
        } else {
            wifi = nil
        }
    }
}

struct ControllerWiFiNetwork: Identifiable, Equatable, Sendable {
    let ssid: String
    let strength: Int
    var id: String { ssid }

    /// Password-protected networks only: the Pi joins WPA2/WPA3 personal networks.
    static func list(_ body: [String: WireValue]) -> [ControllerWiFiNetwork] {
        guard case .array(let items)? = body["networks"] else { return [] }
        return items.compactMap { item in
            guard case .object(let fields) = item, let ssid = fields["ssid"]?.string, !ssid.isEmpty,
                  case .number(let strength)? = fields["strength"], fields["secured"] == .bool(true) else { return nil }
            return ControllerWiFiNetwork(ssid: ssid, strength: Int(strength))
        }
    }
}

struct DiscoveredController: Identifiable, Equatable {
    let id: UUID
    let name: String
    let rssi: Int
}
enum ProvisioningStage: String { case idle, scanning, connecting, needsKey, paired, configuringWiFi, ready }

/// Core Bluetooth is deliberately initialized only after the user chooses Scan.
@MainActor final class ProvisioningManager: NSObject, ObservableObject, BluetoothControllerTransport, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    static let serviceUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100001")
    private static let infoUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100002")
    private static let writeUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100003")
    private static let notifyUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100004")
    @Published private(set) var devices: [DiscoveredController] = []
    @Published private(set) var stage: ProvisioningStage = .idle
    @Published var errorMessage: String?
    @Published private(set) var statusMessage = "Connect your controller to your home network, or scan nearby using Bluetooth."
    @Published private(set) var connectedDeviceName: String?
    @Published private(set) var savedController: PairedController?
    @Published private(set) var pendingController: PairedController?
    @Published private(set) var isWorking = false
    @Published private(set) var controllerStatus: ControllerStatus?
    @Published private(set) var wifiNetworks: [ControllerWiFiNetwork] = []
    @Published private(set) var statusWorking = false
    @Published private(set) var statusError: String?
    @Published private(set) var wifiNotice: String?
    var controllerForSetup: PairedController? { pendingController ?? savedController }
    /// Status and Wi-Fi setup use the saved, encrypted Bluetooth pairing and never move it to the LAN.
    private var bluetoothController: PairedController? {
        savedController.flatMap { $0.bluetoothPeripheralID == nil ? nil : $0 }
    }
    private var generation: UInt64 = 0
    private let vault: any ControllerCredentialStore
    private let verifyController: @Sendable (PairedController) async throws -> Void
    private let pairController: @Sendable (String, String) async throws -> PairedController
    private var central: CBCentralManager?
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var peripheral: CBPeripheral?
    private var infoCharacteristic: CBCharacteristic?
    private var writeCharacteristic: CBCharacteristic?
    private var notifyCharacteristic: CBCharacteristic?
    private var info: ControllerInfo?
    private var codec: ControllerWireCodec?
    private var pending: CheckedContinuation<ControllerWireResponse, Error>?
    private var pendingID: String?
    private var pendingResponse: Result<ControllerWireResponse, Error>?
    private var writeInFlight = false
    private var chunks: [Data] = []
    private var receiveBuffer = ControllerFrameBuffer()
    private var timeoutTask: Task<Void, Never>?
    private var wantsScan = false
    private var reconnectCandidate: PairedController?
    private var connectionWaiter: CheckedContinuation<Void, Error>?
    private var challengeWaiter: CheckedContinuation<ControllerInfo, Error>?
    private var connectionAttemptID: UUID?
    private var challengeAttemptID: UUID?
    private var exchangeInFlight = false
    private var exchangeQueue: [CheckedContinuation<Void, Never>] = []

    init(vault: any ControllerCredentialStore = ControllerVault.shared,
         verifyController: (@Sendable (PairedController) async throws -> Void)? = nil,
         pairController: (@Sendable (String, String) async throws -> PairedController)? = nil) {
        self.vault = vault
        self.verifyController = verifyController ?? { paired in try await LocalControllerClient(vault: vault).ping(paired) }
        self.pairController = pairController ?? { host, key in try await LocalControllerClient(vault: vault).pair(host: host, setupKey: key) }
        super.init()
        do { savedController = try vault.load() } catch { errorMessage = error.localizedDescription }
    }
    /// Record the owner's setup intent before Core Bluetooth can call back.
    /// Runtime polling must not replace this intent with an old saved peripheral.
    func prepareForBluetoothSetup() {
        cancel(); errorMessage = nil; devices = []; peripherals = [:]; wantsScan = true
        stage = .scanning
        statusMessage = "Preparing to find your nearby controller…"
    }
    func startScan() {
        prepareForBluetoothSetup()
        if central == nil { central = CBCentralManager(delegate: self, queue: .main) }
        else if central?.state == .poweredOn { beginScan() }
        else { explainBluetoothState() }
    }
    private func beginScan() {
        guard wantsScan else { return }
        stage = .scanning; statusMessage = "Looking for nearby SPARC controllers…"
        central?.scanForPeripherals(withServices: [Self.serviceUUID], options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        timeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 15_000_000_000)
            guard !Task.isCancelled, let self, self.stage == .scanning else { return }
            self.central?.stopScan(); self.stage = .idle; self.wantsScan = false
            self.statusMessage = self.devices.isEmpty ? "No controller found. Bring it nearby and open its setup window, then scan again." : "Choose your controller below."
        }
    }
    func connect(_ device: DiscoveredController) {
        guard let candidate = peripherals[device.id], central?.state == .poweredOn else { return }
        generation &+= 1; failConnectionWaiters(ControllerConnectionError.cancelled); failPending(ControllerConnectionError.cancelled); pendingController = nil; isWorking = false
        timeoutTask?.cancel(); central?.stopScan(); wantsScan = false; errorMessage = nil
        if let old = peripheral, old.identifier != candidate.identifier { central?.cancelPeripheralConnection(old) }
        peripheral = candidate; candidate.delegate = self; connectedDeviceName = device.name
        info = nil; codec = nil; infoCharacteristic = nil; writeCharacteristic = nil; notifyCharacteristic = nil
        stage = .connecting; statusMessage = "Connecting to \(device.name)…"
        central?.connect(candidate)
        timeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 15_000_000_000)
            guard !Task.isCancelled, let self, self.stage == .connecting else { return }
            self.connectionFailed(ControllerConnectionError.timeout)
        }
    }
    func pair(setupKey: String) async {
        guard stage == .needsKey, let info, !isWorking else { return }
        let attempt = generation; isWorking = true; errorMessage = nil
        defer { if generation == attempt { isWorking = false } }
        let entered = setupKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let setupKey = ControllerPairingCode.setupKey(for: entered) ?? entered
        do {
            codec = try ControllerWireCodec(setupKey: setupKey, info: info)
            let reply = try await exchange(operation: "pair")
            guard generation == attempt else { return }
            guard reply.body?["deviceId"]?.string == info.deviceId else { throw ControllerConnectionError.invalidMessage }
            let host = reply.body?["host"]?.string ?? "sparc-\(info.deviceId.prefix(8)).local"
            guard let peripheral else { throw ControllerConnectionError.cancelled }
            let paired = PairedController(deviceID: info.deviceId, deviceName: info.name, host: host,
                                          setupKey: setupKey.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
                                          bluetoothPeripheralID: peripheral.identifier.uuidString)
            // An authenticated BLE ping proves the transport that will actually carry controls.
            // No LAN request or untrusted advertised host is needed for Bluetooth-only use.
            let proof = try await exchange(operation: "ping")
            guard proof.body?["deviceId"]?.string == paired.deviceID,
                  proof.body?["connected"] == .bool(true) else { throw ControllerConnectionError.invalidMessage }
            try Task.checkCancellation()
            guard generation == attempt else { return }
            try saveVerified(paired)
        } catch { guard generation == attempt else { return }; if Task.isCancelled || error is CancellationError { cancel(); return }; pendingController = nil; codec = nil; errorMessage = error.localizedDescription; stage = peripheral == nil ? .idle : .needsKey }
    }
    func refreshStatus() async {
        guard let paired = bluetoothController, !statusWorking else { return }
        statusWorking = true
        defer { statusWorking = false }
        do {
            let body = try await routedExchange(controller: paired, operation: "status", body: [:], timeout: 15)
            guard let status = ControllerStatus(body) else { throw ControllerConnectionError.invalidMessage }
            controllerStatus = status
            statusError = nil
        } catch {
            statusError = error.localizedDescription
        }
    }

    func scanWiFi() async {
        guard let paired = bluetoothController, !statusWorking else { return }
        statusWorking = true
        defer { statusWorking = false }
        wifiNotice = nil
        do {
            let body = try await routedExchange(controller: paired, operation: "wifi.scan", body: [:], timeout: 30)
            wifiNetworks = ControllerWiFiNetwork.list(body)
            if wifiNetworks.isEmpty { wifiNotice = "Your Pi didn't find a password-protected network nearby. Enter the name below." }
        } catch {
            wifiNotice = error.localizedDescription
        }
    }

    /// Sends the network over Bluetooth. If joining fails, the Pi returns to its previous network.
    @discardableResult
    func configureWiFi(ssid: String, password: String) async -> Bool {
        guard let paired = bluetoothController, !statusWorking else { return false }
        do { try ControllerWiFiCredentials.validate(ssid: ssid, password: password) }
        catch { wifiNotice = error.localizedDescription; return false }
        statusWorking = true
        wifiNotice = "Your Pi is joining \(ssid). This can take up to a minute."
        do {
            let body = try await routedExchange(controller: paired, operation: "wifi.provision",
                                                body: ["ssid": .string(ssid), "password": .string(password)], timeout: 75)
            guard body["connected"] == .bool(true) else { throw ControllerConnectionError.invalidMessage }
            wifiNetworks = []
            wifiNotice = "Your Pi joined \(ssid)."
            statusWorking = false
            await refreshStatus()
            return true
        } catch {
            statusWorking = false
            wifiNotice = error.localizedDescription
            return false
        }
    }
    /// First-time LAN setup needs only a local address and the owner's private key.
    /// The transport authenticates pair + ping before this method can persist anything.
    func pairOnLAN(host: String, setupKey: String) async {
        guard !isWorking else { return }
        cancel()
        let attempt = generation; isWorking = true; stage = .connecting
        defer { if generation == attempt { isWorking = false } }
        errorMessage = nil; statusMessage = "Verifying your controller's private setup key over the home network…"
        do {
            _ = try ControllerAddress.url(host: host, port: 8766, path: "/v1/info")
            let paired = try await pairController(host, setupKey)
            try Task.checkCancellation()
            guard generation == attempt else { return }
            try saveVerified(paired)
        } catch {
            guard generation == attempt else { return }
            if Task.isCancelled || error is CancellationError { cancel(); return }
            stage = .idle; errorMessage = error.localizedDescription
            statusMessage = "Use the same home network and open the controller's setup window, then retry."
        }
    }
    func connectOnLAN(host: String) async {
        guard !isWorking, controllerForSetup != nil else { return }
        let attempt = generation; isWorking = true
        defer { if generation == attempt { isWorking = false } }
        errorMessage = nil; statusMessage = "Verifying the controller on your home network…"
        do {
            try await verifyLAN(host: host, attempt: attempt)
        } catch { guard generation == attempt else { return }; if Task.isCancelled || error is CancellationError { cancel(); return }; stage = .paired; errorMessage = error.localizedDescription; statusMessage = "Use the same home network as your controller. You can retry with its private IP address." }
    }
    private func verifyLAN(host: String, attempt: UInt64) async throws {
        guard var paired = controllerForSetup else { throw ControllerConnectionError.notPaired }
        _ = try ControllerAddress.url(host: host, port: paired.port, path: "/v1/info")
        paired.host = host.trimmingCharacters(in: .whitespacesAndNewlines)
        paired.bluetoothPeripheralID = nil
        try await verifyController(paired)
        try Task.checkCancellation()
        guard generation == attempt else { throw ControllerConnectionError.cancelled }
        try saveVerified(paired)
    }
    private func saveVerified(_ paired: PairedController) throws {
        try vault.save(paired); savedController = paired; pendingController = nil
        if paired.bluetoothPeripheralID == nil { stopBluetooth() }
        stage = .ready
        statusMessage = paired.bluetoothPeripheralID == nil
            ? "Connected securely over your home network. Schedules can now be saved to your controller."
            : "Connected securely over Bluetooth. Schedules and controls communicate directly with your nearby controller."
    }

    func reconnectSavedController() async {
        guard let paired = savedController, !wantsScan, (stage == .idle || stage == .ready), !isWorking else { return }
        if paired.bluetoothPeripheralID == nil { await connectOnLAN(host: paired.host); return }
        let attempt = generation
        errorMessage = nil
        do {
            let proof = try await exchangeBLE(controller: paired, operation: "ping", body: [:])
            try Task.checkCancellation()
            guard generation == attempt else { return }
            guard proof["deviceId"]?.string == paired.deviceID, proof["connected"] == .bool(true) else {
                throw ControllerConnectionError.invalidMessage
            }
            stage = .ready
            statusMessage = "Your Bluetooth controller is connected."
        } catch {
            guard generation == attempt else { return }
            if Task.isCancelled || error is CancellationError { cancel(); return }
            errorMessage = error.localizedDescription
            stage = .idle
        }
    }

    /// Used by the routed app client, with the same central manager as pairing.
    /// Every operation obtains a fresh boot challenge and never retries a mutation.
    func exchangeBLE(controller: PairedController, operation: String,
                     body: [String: WireValue]) async throws -> [String: WireValue] {
        try await routedExchange(controller: controller, operation: operation, body: body, timeout: 15)
    }

    /// Joining Wi-Fi can take the Pi up to a minute, so callers choose how long to wait.
    private func routedExchange(controller: PairedController, operation: String,
                                body: [String: WireValue], timeout: UInt64) async throws -> [String: WireValue] {
        // Polling and a tapped control share one radio link: a control waits for
        // the in-flight poll instead of failing as busy. Nothing is retried.
        if exchangeInFlight { await withCheckedContinuation { exchangeQueue.append($0) } }
        else { exchangeInFlight = true }
        defer { if exchangeQueue.isEmpty { exchangeInFlight = false } else { exchangeQueue.removeFirst().resume() } }
        try Task.checkCancellation()
        guard !wantsScan, (stage == .idle || stage == .ready),
              !isWorking, pending == nil, connectionWaiter == nil, challengeWaiter == nil else {
            throw ControllerConnectionError.unavailable("The Bluetooth controller is busy. Wait for the current request to finish.")
        }
        guard let identifier = controller.bluetoothPeripheralID, UUID(uuidString: identifier) != nil else {
            throw ControllerConnectionError.invalidMessage
        }
        let attempt = generation
        isWorking = true
        defer { if generation == attempt { isWorking = false } }
        try await ensureBluetoothConnection(controller)
        try Task.checkCancellation()
        guard generation == attempt else { throw ControllerConnectionError.cancelled }
        let challenge = try await readFreshChallenge()
        guard challenge.deviceId == controller.deviceID else { throw ControllerConnectionError.invalidMessage }
        codec = try ControllerWireCodec(setupKey: controller.setupKey, info: challenge)
        let response = try await exchange(operation: operation, body: body, timeout: timeout)
        try Task.checkCancellation()
        guard generation == attempt else { throw ControllerConnectionError.cancelled }
        return response.body ?? [:]
    }

    private func ensureBluetoothConnection(_ controller: PairedController) async throws {
        guard let identifier = controller.bluetoothPeripheralID.flatMap(UUID.init(uuidString:)) else {
            throw ControllerConnectionError.invalidMessage
        }
        if peripheral?.identifier == identifier, peripheral?.state == .connected,
           infoCharacteristic != nil, writeCharacteristic != nil, notifyCharacteristic?.isNotifying == true {
            return
        }
        stopBluetooth()
        let attemptID = UUID()
        connectionAttemptID = attemptID
        reconnectCandidate = controller
        stage = .connecting
        statusMessage = "Reconnecting to your Bluetooth controller…"
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                connectionWaiter = continuation
                timeoutTask = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 20_000_000_000)
                    guard !Task.isCancelled else { return }
                    self?.connectionFailed(ControllerConnectionError.timeout)
                }
                if central == nil { central = CBCentralManager(delegate: self, queue: .main) }
                else if central?.state == .poweredOn { beginSavedConnection() }
                else if central?.state != .unknown && central?.state != .resetting {
                    connectionFailed(ControllerConnectionError.unavailable("Bluetooth is unavailable. Turn it on and allow SPARC access in iPhone Settings."))
                }
            }
        }, onCancel: { [weak self] in
            Task { @MainActor in
                guard self?.connectionAttemptID == attemptID else { return }
                self?.connectionFailed(ControllerConnectionError.cancelled)
            }
        })
    }

    private func beginSavedConnection() {
        guard let central, central.state == .poweredOn,
              let savedIdentifier = reconnectCandidate?.bluetoothPeripheralID,
              let identifier = UUID(uuidString: savedIdentifier), connectionWaiter != nil else { return }
        if let candidate = central.retrievePeripherals(withIdentifiers: [identifier]).first {
            connectSavedPeripheral(candidate)
        } else {
            // Reconnect only the saved peripheral; unrelated advertisements cannot replace it.
            central.scanForPeripherals(withServices: [Self.serviceUUID], options: nil)
        }
    }
    private func connectSavedPeripheral(_ candidate: CBPeripheral) {
        central?.stopScan()
        peripheral = candidate
        candidate.delegate = self
        connectedDeviceName = reconnectCandidate?.deviceName
        central?.connect(candidate)
    }
    private func readFreshChallenge() async throws -> ControllerInfo {
        guard let peripheral, peripheral.state == .connected, let infoCharacteristic else {
            throw ControllerConnectionError.unavailable("Your Bluetooth controller disconnected. Bring it nearby and reconnect.")
        }
        let attemptID = UUID()
        challengeAttemptID = attemptID
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                challengeWaiter = continuation
                timeoutTask?.cancel()
                timeoutTask = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 15_000_000_000)
                    guard !Task.isCancelled else { return }
                    self?.connectionFailed(ControllerConnectionError.timeout)
                }
                peripheral.readValue(for: infoCharacteristic)
            }
        }, onCancel: { [weak self] in
            Task { @MainActor in
                guard self?.challengeAttemptID == attemptID else { return }
                self?.connectionFailed(ControllerConnectionError.cancelled)
            }
        })
    }

    func cancel() {
        generation &+= 1; pendingController = nil; isWorking = false; errorMessage = nil
        timeoutTask?.cancel(); timeoutTask = nil; wantsScan = false; central?.stopScan()
        failPending(ControllerConnectionError.cancelled)
        stopBluetooth()
        stage = .idle
        statusMessage = savedController == nil ? "Connect your controller to your home network, or scan nearby using Bluetooth." : "Your saved pairing is available. Reconnect nearby over Bluetooth or use your saved home-network connection."
    }
    private func stopBluetooth() {
        timeoutTask?.cancel(); timeoutTask = nil; wantsScan = false; central?.stopScan()
        failConnectionWaiters(ControllerConnectionError.cancelled)
        let previous = peripheral
        peripheral = nil; infoCharacteristic = nil; writeCharacteristic = nil; notifyCharacteristic = nil; info = nil; codec = nil
        connectedDeviceName = nil; receiveBuffer = ControllerFrameBuffer()
        if let previous { central?.cancelPeripheralConnection(previous) }
    }
    func forget() {
        cancel()
        do {
            try vault.clear(); savedController = nil; statusMessage = "Pairing removed from this iPhone."
            controllerStatus = nil; wifiNetworks = []; statusError = nil; wifiNotice = nil
        }
        catch { errorMessage = error.localizedDescription }
    }
    private func exchange(operation: String, body: [String: WireValue] = [:], timeout: UInt64 = 15) async throws -> ControllerWireResponse {
        guard pending == nil, let codec, let info, let peripheral, let characteristic = writeCharacteristic,
              notifyCharacteristic?.isNotifying == true else { throw ControllerConnectionError.unavailable("Reconnect the controller over Bluetooth before continuing setup.") }
        let id = UUID().uuidString.lowercased()
        // Device time from the challenge avoids requiring a first-boot Pi to have network time already.
        let data = try codec.sealRequest(operation: operation, body: body, id: id, timestamp: info.serverTime + ProcessInfo.processInfo.systemUptime - infoReceivedAt)
        let framed = try ControllerFrameBuffer.frame(data)
        // iOS reports 512 for .withResponse and would split larger-than-MTU chunks into
        // prepared writes; one ATT packet per chunk keeps every Pi write at offset 0.
        let mtu = max(20, min(peripheral.maximumWriteValueLength(for: .withoutResponse), 512))
        chunks = stride(from: 0, to: framed.count, by: mtu).map { Data(framed[$0..<min($0+mtu, framed.count)]) }
        receiveBuffer = ControllerFrameBuffer(); pendingID = id
        pendingResponse = nil; writeInFlight = false
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                pending = continuation
                timeoutTask?.cancel()
                timeoutTask = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: timeout * 1_000_000_000)
                    guard !Task.isCancelled else { return }; self?.failPending(ControllerConnectionError.timeout, disconnect: true)
                }
                if let first = chunks.first {
                    chunks.removeFirst(); writeInFlight = true
                    peripheral.writeValue(first, for: characteristic, type: .withResponse)
                }
            }
        }, onCancel: { [weak self] in Task { @MainActor in guard self?.pendingID == id else { return }; self?.failPending(ControllerConnectionError.cancelled, disconnect: true) } })
    }
    private var infoReceivedAt: Double = 0
    private func failPending(_ error: Error, disconnect: Bool = false) {
        timeoutTask?.cancel(); timeoutTask = nil; chunks = []; pendingID = nil
        pendingResponse = nil; writeInFlight = false
        let continuation = pending; pending = nil; continuation?.resume(throwing: error)
        if disconnect, let peripheral { central?.cancelPeripheralConnection(peripheral); self.peripheral = nil; connectedDeviceName = nil; stage = .idle }
    }
    private func failConnectionWaiters(_ error: Error) {
        reconnectCandidate = nil; connectionAttemptID = nil; challengeAttemptID = nil
        let connection = connectionWaiter; connectionWaiter = nil
        let challenge = challengeWaiter; challengeWaiter = nil
        connection?.resume(throwing: error)
        challenge?.resume(throwing: error)
    }
    private func connectionFailed(_ error: Error) {
        failConnectionWaiters(error)
        failPending(error); errorMessage = error.localizedDescription; stage = .idle
        if let peripheral { central?.cancelPeripheralConnection(peripheral) }
        peripheral = nil; connectedDeviceName = nil
    }
    private func explainBluetoothState() {
        switch central?.state {
        case .poweredOff: errorMessage = "Turn on Bluetooth in Settings, then scan again."
        case .unauthorized: errorMessage = "Allow Bluetooth access for SPARC in iPhone Settings."
        case .unsupported: errorMessage = "Bluetooth is unavailable. Use the controller’s local address and private setup key."
        case .resetting: statusMessage = "Bluetooth is restarting. Try scanning again in a moment."
        default: statusMessage = "Waiting for Bluetooth…"
        }
        stage = .idle
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        // A new central begins as unknown; wait for its powered-on callback.
        if central.state == .unknown || central.state == .resetting {
            if wantsScan || connectionWaiter != nil { statusMessage = "Waiting for Bluetooth…" }
            return
        }
        if central.state == .poweredOn {
            if connectionWaiter != nil { beginSavedConnection() }
            else if wantsScan { beginScan() }
        }
        else {
            // An old central may change state while LAN pairing is running.
            guard wantsScan || peripheral != nil || pending != nil || connectionWaiter != nil else { return }
            central.stopScan()
            failConnectionWaiters(ControllerConnectionError.unavailable("Bluetooth is unavailable."))
            failPending(ControllerConnectionError.unavailable("Bluetooth is unavailable."))
            peripheral = nil; connectedDeviceName = nil; explainBluetoothState()
        }
    }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        if connectionWaiter != nil,
           peripheral.identifier.uuidString.caseInsensitiveCompare(reconnectCandidate?.bluetoothPeripheralID ?? "") == .orderedSame {
            connectSavedPeripheral(peripheral)
            return
        }
        guard stage == .scanning, wantsScan else { return }
        peripherals[peripheral.identifier] = peripheral
        let device = DiscoveredController(id: peripheral.identifier, name: advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name ?? "SPARC controller", rssi: RSSI.intValue)
        if let index = devices.firstIndex(where: { $0.id == device.id }) { devices[index] = device } else { devices.append(device) }
        devices.sort { $0.rssi > $1.rssi }
    }
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) { guard self.peripheral?.identifier == peripheral.identifier else { return }; peripheral.discoverServices([Self.serviceUUID]) }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) { guard self.peripheral?.identifier == peripheral.identifier else { return }; connectionFailed(error ?? ControllerConnectionError.unavailable("Could not connect. Keep your controller nearby and retry.")) }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        self.peripheral = nil; connectedDeviceName = nil
        let failure = ControllerConnectionError.unavailable("Bluetooth disconnected. Bring your controller nearby and reconnect.")
        failConnectionWaiters(failure)
        failPending(failure)
        if stage != .ready { stage = .idle; statusMessage = "Controller disconnected. Scan to reconnect." }
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil, let service = peripheral.services?.first(where: { $0.uuid == Self.serviceUUID }) else { connectionFailed(error ?? ControllerConnectionError.invalidMessage); return }
        peripheral.discoverCharacteristics([Self.infoUUID, Self.writeUUID, Self.notifyUUID], for: service)
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil, let characteristics = service.characteristics,
              let info = characteristics.first(where: { $0.uuid == Self.infoUUID }), info.properties.contains(.read),
              let write = characteristics.first(where: { $0.uuid == Self.writeUUID }), write.properties.contains(.write),
              let notify = characteristics.first(where: { $0.uuid == Self.notifyUUID }), notify.properties.contains(.notify) else {
            connectionFailed(error ?? ControllerConnectionError.invalidMessage); return
        }
        infoCharacteristic = info; writeCharacteristic = write; notifyCharacteristic = notify
        peripheral.readValue(for: info); peripheral.setNotifyValue(true, for: notify)
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil, characteristic.isNotifying else { connectionFailed(error ?? ControllerConnectionError.invalidMessage); return }
        finishDiscovery()
    }
    private func finishDiscovery() {
        guard info != nil, notifyCharacteristic?.isNotifying == true else { return }
        if stage == .ready, codec != nil, reconnectCandidate == nil { return }
        timeoutTask?.cancel(); timeoutTask = nil
        if let candidate = reconnectCandidate {
            do {
                guard info?.deviceId == candidate.deviceID, let info else { throw ControllerConnectionError.invalidMessage }
                codec = try ControllerWireCodec(setupKey: candidate.setupKey, info: info)
                reconnectCandidate = nil; connectionAttemptID = nil
                stage = .ready
                statusMessage = "Your Bluetooth controller is connected."
                let waiter = connectionWaiter; connectionWaiter = nil
                waiter?.resume()
            } catch { connectionFailed(error) }
        } else {
            stage = .needsKey
            if info?.capabilities.contains("pairing-code") == true {
                // Connect without typing; the code field appears only if this fails.
                statusMessage = "Connecting to your controller…"
                Task { await pair(setupKey: ControllerPairingCode.default) }
            } else {
                statusMessage = "Enter the setup key from this controller’s label or setup terminal."
            }
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil, let value = characteristic.value else { connectionFailed(error ?? ControllerConnectionError.invalidMessage); return }
        if characteristic.uuid == Self.infoUUID {
            do {
                let decoded = try JSONDecoder().decode(ControllerInfo.self, from: value)
                try decoded.validate()
                info = decoded; infoReceivedAt = ProcessInfo.processInfo.systemUptime
                if let waiter = challengeWaiter {
                    challengeWaiter = nil; challengeAttemptID = nil; timeoutTask?.cancel(); timeoutTask = nil
                    waiter.resume(returning: decoded)
                } else { finishDiscovery() }
            } catch { connectionFailed(error) }
        } else if characteristic.uuid == Self.notifyUUID, pending != nil, let codec, let pendingID {
            do {
                guard let payload = try receiveBuffer.append(value) else { return }
                let response = try codec.openResponse(payload, expectedID: pendingID)
                pendingResponse = response.ok ? .success(response)
                    : .failure(ControllerConnectionError.unavailable(response.error?.message ?? "The controller could not complete this request."))
                finishPendingResponse()
            } catch { failPending(error, disconnect: true) }
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil else { failPending(error!, disconnect: true); return }
        writeInFlight = false
        guard pending != nil else { return }
        if !chunks.isEmpty {
            let next = chunks.removeFirst(); writeInFlight = true
            peripheral.writeValue(next, for: characteristic, type: .withResponse)
        } else { finishPendingResponse() }
    }
    private func finishPendingResponse() {
        // A notification can arrive before the final write acknowledgment. Do not
        // allow that late acknowledgment to consume a chunk from the next request.
        guard !writeInFlight, chunks.isEmpty, let response = pendingResponse, let continuation = pending else { return }
        pendingResponse = nil; pending = nil; pendingID = nil
        timeoutTask?.cancel(); timeoutTask = nil
        continuation.resume(with: response)
    }
}
