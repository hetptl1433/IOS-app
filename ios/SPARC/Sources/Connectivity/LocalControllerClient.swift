import Foundation

protocol ControllerClient: Sendable {
    func fetchSnapshot() async throws -> ChargeSnapshot
    func fetchSettings() async throws -> ControllerSettings
    func saveWeekly(_ value: WeeklySchedule) async throws
    func saveToday(_ value: TodayOverride?) async throws
    func command(_ command: String) async throws
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
    init(vault: any ControllerCredentialStore = ControllerVault.shared) {
        self.vault = vault
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 12
        configuration.timeoutIntervalForResource = 18
        configuration.waitsForConnectivity = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.httpCookieStorage = nil
        session = URLSession(configuration: configuration, delegate: ControllerRedirectBlocker(), delegateQueue: nil)
    }
    func fetchSnapshot() async throws -> ChargeSnapshot { let body = try await exchange(operation: "get_snapshot"); return try decode(body) }
    func fetchSettings() async throws -> ControllerSettings { let body = try await exchange(operation: "get_settings"); return try decode(body) }
    func saveWeekly(_ value: WeeklySchedule) async throws { _ = try await exchange(operation: "set_weekly", body: WireValue.dictionary(value)) }
    func saveToday(_ value: TodayOverride?) async throws {
        let body: [String: WireValue] = try value.map { ["today": .object(try WireValue.dictionary($0))] } ?? ["today": .null]
        _ = try await exchange(operation: "set_today", body: body)
    }
    func command(_ command: String) async throws {
        guard ["charge_now", "pause", "resume", "end"].contains(command) else { throw ControllerConnectionError.invalidMessage }
        _ = try await exchange(operation: command)
    }
    func ping(_ controller: PairedController) async throws {
        _ = try await exchange(operation: "ping", paired: controller)
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
        // Public boot challenge is authenticated indirectly: a forged challenge cannot produce a valid sealed response.
        let infoData = try await fetch(URLRequest(url: ControllerAddress.url(host: controller.host, port: controller.port, path: "/v1/info")))
        let info = try JSONDecoder().decode(ControllerInfo.self, from: infoData)
        try info.validate()
        guard info.deviceId == controller.deviceID else { throw ControllerConnectionError.invalidMessage }
        let codec = try ControllerWireCodec(setupKey: controller.setupKey, info: info)
        let id = UUID().uuidString.lowercased()
        var request = URLRequest(url: try ControllerAddress.url(host: controller.host, port: controller.port, path: "/v1/exchange"))
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
