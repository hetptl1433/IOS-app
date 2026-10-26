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

struct DiscoveredController: Identifiable, Equatable {
    let id: UUID
    let name: String
    let rssi: Int
}
enum ProvisioningStage: String { case idle, scanning, connecting, needsKey, paired, configuringWiFi, ready }

/// Core Bluetooth is deliberately initialized only after the user chooses Scan.
@MainActor final class ProvisioningManager: NSObject, ObservableObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    static let serviceUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100001")
    private static let infoUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100002")
    private static let writeUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100003")
    private static let notifyUUID = CBUUID(string: "7C49B5A0-8D22-4FB8-A3A8-6D7703100004")
    @Published private(set) var devices: [DiscoveredController] = []
    @Published private(set) var stage: ProvisioningStage = .idle
    @Published var errorMessage: String?
    @Published private(set) var statusMessage = "Power on your Raspberry Pi 4, then scan nearby."
    @Published private(set) var connectedDeviceName: String?
    @Published private(set) var savedController: PairedController?
    @Published private(set) var pendingController: PairedController?
    @Published private(set) var isWorking = false
    var controllerForSetup: PairedController? { pendingController ?? savedController }
    private var generation: UInt64 = 0
    private let vault: any ControllerCredentialStore
    private let verifyController: @Sendable (PairedController) async throws -> Void
    private var central: CBCentralManager?
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var peripheral: CBPeripheral?
    private var writeCharacteristic: CBCharacteristic?
    private var notifyCharacteristic: CBCharacteristic?
    private var info: ControllerInfo?
    private var codec: ControllerWireCodec?
    private var pending: CheckedContinuation<ControllerWireResponse, Error>?
    private var pendingID: String?
    private var chunks: [Data] = []
    private var receiveBuffer = ControllerFrameBuffer()
    private var timeoutTask: Task<Void, Never>?
    private var wantsScan = false

    init(vault: any ControllerCredentialStore = ControllerVault.shared,
         verifyController: (@Sendable (PairedController) async throws -> Void)? = nil) {
        self.vault = vault
        self.verifyController = verifyController ?? { paired in try await LocalControllerClient(vault: vault).ping(paired) }
        super.init()
        do { savedController = try vault.load() } catch { errorMessage = error.localizedDescription }
    }
    func startScan() {
        cancel(); errorMessage = nil; devices = []; peripherals = [:]; wantsScan = true
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
        generation &+= 1; failPending(ControllerConnectionError.cancelled); pendingController = nil; isWorking = false
        timeoutTask?.cancel(); central?.stopScan(); wantsScan = false; errorMessage = nil
        if let old = peripheral, old.identifier != candidate.identifier { central?.cancelPeripheralConnection(old) }
        peripheral = candidate; candidate.delegate = self; connectedDeviceName = device.name
        info = nil; codec = nil; writeCharacteristic = nil; notifyCharacteristic = nil
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
        do {
            codec = try ControllerWireCodec(setupKey: setupKey.trimmingCharacters(in: .whitespacesAndNewlines), info: info)
            let reply = try await exchange(operation: "pair")
            guard generation == attempt else { return }
            let host = reply.body?["host"]?.string ?? "sparc-\(info.deviceId.prefix(8)).local"
            let paired = PairedController(deviceID: info.deviceId, deviceName: info.name, host: host,
                                          setupKey: setupKey.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
            pendingController = paired
            stage = .paired; statusMessage = "Controller identity verified. Connect it to your home Wi-Fi."
        } catch { guard generation == attempt else { return }; if Task.isCancelled || error is CancellationError { cancel(); return }; codec = nil; errorMessage = error.localizedDescription; stage = peripheral == nil ? .idle : .needsKey }
    }
    func configureWiFi(ssid: String, password: String) async {
        guard (stage == .paired || stage == .ready), !isWorking else { return }
        do { try ControllerWiFiCredentials.validate(ssid: ssid, password: password) }
        catch { errorMessage = error.localizedDescription; return }
        let attempt = generation; isWorking = true
        defer { if generation == attempt { isWorking = false } }
        errorMessage = nil; stage = .configuringWiFi; statusMessage = "Securely connecting your controller to Wi-Fi…"
        do {
            let response = try await exchange(operation: "wifi.provision", body: ["ssid": .string(ssid), "password": .string(password)], timeout: 75)
            guard generation == attempt else { return }
            guard let host = response.body?["host"]?.string else { throw ControllerConnectionError.invalidMessage }
            try await verifyLAN(host: host, attempt: attempt)
        } catch { guard generation == attempt else { return }; if Task.isCancelled || error is CancellationError { cancel(); return }; stage = peripheral == nil ? .idle : .paired; errorMessage = error.localizedDescription; statusMessage = "Wi-Fi setup did not finish. Check your network and retry." }
    }
    func connectOnLAN(host: String) async {
        guard !isWorking, controllerForSetup != nil else { return }
        let attempt = generation; isWorking = true
        defer { if generation == attempt { isWorking = false } }
        errorMessage = nil; statusMessage = "Verifying the controller on your home network…"
        do {
            try await verifyLAN(host: host, attempt: attempt)
        } catch { guard generation == attempt else { return }; if Task.isCancelled || error is CancellationError { cancel(); return }; stage = .paired; errorMessage = error.localizedDescription; statusMessage = "Use the same home Wi-Fi as your Pi. You can retry with its private IP address." }
    }
    private func verifyLAN(host: String, attempt: UInt64) async throws {
        guard var paired = controllerForSetup else { throw ControllerConnectionError.notPaired }
        _ = try ControllerAddress.url(host: host, port: paired.port, path: "/v1/info")
        paired.host = host.trimmingCharacters(in: .whitespacesAndNewlines)
        try await verifyController(paired)
        try Task.checkCancellation()
        guard generation == attempt else { throw ControllerConnectionError.cancelled }
        try vault.save(paired); savedController = paired; pendingController = nil
        stopBluetooth()
        stage = .ready
        statusMessage = "Connected securely over your home network. Vehicle control becomes available when its Pi adapter is installed."
    }
    func cancel() {
        generation &+= 1; pendingController = nil; isWorking = false; errorMessage = nil
        timeoutTask?.cancel(); timeoutTask = nil; wantsScan = false; central?.stopScan()
        failPending(ControllerConnectionError.cancelled)
        stopBluetooth()
        stage = .idle
        statusMessage = savedController == nil ? "Power on your Raspberry Pi 4, then scan nearby." : "Your saved pairing is available. Connect over Wi-Fi or scan to set up a controller."
    }
    private func stopBluetooth() {
        timeoutTask?.cancel(); timeoutTask = nil; wantsScan = false; central?.stopScan()
        let previous = peripheral
        peripheral = nil; writeCharacteristic = nil; notifyCharacteristic = nil; info = nil; codec = nil
        connectedDeviceName = nil; receiveBuffer = ControllerFrameBuffer()
        if let previous { central?.cancelPeripheralConnection(previous) }
    }
    func forget() {
        cancel()
        do { try vault.clear(); savedController = nil; statusMessage = "Pairing removed from this iPhone." }
        catch { errorMessage = error.localizedDescription }
    }
    private func exchange(operation: String, body: [String: WireValue] = [:], timeout: UInt64 = 15) async throws -> ControllerWireResponse {
        guard pending == nil, let codec, let info, let peripheral, let characteristic = writeCharacteristic,
              notifyCharacteristic?.isNotifying == true else { throw ControllerConnectionError.unavailable("Reconnect the controller over Bluetooth before continuing setup.") }
        let id = UUID().uuidString.lowercased()
        // Device time from the challenge avoids requiring a first-boot Pi to have network time already.
        let data = try codec.sealRequest(operation: operation, body: body, id: id, timestamp: info.serverTime + ProcessInfo.processInfo.systemUptime - infoReceivedAt)
        let framed = try ControllerFrameBuffer.frame(data)
        let mtu = max(1, min(peripheral.maximumWriteValueLength(for: .withResponse), 512))
        chunks = stride(from: 0, to: framed.count, by: mtu).map { Data(framed[$0..<min($0+mtu, framed.count)]) }
        receiveBuffer = ControllerFrameBuffer(); pendingID = id
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                pending = continuation
                timeoutTask?.cancel()
                timeoutTask = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: timeout * 1_000_000_000)
                    guard !Task.isCancelled else { return }; self?.failPending(ControllerConnectionError.timeout, disconnect: true)
                }
                if let first = chunks.first { chunks.removeFirst(); peripheral.writeValue(first, for: characteristic, type: .withResponse) }
            }
        }, onCancel: { [weak self] in Task { @MainActor in guard self?.pendingID == id else { return }; self?.failPending(ControllerConnectionError.cancelled, disconnect: true) } })
    }
    private var infoReceivedAt: Double = 0
    private func failPending(_ error: Error, disconnect: Bool = false) {
        timeoutTask?.cancel(); timeoutTask = nil; chunks = []; pendingID = nil
        let continuation = pending; pending = nil; continuation?.resume(throwing: error)
        if disconnect, let peripheral { central?.cancelPeripheralConnection(peripheral); self.peripheral = nil; connectedDeviceName = nil; stage = .idle }
    }
    private func connectionFailed(_ error: Error) {
        failPending(error); errorMessage = error.localizedDescription; stage = .idle
        if let peripheral { central?.cancelPeripheralConnection(peripheral) }
        peripheral = nil; connectedDeviceName = nil
    }
    private func explainBluetoothState() {
        switch central?.state {
        case .poweredOff: errorMessage = "Turn on Bluetooth in Settings, then scan again."
        case .unauthorized: errorMessage = "Allow Bluetooth access for SPARC in iPhone Settings."
        case .unsupported: errorMessage = "Bluetooth setup requires a physical iPhone. The simulator can use demo mode."
        case .resetting: statusMessage = "Bluetooth is restarting. Try scanning again in a moment."
        default: statusMessage = "Waiting for Bluetooth…"
        }
        stage = .idle
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn { if wantsScan { beginScan() } }
        else { central.stopScan(); failPending(ControllerConnectionError.unavailable("Bluetooth is unavailable.")); peripheral = nil; connectedDeviceName = nil; explainBluetoothState() }
    }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
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
        self.peripheral = nil; connectedDeviceName = nil; failPending(ControllerConnectionError.unavailable("Bluetooth disconnected. Reconnect to continue setup."))
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
              let info = characteristics.first(where: { $0.uuid == Self.infoUUID }),
              let write = characteristics.first(where: { $0.uuid == Self.writeUUID }), write.properties.contains(.write),
              let notify = characteristics.first(where: { $0.uuid == Self.notifyUUID }), notify.properties.contains(.notify) else {
            connectionFailed(error ?? ControllerConnectionError.invalidMessage); return
        }
        writeCharacteristic = write; notifyCharacteristic = notify
        peripheral.readValue(for: info); peripheral.setNotifyValue(true, for: notify)
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil, characteristic.isNotifying else { connectionFailed(error ?? ControllerConnectionError.invalidMessage); return }
        finishDiscovery()
    }
    private func finishDiscovery() {
        guard info != nil, notifyCharacteristic?.isNotifying == true else { return }
        timeoutTask?.cancel(); timeoutTask = nil; stage = .needsKey
        statusMessage = "Enter the setup key from this controller’s label or setup terminal."
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil, let value = characteristic.value else { connectionFailed(error ?? ControllerConnectionError.invalidMessage); return }
        if characteristic.uuid == Self.infoUUID {
            do { let decoded = try JSONDecoder().decode(ControllerInfo.self, from: value); try decoded.validate(); info = decoded; infoReceivedAt = ProcessInfo.processInfo.systemUptime; finishDiscovery() }
            catch { connectionFailed(error) }
        } else if characteristic.uuid == Self.notifyUUID, pending != nil, let codec, let pendingID {
            do {
                guard let payload = try receiveBuffer.append(value) else { return }
                let response = try codec.openResponse(payload, expectedID: pendingID)
                guard response.ok else { throw ControllerConnectionError.unavailable(response.error?.message ?? "The controller could not complete setup.") }
                timeoutTask?.cancel(); timeoutTask = nil; self.pendingID = nil; chunks = []
                let continuation = pending; pending = nil; continuation?.resume(returning: response)
            } catch { failPending(error) }
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral?.identifier == peripheral.identifier else { return }
        guard error == nil else { failPending(error!); return }
        if !chunks.isEmpty { let next = chunks.removeFirst(); peripheral.writeValue(next, for: characteristic, type: .withResponse) }
    }
}
