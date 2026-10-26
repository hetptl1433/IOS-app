import Foundation
import CryptoKit

enum ControllerConnectionError: LocalizedError {
    case invalidKey, invalidMessage, invalidAddress, notPaired, timeout, cancelled
    case unavailable(String)
    var errorDescription: String? {
        switch self {
        case .invalidKey: return "Enter the 64-character setup key shown on your Raspberry Pi."
        case .invalidMessage: return "The controller response could not be authenticated. Check the setup key and try again."
        case .invalidAddress: return "Enter the controller’s .local name or a private home-network IP address."
        case .notPaired: return "Pair your Raspberry Pi in Settings first."
        case .timeout: return "The controller did not respond. Keep it nearby and try again."
        case .cancelled: return "Connection cancelled."
        case .unavailable(let message): return message
        }
    }
}

enum WireValue: Codable, Equatable, Sendable {
    case string(String), number(Double), bool(Bool), object([String: WireValue]), array([WireValue]), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let x = try? c.decode(Bool.self) { self = .bool(x) }
        else if let x = try? c.decode(Double.self) { self = .number(x) }
        else if let x = try? c.decode(String.self) { self = .string(x) }
        else if let x = try? c.decode([String: WireValue].self) { self = .object(x) }
        else { self = .array(try c.decode([WireValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let x): try c.encode(x)
        case .number(let x): try c.encode(x)
        case .bool(let x): try c.encode(x)
        case .object(let x): try c.encode(x)
        case .array(let x): try c.encode(x)
        case .null: try c.encodeNil()
        }
    }
    var string: String? { if case .string(let x) = self { return x }; return nil }
    static func dictionary<T: Encodable>(_ value: T) throws -> [String: WireValue] {
        try JSONDecoder().decode([String: WireValue].self, from: ControllerJSON.encoder.encode(value))
    }
}

enum ControllerJSON {
    static var encoder: JSONEncoder { let x = JSONEncoder(); x.dateEncodingStrategy = .iso8601; x.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]; return x }
    static var decoder: JSONDecoder {
        let x = JSONDecoder()
        x.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: value) { return date }
            formatter.formatOptions = [.withInternetDateTime]
            guard let date = formatter.date(from: value) else { throw ControllerConnectionError.invalidMessage }
            return date
        }
        return x
    }
}

struct ControllerInfo: Codable, Equatable, Sendable {
    let version: Int
    let deviceId: String
    let bootId: String
    let name: String
    let serverTime: Double
    let capabilities: [String]
    let vehicleAdapterAvailable: Bool
    func validate() throws {
        guard version == 1, UUID(uuidString: deviceId) != nil,
              UUID(uuidString: bootId) != nil, name.count <= 80, serverTime.isFinite else {
            throw ControllerConnectionError.invalidMessage
        }
    }
}

struct ControllerWireResponse: Codable, Sendable {
    struct Failure: Codable, Sendable { let code: String; let message: String }
    let id: String
    let ok: Bool
    let body: [String: WireValue]?
    let error: Failure?
}

struct ControllerWireCodec {
    let info: ControllerInfo
    private let sendKey: SymmetricKey
    private let receiveKey: SymmetricKey
    init(setupKey: String, info: ControllerInfo) throws {
        try info.validate()
        guard setupKey.utf8.count == 64, setupKey.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }) else { throw ControllerConnectionError.invalidKey }
        let chars = Array(setupKey.utf8)
        var bytes = Data()
        for index in stride(from: 0, to: 64, by: 2) {
            guard let value = UInt8(String(decoding: chars[index..<index+2], as: UTF8.self), radix: 16) else { throw ControllerConnectionError.invalidKey }
            bytes.append(value)
        }
        let master = SymmetricKey(data: bytes)
        sendKey = HKDF<SHA256>.deriveKey(inputKeyMaterial: master, salt: Data(info.deviceId.utf8), info: Data("SPARC/1/c2d".utf8), outputByteCount: 32)
        receiveKey = HKDF<SHA256>.deriveKey(inputKeyMaterial: master, salt: Data(info.deviceId.utf8), info: Data("SPARC/1/d2c".utf8), outputByteCount: 32)
        self.info = info
    }
    private func aad(_ direction: String) -> Data { Data("SPARC/1\n\(info.deviceId)\n\(info.bootId)\n\(direction)".utf8) }
    func sealRequest(operation: String, body: [String: WireValue], id: String, timestamp: Double,
                     nonce: Data? = nil) throws -> Data {
        struct Request: Encodable { let id: String; let timestamp: Double; let operation: String; let body: [String: WireValue] }
        struct Envelope: Encodable { let v = 1; let box: String }
        let plaintext = try ControllerJSON.encoder.encode(Request(id: id, timestamp: timestamp, operation: operation, body: body))
        let sealed: ChaChaPoly.SealedBox
        if let nonce { sealed = try ChaChaPoly.seal(plaintext, using: sendKey, nonce: ChaChaPoly.Nonce(data: nonce), authenticating: aad("c2d")) }
        else { sealed = try ChaChaPoly.seal(plaintext, using: sendKey, authenticating: aad("c2d")) }
        let data = try ControllerJSON.encoder.encode(Envelope(box: sealed.combined.base64EncodedString()))
        guard data.count <= ControllerFrameBuffer.maximumLength else { throw ControllerConnectionError.invalidMessage }
        return data
    }
    func openResponse(_ data: Data, expectedID: String) throws -> ControllerWireResponse {
        struct Envelope: Decodable { let v: Int; let box: String }
        guard data.count <= ControllerFrameBuffer.maximumLength else { throw ControllerConnectionError.invalidMessage }
        do {
            let envelope = try JSONDecoder().decode(Envelope.self, from: data)
            guard envelope.v == 1, let combined = Data(base64Encoded: envelope.box) else { throw ControllerConnectionError.invalidMessage }
            let plaintext = try ChaChaPoly.open(ChaChaPoly.SealedBox(combined: combined), using: receiveKey, authenticating: aad("d2c"))
            let reply = try JSONDecoder().decode(ControllerWireResponse.self, from: plaintext)
            guard reply.id.caseInsensitiveCompare(expectedID) == .orderedSame else { throw ControllerConnectionError.invalidMessage }
            return reply
        } catch { throw ControllerConnectionError.invalidMessage }
    }
}

struct ControllerFrameBuffer {
    static let maximumLength = 16_384
    private var buffer = Data()
    mutating func append(_ chunk: Data) throws -> Data? {
        guard buffer.count + chunk.count <= Self.maximumLength + 4 else { buffer.removeAll(); throw ControllerConnectionError.invalidMessage }
        buffer.append(chunk)
        guard buffer.count >= 4 else { return nil }
        let count = buffer.prefix(4).reduce(0) { ($0 << 8) | Int($1) }
        guard count > 0, count <= Self.maximumLength, buffer.count <= count + 4 else { buffer.removeAll(); throw ControllerConnectionError.invalidMessage }
        guard buffer.count == count + 4 else { return nil }
        let payload = Data(buffer.dropFirst(4)); buffer.removeAll(keepingCapacity: true); return payload
    }
    static func frame(_ payload: Data) throws -> Data {
        guard !payload.isEmpty, payload.count <= maximumLength else { throw ControllerConnectionError.invalidMessage }
        let count = UInt32(payload.count)
        return Data([UInt8((count >> 24) & 255), UInt8((count >> 16) & 255), UInt8((count >> 8) & 255), UInt8(count & 255)]) + payload
    }
}
