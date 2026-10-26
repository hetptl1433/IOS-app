import XCTest
@testable import SPARC

final class ControllerProtocolTests: XCTestCase {
    private struct Fixture: Decodable {
        struct Request: Decodable {
            let id: String
            let timestamp: Double
            let operation: String
            let body: [String: WireValue]
        }
        struct Envelope: Codable { let v: Int; var box: String }
        let setupKeyHex: String
        let info: ControllerInfo
        let request: Request
        let requestNonceHex: String
        let requestEnvelope: Envelope
        let responseEnvelope: Envelope
        let response: ControllerWireResponse
    }

    private func fixture() throws -> Fixture {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "wire-v1", withExtension: "json"))
        return try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
    }

    func testPythonAndSwiftProduceIdenticalAuthenticatedWireMessages() throws {
        let vector = try fixture()
        let codec = try ControllerWireCodec(setupKey: vector.setupKeyHex, info: vector.info)
        let text = Array(vector.requestNonceHex.utf8)
        let nonce = Data(stride(from: 0, to: text.count, by: 2).map {
            UInt8(String(decoding: text[$0..<$0+2], as: UTF8.self), radix: 16)!
        })
        let request = try codec.sealRequest(operation: vector.request.operation, body: vector.request.body,
            id: vector.request.id, timestamp: vector.request.timestamp, nonce: nonce)
        let sealed = try JSONDecoder().decode(Fixture.Envelope.self, from: request)
        XCTAssertEqual(sealed.v, vector.requestEnvelope.v)
        XCTAssertEqual(sealed.box, vector.requestEnvelope.box)
        let response = try codec.openResponse(JSONEncoder().encode(vector.responseEnvelope), expectedID: vector.request.id)
        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.id, vector.response.id)
        XCTAssertEqual(response.body, vector.response.body)
    }

    func testKnownResponseRejectsTamperingWrongKeyBootAndRequestIdentity() throws {
        let vector = try fixture()
        let codec = try ControllerWireCodec(setupKey: vector.setupKeyHex, info: vector.info)
        let response = try JSONEncoder().encode(vector.responseEnvelope)
        XCTAssertThrowsError(try codec.openResponse(response, expectedID: UUID().uuidString))
        var changed = vector.responseEnvelope
        var combined = try XCTUnwrap(Data(base64Encoded: changed.box))
        combined[combined.count - 1] ^= 1
        changed.box = combined.base64EncodedString()
        XCTAssertThrowsError(try codec.openResponse(JSONEncoder().encode(changed), expectedID: vector.request.id))
        let wrongKey = try ControllerWireCodec(setupKey: String(repeating: "ff", count: 32), info: vector.info)
        XCTAssertThrowsError(try wrongKey.openResponse(response, expectedID: vector.request.id))
        let newBoot = ControllerInfo(version: 1, deviceId: vector.info.deviceId, bootId: UUID().uuidString,
            name: vector.info.name, serverTime: vector.info.serverTime, capabilities: vector.info.capabilities,
            vehicleAdapterAvailable: false)
        let restarted = try ControllerWireCodec(setupKey: vector.setupKeyHex, info: newBoot)
        XCTAssertThrowsError(try restarted.openResponse(response, expectedID: vector.request.id))
    }

    private let info = ControllerInfo(version: 1,
        deviceId: "2da0c3ee-32b1-4b92-9844-426930f782e7",
        bootId: "43a9df7c-97e1-4c3a-af0f-c367442ec384", name: "SPARC Test Pi",
        serverTime: 1_793_000_000, capabilities: ["wifi", "settings"], vehicleAdapterAvailable: false)

    func testFrameReassemblesAnArbitraryBLEMTUAndResetsForNextMessage() throws {
        let original = Data((0..<230).map { UInt8($0 % 256) })
        let framed = try ControllerFrameBuffer.frame(original)
        var buffer = ControllerFrameBuffer()
        var result: Data?
        for start in stride(from: 0, to: framed.count, by: 17) {
            result = try buffer.append(framed.subdata(in: start..<min(start + 17, framed.count)))
        }
        XCTAssertEqual(result, original)
        XCTAssertEqual(try buffer.append(ControllerFrameBuffer.frame(Data("next".utf8))), Data("next".utf8))
    }

    func testFrameRejectsOversizeZeroLengthAndTrailingData() throws {
        XCTAssertThrowsError(try ControllerFrameBuffer.frame(Data()))
        XCTAssertThrowsError(try ControllerFrameBuffer.frame(Data(repeating: 0, count: 16_385)))
        var buffer = ControllerFrameBuffer()
        XCTAssertThrowsError(try buffer.append(Data([0, 0, 0, 0])))
        XCTAssertThrowsError(try buffer.append(Data([0, 0, 64, 1])))
        let framed = try ControllerFrameBuffer.frame(Data("hello".utf8))
        XCTAssertThrowsError(try buffer.append(framed + Data([0])))
        // Invalid input clears the parser instead of contaminating the next exchange.
        XCTAssertEqual(try buffer.append(framed), Data("hello".utf8))
    }

    func testMaximumFrameAndSplitHeaderAreAccepted() throws {
        let payload = Data(repeating: 42, count: ControllerFrameBuffer.maximumLength)
        let frame = try ControllerFrameBuffer.frame(payload)
        var buffer = ControllerFrameBuffer()
        XCTAssertNil(try buffer.append(frame.prefix(1)))
        XCTAssertNil(try buffer.append(frame.subdata(in: 1..<3)))
        XCTAssertEqual(try buffer.append(frame.dropFirst(3)), payload)
    }

    func testOnlyLocalControllerAddressesAreAccepted() throws {
        for host in ["sparc-pi.local", "SPARC-8426.local", "10.0.0.2", "172.16.1.2", "172.31.255.254", "192.168.1.5", "169.254.10.20", "127.0.0.1", "localhost"] {
            let url = try ControllerAddress.url(host: host, port: 8787, path: "/v1/info")
            XCTAssertEqual(url.scheme, "http")
            XCTAssertEqual(url.port, 8787)
        }
        for host in ["8.8.8.8", "172.32.0.1", "192.0.2.1", "example.com", "https://sparc.local", "sparc.local@evil.com", "192.168.x.0.1", "192..168.0.1", "192.168.1.1.", "192.168.001.1", "-bad.local", "bad-.local", ".local", "bad..local"] {
            XCTAssertThrowsError(try ControllerAddress.url(host: host, port: 8787, path: "/v1/info"), "Accepted \(host)")
        }
        XCTAssertThrowsError(try ControllerAddress.url(host: "sparc.local", port: 0, path: "/v1/info"))
        XCTAssertThrowsError(try ControllerAddress.url(host: "sparc.local", port: 65536, path: "/v1/info"))
    }

    func testSetupKeyMustBeExactly32BytesOfHex() throws {
        for key in ["", String(repeating: "a", count: 63), String(repeating: "g", count: 64), String(repeating: "a", count: 65), String(repeating: "a", count: 64) + "\u{0301}"] {
            XCTAssertThrowsError(try ControllerWireCodec(setupKey: key, info: info))
        }
        XCTAssertNoThrow(try ControllerWireCodec(setupKey: String(repeating: "a1", count: 32), info: info))
    }

    func testForgedAndWrongDirectionMessagesAreNotAcceptedAsResponses() throws {
        let codec = try ControllerWireCodec(setupKey: String(repeating: "a1", count: 32), info: info)
        let id = "8ec57911-640b-4c2c-929d-2210ab2fdab5"
        let request = try codec.sealRequest(operation: "ping", body: [:], id: id, timestamp: info.serverTime)
        XCTAssertThrowsError(try codec.openResponse(request, expectedID: id))
        XCTAssertThrowsError(try codec.openResponse(Data("{\"v\":1,\"box\":\"AAAA\"}".utf8), expectedID: id))
        XCTAssertThrowsError(try codec.openResponse(Data("{}".utf8), expectedID: id))
    }

    func testWiFiPasswordIsNotPresentInSerializedRequest() throws {
        let codec = try ControllerWireCodec(setupKey: String(repeating: "a1", count: 32), info: info)
        let request = try codec.sealRequest(operation: "configure_wifi", body: [
            "ssid": .string("My Home Network"), "password": .string("correct-horse-battery-staple")
        ], id: UUID().uuidString, timestamp: info.serverTime)
        let serialized = try XCTUnwrap(String(data: request, encoding: .utf8))
        XCTAssertFalse(serialized.contains("My Home Network"))
        XCTAssertFalse(serialized.contains("correct-horse-battery-staple"))
        XCTAssertFalse(serialized.contains("configure_wifi"))
    }

    func testDeviceInfoRejectsWrongVersionAndInvalidIdentity() {
        let invalid = ControllerInfo(version: 2, deviceId: "invalid", bootId: info.bootId,
            name: "Untrusted", serverTime: .infinity, capabilities: [], vehicleAdapterAvailable: false)
        XCTAssertThrowsError(try invalid.validate())
    }
    @MainActor
    func testCancellingLANVerificationPreservesThePreviousPairing() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original)
        let gate = ControllerVerificationGate()
        let manager = ProvisioningManager(vault: storage, verifyController: { try await gate.verify($0) })
        let operation = Task { await manager.connectOnLAN(host: "192.168.1.44") }
        await gate.waitForRequest()
        manager.cancel()
        await gate.succeed()
        await operation.value
        XCTAssertEqual(try storage.load(), original)
        XCTAssertEqual(manager.savedController, original)
        XCTAssertNil(manager.pendingController)
        XCTAssertEqual(manager.stage, .idle)
        XCTAssertFalse(manager.isWorking)
    }

    @MainActor
    func testCancellingTheVerificationTaskCannotSaveItsLateSuccess() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original)
        let gate = ControllerVerificationGate()
        let manager = ProvisioningManager(vault: storage, verifyController: { try await gate.verify($0) })
        let operation = Task { await manager.connectOnLAN(host: "192.168.1.44") }
        await gate.waitForRequest()
        operation.cancel()
        await gate.succeed()
        await operation.value
        XCTAssertEqual(try storage.load(), original)
        XCTAssertEqual(manager.savedController, original)
        XCTAssertEqual(manager.stage, .idle)
        XCTAssertFalse(manager.isWorking)
    }

    @MainActor
    func testLANPairingIsSavedOnlyAfterAuthenticatedVerification() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original)
        let gate = ControllerVerificationGate()
        let manager = ProvisioningManager(vault: storage, verifyController: { try await gate.verify($0) })
        let operation = Task { await manager.connectOnLAN(host: " 192.168.1.44 ") }
        await gate.waitForRequest()
        XCTAssertEqual(try storage.load(), original)
        XCTAssertTrue(manager.isWorking)
        await gate.succeed()
        await operation.value
        var expected = original
        expected.host = "192.168.1.44"
        XCTAssertEqual(try storage.load(), expected)
        XCTAssertEqual(manager.savedController, expected)
        XCTAssertEqual(manager.stage, .ready)
        XCTAssertFalse(manager.isWorking)
    }

    @MainActor
    func testFailedLANVerificationKeepsExistingCredentialsAndRemainsRetryable() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original)
        let manager = ProvisioningManager(vault: storage, verifyController: { _ in
            throw ControllerConnectionError.invalidMessage
        })
        await manager.connectOnLAN(host: "192.168.1.44")
        XCTAssertEqual(try storage.load(), original)
        XCTAssertEqual(manager.savedController, original)
        XCTAssertEqual(manager.stage, .paired)
        XCTAssertNotNil(manager.errorMessage)
        XCTAssertFalse(manager.isWorking)
    }

    @MainActor
    func testSavedPairingIsRetainedIfKeychainWriteFails() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original, rejectWrites: true)
        let manager = ProvisioningManager(vault: storage, verifyController: { _ in })
        await manager.connectOnLAN(host: "192.168.1.44")
        XCTAssertEqual(try storage.load(), original)
        XCTAssertEqual(manager.savedController, original)
        XCTAssertNotEqual(manager.stage, .ready)
        XCTAssertNotNil(manager.errorMessage)
    }

    func testWiFiValidationUsesByteLimitsAndRejectsMalformedCredentials() throws {
        for (ssid, password) in [("", "password"), (String(repeating: "x", count: 33), "password"),
                                 (String(repeating: "é", count: 17), "password"), ("Home\0", "password"),
                                 ("Home", "short"), ("Home", "password\0"),
                                 ("Home", String(repeating: "g", count: 64)),
                                 ("Home", String(repeating: "Ａ", count: 64))] {
            XCTAssertThrowsError(try ControllerWiFiCredentials.validate(ssid: ssid, password: password))
        }
        XCTAssertNoThrow(try ControllerWiFiCredentials.validate(ssid: "Home", password: "password"))
        XCTAssertNoThrow(try ControllerWiFiCredentials.validate(ssid: String(repeating: "é", count: 16), password: String(repeating: "aF", count: 32)))
    }

    private func pairedController() -> PairedController {
        PairedController(deviceID: info.deviceId, deviceName: info.name, host: "saved.local",
                         setupKey: String(repeating: "a1", count: 32))
    }

}



