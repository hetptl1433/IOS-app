import Foundation

protocol ControllerClient: Sendable {
    func fetchSnapshot() async throws -> ChargeSnapshot
    func fetchSettings() async throws -> ControllerSettings
    func fetchRates(day: String) async throws -> ControllerRatePage
    func fetchCombinedSavings(kind: String) async throws -> CombinedSavingsReport
    func saveSleepWindows(_ value: SleepWindowUpdate) async throws
    func saveWeekly(_ value: WeeklySchedule) async throws
    func saveToday(_ value: TodayOverride?) async throws
    func command(_ command: String) async throws
    func setVirtualCarEnabled(_ enabled: Bool) async throws
    func fetchHistory(cursor: Int?) async throws -> ControllerHistoryPage
    /// One Tesla's own charging history. The virtual car's sessions never appear in it.
    func fetchTeslaHistory(vehicleId: String, cursor: Int?) async throws -> ControllerHistoryPage
    func saveCampaign(_ value: CampaignUpdate) async throws
    func setVirtualCarState(batteryPercent: Double, pluggedIn: Bool) async throws
    func saveConnectionSettings(_ value: ConnectionSettingsUpdate) async throws
    func checkSmartPlug(password: String?) async throws
    func setSmartPlugPower(on: Bool) async throws
    func authorizeTesla() async throws -> TeslaAuthorization
    func completeTeslaAuthorization(code: String, state: String) async throws
    func refreshTesla() async throws
    func selectTeslaVehicle(_ vehicleId: String) async throws
    /// Removes one Tesla account, or every account when `accountId` is nil.
    func disconnectTesla(accountId: String?) async throws
    func activateConnection(_ mode: ControllerConnectionMode) async throws
    func teslaCommand(_ command: TeslaChargeCommand, value: Int?) async throws
}

/// A Pi that holds one Tesla account rejects extra fields, so "remove everything" sends an empty body.
enum TeslaAccountBody {
    static func disconnect(_ accountId: String?) -> [String: WireValue] {
        accountId.map { ["accountId": .string($0)] } ?? [:]
    }
}

extension ControllerClient {
    func saveSleepWindows(_ value: SleepWindowUpdate) async throws { throw ControllerConnectionError.unavailable("Update the Pi for Apple Calendar schedules.") }
    func fetchCombinedSavings(kind: String) async throws -> CombinedSavingsReport {
        throw ControllerConnectionError.unavailable("Update the Pi for combined savings.")
    }
    func fetchRates(day: String) async throws -> ControllerRatePage {
        throw ControllerConnectionError.unavailable("Update the Pi's SPARC software to see hourly rate history.")
    }
    func activateConnection(_ mode: ControllerConnectionMode) async throws {
        throw ControllerConnectionError.unavailable("Update the controller to select the active charging provider.")
    }
    func teslaCommand(_ command: TeslaChargeCommand, value: Int?) async throws {
        throw ControllerConnectionError.unavailable("Update the controller to send Tesla charging commands.")
    }
    func setSmartPlugPower(on: Bool) async throws { throw ControllerConnectionError.unavailable("Update the controller to control a smart plug.") }
    func saveConnectionSettings(_ value: ConnectionSettingsUpdate) async throws {
        throw ControllerConnectionError.unavailable("Update the controller to configure its connections.")
    }
    func checkSmartPlug(password: String?) async throws {
        throw ControllerConnectionError.unavailable("Update the controller to check a smart plug.")
    }
    func authorizeTesla() async throws -> TeslaAuthorization {
        throw ControllerConnectionError.unavailable("Tesla authorization requires private configuration on the Pi.")
    }
    func completeTeslaAuthorization(code: String, state: String) async throws { throw ControllerConnectionError.unavailable("Update the controller to finish Tesla authorization.") }
    func refreshTesla() async throws { throw ControllerConnectionError.unavailable("Tesla access is not available on this controller.") }
    func selectTeslaVehicle(_ vehicleId: String) async throws { throw ControllerConnectionError.unavailable("Tesla access is not available on this controller.") }
    func disconnectTesla(accountId: String?) async throws { throw ControllerConnectionError.unavailable("Tesla access is not available on this controller.") }
    func setVirtualCarState(batteryPercent: Double, pluggedIn: Bool) async throws {
        throw ControllerConnectionError.unavailable("Update the controller to edit its simulated car state.")
    }
    func fetchHistory(cursor: Int?) async throws -> ControllerHistoryPage {
        throw ControllerConnectionError.unavailable("This controller has not provided charging history yet.")
    }
    func fetchTeslaHistory(vehicleId: String, cursor: Int?) async throws -> ControllerHistoryPage {
        throw ControllerConnectionError.unavailable(Self.teslaHistoryUpdateMessage)
    }
    static var teslaHistoryUpdateMessage: String { "Update the Pi's SPARC software to record your Tesla's charges." }
    /// The `get_history` body for one car. Omitting `car` keeps older controllers on the virtual car.
    static func historyBody(limit: Int, cursor: Int?, teslaVehicleId: String? = nil) -> [String: WireValue] {
        var body: [String: WireValue] = ["limit": .number(Double(limit))]
        if let cursor { body["cursor"] = .number(Double(cursor)) }
        if let teslaVehicleId { body["car"] = .string("tesla"); body["vehicleId"] = .string(teslaVehicleId) }
        return body
    }
    func saveCampaign(_ value: CampaignUpdate) async throws {
        throw ControllerConnectionError.unavailable("This controller does not support the finite nightly schedule yet.")
    }
    func setVirtualCarEnabled(_ enabled: Bool) async throws {
        throw ControllerConnectionError.unavailable("This controller does not support Virtual car. Update the app and controller together.")
    }
}