private final class MemoryControllerCredentialStore: ControllerCredentialStore, @unchecked Sendable {
    private let lock = NSLock()
    private var controller: PairedController?
    private let rejectWrites: Bool
    init(_ controller: PairedController?, rejectWrites: Bool = false) {
        self.controller = controller
        self.rejectWrites = rejectWrites
    }
    func load() throws -> PairedController? {
        lock.lock(); defer { lock.unlock() }
        return controller
    }
    func save(_ controller: PairedController) throws {
        lock.lock(); defer { lock.unlock() }
        if rejectWrites { throw ControllerConnectionError.unavailable("Unlock iPhone and retry.") }
        self.controller = controller
    }
    func clear() throws {
        lock.lock(); defer { lock.unlock() }
        controller = nil
    }
}

private actor ControllerVerificationGate {
    private var pending: CheckedContinuation<Void, Error>?
    private var waiting: [CheckedContinuation<Void, Never>] = []
    func verify(_ controller: PairedController) async throws {
        try await withCheckedThrowingContinuation { continuation in
            pending = continuation
            waiting.forEach { $0.resume() }
            waiting.removeAll()
        }
    }
    func waitForRequest() async {
        if pending != nil { return }
        await withCheckedContinuation { waiting.append($0) }
    }
    func succeed() { pending?.resume(); pending = nil }
}