enum ControllerAddress {
    static func url(host: String, port: Int, path: String) throws -> URL {
        let name = host.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let pieces = name.split(separator: ".", omittingEmptySubsequences: false)
        let octets = pieces.compactMap { Int($0) }
        let privateIPv4 = pieces.count == 4 && octets.count == 4 && pieces.allSatisfy { !$0.isEmpty && ($0.count == 1 || !$0.hasPrefix("0")) && $0.utf8.allSatisfy { (48...57).contains($0) } }
            && octets.allSatisfy { (0...255).contains($0) }
            && (octets[0] == 10 || octets[0] == 127 || (octets[0] == 192 && octets[1] == 168)
                || (octets[0] == 172 && (16...31).contains(octets[1])) || (octets[0] == 169 && octets[1] == 254))
        let localName = name.hasSuffix(".local") && name.count <= 253
            && name.unicodeScalars.allSatisfy { CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789-.").contains($0) }
            && pieces.allSatisfy { !$0.isEmpty && $0.count <= 63 && !$0.hasPrefix("-") && !$0.hasSuffix("-") }
        guard (privateIPv4 || localName || name == "localhost"), (1...65535).contains(port), path.hasPrefix("/") else {
            throw ControllerConnectionError.invalidAddress
        }
        var c = URLComponents(); c.scheme = "http"; c.host = name; c.port = port; c.path = path
        guard let url = c.url else { throw ControllerConnectionError.invalidAddress }
        return url
    }
}

actor LocalControllerClient: ControllerClient {
    private let vault: any ControllerCredentialStore
    private let session: URLSession
    init(vault: any ControllerCredentialStore = ControllerVault.shared, configuration: URLSessionConfiguration? = nil) {
        self.vault = vault
        let configuration = configuration ?? URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 12
        configuration.timeoutIntervalForResource = 55
        configuration.waitsForConnectivity = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.httpCookieStorage = nil
        session = URLSession(configuration: configuration, delegate: ControllerRedirectBlocker(), delegateQueue: nil)
    }
    func fetchSnapshot() async throws -> ChargeSnapshot { let body = try await exchange(operation: "get_snapshot"); return try decode(body) }
    func fetchSettings() async throws -> ControllerSettings { let body = try await exchange(operation: "get_settings"); return try decode(body) }
    func saveSleepWindows(_ value: SleepWindowUpdate) async throws {
        try value.validate()
        _ = try await exchange(operation: "set_sleep_windows", body: WireValue.dictionary(value))
    }
    func fetchCombinedSavings(kind: String) async throws -> CombinedSavingsReport {
        try decode(try await exchange(operation:"get_savings",body:["kind":.string(kind)]))
    }
    func fetchRates(day: String) async throws -> ControllerRatePage {
        try decode(try await exchange(operation: "get_rates", body: ["day": .string(day)]))
    }
    func fetchHistory(cursor: Int?) async throws -> ControllerHistoryPage {
        var body: [String: WireValue] = ["limit": .number(8)]
        if let cursor { body["cursor"] = .number(Double(cursor)) }
        return try decode(try await exchange(operation: "get_history", body: body))
    }
    func fetchTeslaHistory(vehicleId: String, cursor: Int?) async throws -> ControllerHistoryPage {
        let body = Self.historyBody(limit: 8, cursor: cursor, teslaVehicleId: vehicleId)
        return try decode(try await exchange(operation: "get_history", body: body))
    }
    func saveCampaign(_ value: CampaignUpdate) async throws {
        try value.validate()
        _ = try await exchange(operation: "set_campaign", body: WireValue.dictionary(value))
    }
    func saveWeekly(_ value: WeeklySchedule) async throws { _ = try await exchange(operation: "set_weekly", body: WireValue.dictionary(value)) }
    func saveToday(_ value: TodayOverride?) async throws {
        let body: [String: WireValue] = try value.map { ["today": .object(try WireValue.dictionary($0))] } ?? ["today": .null]
        _ = try await exchange(operation: "set_today", body: body)
    }
    func command(_ command: String) async throws {
        guard ["charge_now", "pause", "resume", "end"].contains(command) else { throw ControllerConnectionError.invalidMessage }
        _ = try await exchange(operation: command)
    }
    func setVirtualCarEnabled(_ enabled: Bool) async throws {
        _ = try await exchange(operation: "set_virtual_car", body: ["enabled": .bool(enabled)])
    }
    func setVirtualCarState(batteryPercent: Double, pluggedIn: Bool) async throws {
        guard batteryPercent.isFinite, (0...100).contains(batteryPercent) else { throw SnapshotError.invalid }
        _ = try await exchange(operation: "set_virtual_car_state", body: ["batteryPercent": .number(batteryPercent), "pluggedIn": .bool(pluggedIn)])
    }
    func saveConnectionSettings(_ value: ConnectionSettingsUpdate) async throws {
        try value.validate()
        _ = try await exchange(operation: "set_connection_settings", body: WireValue.dictionary(value))
    }
    func checkSmartPlug(password: String?) async throws {
        var body: [String: WireValue] = [:]
        if let password, !password.isEmpty { body["password"] = .string(password) }
        _ = try await exchange(operation: "check_smart_plug", body: body)
    }
    func setSmartPlugPower(on: Bool) async throws { _ = try await exchange(operation: "set_smart_plug_power", body: ["on": .bool(on)]) }
    func authorizeTesla() async throws -> TeslaAuthorization {
        let value: TeslaAuthorization = try decode(try await exchange(operation: "tesla_authorize"))
        try value.validate(); return value
    }
    func completeTeslaAuthorization(code: String, state: String) async throws {
        _ = try await exchange(operation: "tesla_complete_authorization", body: ["code": .string(code), "state": .string(state)])
    }
    func refreshTesla() async throws { _ = try await exchange(operation: "tesla_refresh") }
    func selectTeslaVehicle(_ vehicleId: String) async throws {
        _ = try await exchange(operation: "tesla_select_vehicle", body: ["vehicleId": .string(vehicleId)])
    }
    func disconnectTesla(accountId: String?) async throws {
        _ = try await exchange(operation: "tesla_disconnect", body: TeslaAccountBody.disconnect(accountId))
    }
    func activateConnection(_ mode: ControllerConnectionMode) async throws {
        guard mode == .tesla || mode == .virtual else { throw ControllerConnectionError.invalidMessage }
        _ = try await exchange(operation: "activate_connection", body: ["mode": .string(mode.rawValue)])
    }
    func teslaCommand(_ command: TeslaChargeCommand, value: Int?) async throws {
        try command.validate(value: value)
        var body: [String: WireValue] = ["command": .string(command.rawValue)]
        if let value { body["value"] = .number(Double(value)) }
        let receipt: TeslaCommandReceipt = try decode(try await exchange(operation: "tesla_command", body: body))
        try receipt.validate(expected: command)
    }
    /// A public challenge supplies the candidate identity, never proof of trust.
    /// Keep the entered address; a discovery response cannot redirect the key elsewhere.
    func pair(host: String, setupKey: String, port: Int = 8766) async throws -> PairedController {
        let address = try ControllerAddress.url(host: host, port: port, path: "/v1/info")
        let info = try await challenge(at: address)
        let key = setupKey.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let candidate = PairedController(deviceID: info.deviceId, deviceName: info.name,
                                        host: address.host ?? host, port: port, setupKey: key)
        let body = try await exchange(operation: "pair", controller: candidate, info: info)
        guard body["deviceId"]?.string == candidate.deviceID else { throw ControllerConnectionError.invalidMessage }
        try Task.checkCancellation()
        try await ping(candidate)
        try Task.checkCancellation()
        return candidate
    }
    func ping(_ controller: PairedController) async throws {
        let body = try await exchange(operation: "ping", paired: controller)
        guard body["deviceId"]?.string == controller.deviceID, body["connected"] == .bool(true) else {
            throw ControllerConnectionError.invalidMessage
        }
    }
    private func decode<T: Decodable>(_ result: [String: WireValue]) throws -> T {
        return try ControllerJSON.decoder.decode(T.self, from: ControllerJSON.encoder.encode(result))
    }
    private func fetch(_ request: URLRequest) async throws -> Data {
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              response.expectedContentLength <= ControllerFrameBuffer.maximumLength else {
            throw ControllerConnectionError.unavailable("The home controller returned an unavailable response. Try again on your home Wi-Fi.")
        }
        var data = Data()
        for try await byte in bytes {
            guard data.count < ControllerFrameBuffer.maximumLength else { throw ControllerConnectionError.invalidMessage }
            data.append(byte)
        }
        return data
    }
    private func exchange(operation: String, body: [String: WireValue] = [:], paired: PairedController? = nil) async throws -> [String: WireValue] {
        guard let controller = try paired ?? vault.load() else { throw ControllerConnectionError.notPaired }
        guard controller.bluetoothPeripheralID == nil else {
            throw ControllerConnectionError.unavailable("This controller uses Bluetooth. Reconnect it nearby before continuing.")
        }
        let info = try await challenge(at: ControllerAddress.url(host: controller.host, port: controller.port, path: "/v1/info"))
        return try await exchange(operation: operation, body: body, controller: controller, info: info)
    }
    private func challenge(at address: URL) async throws -> ControllerInfo {
        try Task.checkCancellation()
        let data = try await fetch(URLRequest(url: address))
        let info = try JSONDecoder().decode(ControllerInfo.self, from: data)
        try info.validate()
        return info
    }
    private func exchange(operation: String, body: [String: WireValue] = [:], controller: PairedController,
                          info: ControllerInfo) async throws -> [String: WireValue] {
        try Task.checkCancellation()
        guard info.deviceId == controller.deviceID else { throw ControllerConnectionError.invalidMessage }
        let codec = try ControllerWireCodec(setupKey: controller.setupKey, info: info, client: .current)
        let id = UUID().uuidString.lowercased()
        var request = URLRequest(url: try ControllerAddress.url(host: controller.host, port: controller.port, path: "/v1/exchange"))
        if operation.hasPrefix("tesla_") || operation == "activate_connection" { request.timeoutInterval = 45 }
        request.httpMethod = "POST"; request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try codec.sealRequest(operation: operation, body: body, id: id, timestamp: info.serverTime)
        let response = try codec.openResponse(try await fetch(request), expectedID: id)
        guard response.ok else { throw ControllerConnectionError.unavailable(response.error?.message ?? "The controller could not complete this request.") }
        return response.body ?? [:]
    }
}

/// Do not follow an untrusted LAN device's redirect with a controller request attached.
private final class ControllerRedirectBlocker: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}


/// The same authenticated operations can travel over LAN or the owner's saved BLE peripheral.
protocol BluetoothControllerTransport: Sendable {
    func exchangeBLE(controller: PairedController, operation: String,
                     body: [String: WireValue]) async throws -> [String: WireValue]
}

actor ControllerClientRouter: ControllerClient {
    private let vault: any ControllerCredentialStore
    private let bluetooth: any BluetoothControllerTransport
    private let lan: any ControllerClient

    init(vault: any ControllerCredentialStore = ControllerVault.shared,
         bluetooth: any BluetoothControllerTransport,
         lan: (any ControllerClient)? = nil) {
        self.vault = vault
        self.bluetooth = bluetooth
        self.lan = lan ?? LocalControllerClient(vault: vault)
    }

    func fetchSnapshot() async throws -> ChargeSnapshot {
        if let controller = try bluetoothController() {
            let body = try await bluetooth.exchangeBLE(controller: controller, operation: "get_snapshot", body: [:])
            return try decode(body)
        }
        return try await lan.fetchSnapshot()
    }
    func fetchSettings() async throws -> ControllerSettings {
        if let controller = try bluetoothController() {
            let body = try await bluetooth.exchangeBLE(controller: controller, operation: "get_settings", body: [:])
            return try decode(body)
        }
        return try await lan.fetchSettings()
    }
    func saveWeekly(_ value: WeeklySchedule) async throws {
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_weekly", body: WireValue.dictionary(value))
        } else { try await lan.saveWeekly(value) }
    }
    func saveSleepWindows(_ value: SleepWindowUpdate) async throws {
        try value.validate()
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_sleep_windows", body: WireValue.dictionary(value))
        } else { try await lan.saveSleepWindows(value) }
    }
    func fetchCombinedSavings(kind: String) async throws -> CombinedSavingsReport {
        if let controller = try bluetoothController() {
            return try decode(try await bluetooth.exchangeBLE(controller:controller,operation:"get_savings",body:["kind":.string(kind)]))
        }
        return try await lan.fetchCombinedSavings(kind:kind)
    }
    func fetchRates(day: String) async throws -> ControllerRatePage {
        if let controller = try bluetoothController() {
            return try decode(try await bluetooth.exchangeBLE(controller: controller, operation: "get_rates", body: ["day": .string(day)]))
        }
        return try await lan.fetchRates(day: day)
    }
    func fetchHistory(cursor: Int?) async throws -> ControllerHistoryPage {
        if let controller = try bluetoothController() {
            var body: [String: WireValue] = ["limit": .number(4)]
            if let cursor { body["cursor"] = .number(Double(cursor)) }
            return try decode(try await bluetooth.exchangeBLE(controller: controller, operation: "get_history", body: body))
        }
        return try await lan.fetchHistory(cursor: cursor)
    }
    func fetchTeslaHistory(vehicleId: String, cursor: Int?) async throws -> ControllerHistoryPage {
        if let controller = try bluetoothController() {
            let body = Self.historyBody(limit: 4, cursor: cursor, teslaVehicleId: vehicleId)
            return try decode(try await bluetooth.exchangeBLE(controller: controller, operation: "get_history", body: body))
        }
        return try await lan.fetchTeslaHistory(vehicleId: vehicleId, cursor: cursor)
    }
    func saveCampaign(_ value: CampaignUpdate) async throws {
        try value.validate()
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_campaign", body: WireValue.dictionary(value))
        } else { try await lan.saveCampaign(value) }
    }
    func saveToday(_ value: TodayOverride?) async throws {
        if let controller = try bluetoothController() {
            let body: [String: WireValue] = try value.map { ["today": .object(try WireValue.dictionary($0))] } ?? ["today": .null]
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_today", body: body)
        } else { try await lan.saveToday(value) }
    }
    func command(_ command: String) async throws {
        guard ["charge_now", "pause", "resume", "end"].contains(command) else { throw ControllerConnectionError.invalidMessage }
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: command, body: [:])
        } else { try await lan.command(command) }
    }

    func setVirtualCarEnabled(_ enabled: Bool) async throws {
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_virtual_car", body: ["enabled": .bool(enabled)])
        } else { try await lan.setVirtualCarEnabled(enabled) }
    }

    func setVirtualCarState(batteryPercent: Double, pluggedIn: Bool) async throws {
        guard batteryPercent.isFinite, (0...100).contains(batteryPercent) else { throw SnapshotError.invalid }
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_virtual_car_state",
                                                body: ["batteryPercent": .number(batteryPercent), "pluggedIn": .bool(pluggedIn)])
        } else { try await lan.setVirtualCarState(batteryPercent: batteryPercent, pluggedIn: pluggedIn) }
    }

    func saveConnectionSettings(_ value: ConnectionSettingsUpdate) async throws {
        try value.validate()
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_connection_settings", body: WireValue.dictionary(value))
        } else { try await lan.saveConnectionSettings(value) }
    }
    func checkSmartPlug(password: String?) async throws {
        if let controller = try bluetoothController() {
            var body: [String: WireValue] = [:]
            if let password, !password.isEmpty { body["password"] = .string(password) }
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "check_smart_plug", body: body)
        } else { try await lan.checkSmartPlug(password: password) }
    }
    func setSmartPlugPower(on: Bool) async throws {
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "set_smart_plug_power", body: ["on": .bool(on)])
        } else { try await lan.setSmartPlugPower(on: on) }
    }
    func authorizeTesla() async throws -> TeslaAuthorization {
        if let controller = try bluetoothController() {
            let result = try await bluetooth.exchangeBLE(controller: controller, operation: "tesla_authorize", body: [:])
            let value: TeslaAuthorization = try decode(result)
            try value.validate(); return value
        }
        return try await lan.authorizeTesla()
    }
    func completeTeslaAuthorization(code: String, state: String) async throws {
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "tesla_complete_authorization", body: ["code": .string(code), "state": .string(state)])
        } else { try await lan.completeTeslaAuthorization(code: code, state: state) }
    }
    func refreshTesla() async throws {
        if let controller = try bluetoothController() { _ = try await bluetooth.exchangeBLE(controller: controller, operation: "tesla_refresh", body: [:]) }
        else { try await lan.refreshTesla() }
    }
    func selectTeslaVehicle(_ vehicleId: String) async throws {
        if let controller = try bluetoothController() { _ = try await bluetooth.exchangeBLE(controller: controller, operation: "tesla_select_vehicle", body: ["vehicleId": .string(vehicleId)]) }
        else { try await lan.selectTeslaVehicle(vehicleId) }
    }
    func disconnectTesla(accountId: String?) async throws {
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "tesla_disconnect", body: TeslaAccountBody.disconnect(accountId))
        } else { try await lan.disconnectTesla(accountId: accountId) }
    }
    func activateConnection(_ mode: ControllerConnectionMode) async throws {
        guard mode == .tesla || mode == .virtual else { throw ControllerConnectionError.invalidMessage }
        if let controller = try bluetoothController() {
            _ = try await bluetooth.exchangeBLE(controller: controller, operation: "activate_connection", body: ["mode": .string(mode.rawValue)])
        } else { try await lan.activateConnection(mode) }
    }
    func teslaCommand(_ command: TeslaChargeCommand, value: Int?) async throws {
        try command.validate(value: value)
        if let controller = try bluetoothController() {
            var body: [String: WireValue] = ["command": .string(command.rawValue)]
            if let value { body["value"] = .number(Double(value)) }
            let receipt: TeslaCommandReceipt = try decode(try await bluetooth.exchangeBLE(controller: controller, operation: "tesla_command", body: body))
            try receipt.validate(expected: command)
        } else { try await lan.teslaCommand(command, value: value) }
    }

    private func bluetoothController() throws -> PairedController? {
        guard let controller = try vault.load() else { throw ControllerConnectionError.notPaired }
        guard let identifier = controller.bluetoothPeripheralID else { return nil }
        guard UUID(uuidString: identifier) != nil, UUID(uuidString: controller.deviceID) != nil else {
            throw ControllerConnectionError.invalidMessage
        }
        return controller
    }
    private func decode<T: Decodable>(_ body: [String: WireValue]) throws -> T {
        try ControllerJSON.decoder.decode(T.self, from: ControllerJSON.encoder.encode(body))
    }
}
