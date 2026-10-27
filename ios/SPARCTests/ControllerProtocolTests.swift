import XCTest
import CryptoKit
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

    func testPairingCodeMatchesControllerDerivationAndRejectsNonDigits() throws {
        // Same vector as the controller's test_pairing_code_requires_app_test_and_digits.
        XCTAssertEqual(ControllerPairingCode.setupKey(for: ControllerPairingCode.default),
                       "0216e0d358ef2470c76d54687226ce99925a8dd45541d139f08806e18c574987")
        for code in ["", "123", "123456789", "12a4", "٠٠٠٠"] { XCTAssertNil(ControllerPairingCode.setupKey(for: code)) }
        XCTAssertNoThrow(try ControllerWireCodec(setupKey: XCTUnwrap(ControllerPairingCode.setupKey(for: "0000")), info: info))
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

    func testFirstTimeLANPairAuthenticatesPairAndPingWithoutSavingOrFollowingAdvertisedHost() async throws {
        let storage = MemoryControllerCredentialStore(nil)
        let server = LANTestController(info: info)
        let client = lanClient(server, storage: storage)
        let candidate = try await client.pair(host: " 192.168.1.44 ", setupKey: LANTestController.key.uppercased())
        XCTAssertEqual(candidate.deviceID, info.deviceId)
        XCTAssertEqual(candidate.host, "192.168.1.44")
        XCTAssertEqual(candidate.setupKey, LANTestController.key)
        XCTAssertNil(try storage.load(), "Transport must never write credentials before the manager checks cancellation")
        XCTAssertEqual(server.operations, ["pair", "ping"])
        XCTAssertEqual(server.requestHosts, ["192.168.1.44", "192.168.1.44", "192.168.1.44", "192.168.1.44"])
        XCTAssertFalse(server.containsPlaintextKey)
    }

    func testFirstTimeLANPairRejectsUnauthenticatedAndMismatchedProofs() async throws {
        for failure in ["wrong-key", "tamper", "forged-info", "request-id", "pair-device", "ping-device", "ping-disconnected", "pair-error", "ping-error", "redirect", "oversize"] {
            let original = pairedController()
            let storage = MemoryControllerCredentialStore(original)
            let server = LANTestController(info: info, failure: failure)
            let client = lanClient(server, storage: storage)
            do {
                _ = try await client.pair(host: "192.168.1.44", setupKey: LANTestController.key)
                XCTFail("Accepted invalid LAN authentication: \(failure)")
            } catch {}
            XCTAssertEqual(try storage.load(), original, "Replaced pairing after \(failure)")
        }
    }

    func testLANPairRejectsPublicDestinationBeforeAnyNetworkRequest() async throws {
        let server = LANTestController(info: info)
        let client = lanClient(server, storage: MemoryControllerCredentialStore(nil))
        do { _ = try await client.pair(host: "example.com", setupKey: LANTestController.key); XCTFail("Accepted public address") }
        catch {}
        XCTAssertTrue(server.requestHosts.isEmpty)
    }

    @MainActor
    func testFirstTimeLANManagerSavesOnlyAfterCompleteAuthenticatedExchange() async throws {
        let storage = MemoryControllerCredentialStore(nil)
        let server = LANTestController(info: info)
        let client = lanClient(server, storage: storage)
        let manager = ProvisioningManager(vault: storage, pairController: { host, key in
            try await client.pair(host: host, setupKey: key)
        })
        await manager.pairOnLAN(host: "192.168.1.44", setupKey: LANTestController.key)
        XCTAssertEqual(try storage.load()?.deviceID, info.deviceId)
        XCTAssertEqual(manager.stage, .ready)
        XCTAssertNil(manager.pendingController)
        XCTAssertFalse(manager.isWorking)
        XCTAssertEqual(server.operations, ["pair", "ping"])
    }

    @MainActor
    func testFirstTimeLANManagerPreservesPreviousPairingOnAuthenticationFailure() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original)
        let server = LANTestController(info: info, failure: "tamper")
        let client = lanClient(server, storage: storage)
        let manager = ProvisioningManager(vault: storage, pairController: { host, key in
            try await client.pair(host: host, setupKey: key)
        })
        await manager.pairOnLAN(host: "192.168.1.44", setupKey: LANTestController.key)
        XCTAssertEqual(try storage.load(), original)
        XCTAssertEqual(manager.savedController, original)
        XCTAssertEqual(manager.stage, .idle)
        XCTAssertNotNil(manager.errorMessage)
    }

    @MainActor
    func testCancellingNewLANPairingDiscardsLateResultAndAllowsReplacementAttempt() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original)
        let gate = ControllerVerificationGate()
        var candidate = original
        candidate.host = "192.168.1.44"
        let lateCandidate = candidate
        let manager = ProvisioningManager(vault: storage, pairController: { _, _ in
            try await gate.verify(lateCandidate)
            return lateCandidate
        })
        let operation = Task { await manager.pairOnLAN(host: "192.168.1.44", setupKey: LANTestController.key) }
        await gate.waitForRequest()
        XCTAssertEqual(try storage.load(), original)
        manager.cancel()
        // Start a different operation while the cancelled result is still in flight.
        await manager.connectOnLAN(host: "example.com")
        await gate.succeed()
        await operation.value
        XCTAssertEqual(try storage.load(), original)
        XCTAssertEqual(manager.savedController, original)
        XCTAssertNotEqual(manager.stage, .ready)
        XCTAssertNil(manager.pendingController)
        XCTAssertFalse(manager.isWorking)
    }

    @MainActor
    func testCancellingNewLANPairingTaskCannotWriteItsLateResult() async throws {
        let storage = MemoryControllerCredentialStore(nil)
        let gate = ControllerVerificationGate()
        let candidate = pairedController()
        let manager = ProvisioningManager(vault: storage, pairController: { _, _ in
            try await gate.verify(candidate)
            return candidate
        })
        let operation = Task { await manager.pairOnLAN(host: "192.168.1.44", setupKey: LANTestController.key) }
        await gate.waitForRequest()
        operation.cancel()
        await gate.succeed()
        await operation.value
        XCTAssertNil(try storage.load())
        XCTAssertEqual(manager.stage, .idle)
        XCTAssertFalse(manager.isWorking)
    }

    @MainActor
    func testNewLANPairingKeychainFailurePreservesOriginal() async throws {
        let original = pairedController()
        let storage = MemoryControllerCredentialStore(original, rejectWrites: true)
        let manager = ProvisioningManager(vault: storage, pairController: { _, _ in original })
        await manager.pairOnLAN(host: "192.168.1.44", setupKey: LANTestController.key)
        XCTAssertEqual(try storage.load(), original)
        XCTAssertEqual(manager.savedController, original)
        XCTAssertNotEqual(manager.stage, .ready)
        XCTAssertNotNil(manager.errorMessage)
    }

    func testExistingSavedCredentialsDecodeAsLANAndBluetoothIDRoundTrips() throws {
        let original = pairedController()
        let legacy: [String: Any] = ["deviceID": original.deviceID, "deviceName": original.deviceName,
            "host": original.host, "port": original.port, "setupKey": original.setupKey]
        let decoded = try JSONDecoder().decode(PairedController.self, from: JSONSerialization.data(withJSONObject: legacy))
        XCTAssertNil(decoded.bluetoothPeripheralID)
        XCTAssertEqual(decoded, original)
        var bluetooth = original
        bluetooth.bluetoothPeripheralID = UUID().uuidString
        XCTAssertEqual(try JSONDecoder().decode(PairedController.self, from: JSONEncoder().encode(bluetooth)), bluetooth)
    }

    func testBluetoothSavedPairingRoutesEveryControllerOperationWithoutLAN() async throws {
        var paired = pairedController()
        paired.bluetoothPeripheralID = UUID().uuidString
        let storage = MemoryControllerCredentialStore(paired)
        let bluetooth = RecordingBluetoothTransport()
        let lan = RecordingLANClient()
        let client = ControllerClientRouter(vault: storage, bluetooth: bluetooth, lan: lan)
        let settings = try await client.fetchSettings()
        XCTAssertEqual(settings.weekly, .default)
        let snapshot = try await client.fetchSnapshot()
        XCTAssertEqual(snapshot.state, .waiting)
        var weekly = WeeklySchedule.default
        weekly.targetPercent = 90
        try await client.saveWeekly(weekly)
        let today = TodayOverride(targetPercent: 85, departure: .now.addingTimeInterval(7200))
        try await client.saveToday(today)
        try await client.saveToday(nil)
        for command in ["charge_now", "pause", "resume", "end"] { try await client.command(command) }
        try await client.setVirtualCarEnabled(false)
        let operations = await bluetooth.operations
        let bodies = await bluetooth.bodies
        let deviceIDs = await bluetooth.deviceIDs
        let lanOperations = await lan.operations
        XCTAssertEqual(operations, ["get_settings", "get_snapshot", "set_weekly", "set_today", "set_today", "charge_now", "pause", "resume", "end", "set_virtual_car"])
        XCTAssertEqual(bodies[2]["targetPercent"], .number(90))
        XCTAssertNotNil(bodies[3]["today"])
        XCTAssertEqual(bodies[4]["today"], .null)
        XCTAssertEqual(bodies.last?["enabled"], .bool(false))
        XCTAssertTrue(deviceIDs.allSatisfy { $0 == paired.deviceID })
        XCTAssertTrue(lanOperations.isEmpty)
        XCTAssertEqual(try storage.load(), paired)
    }

    func testExistingLANPairingDoesNotInitializeOrCallBluetooth() async throws {
        let storage = MemoryControllerCredentialStore(pairedController())
        let bluetooth = RecordingBluetoothTransport()
        let lan = RecordingLANClient()
        let client = ControllerClientRouter(vault: storage, bluetooth: bluetooth, lan: lan)
        _ = try await client.fetchSettings()
        _ = try await client.fetchSnapshot()
        try await client.saveWeekly(.default)
        try await client.saveToday(nil)
        try await client.command("pause")
        try await client.setVirtualCarEnabled(true)
        let bleOperations = await bluetooth.operations
        let lanOperations = await lan.operations
        XCTAssertTrue(bleOperations.isEmpty)
        XCTAssertEqual(lanOperations, ["get_settings", "get_snapshot", "set_weekly", "set_today", "pause", "set_virtual_car"])
    }

    func testMalformedSavedBluetoothIdentityFailsBeforeEitherTransport() async throws {
        var paired = pairedController()
        paired.bluetoothPeripheralID = "not-a-peripheral-uuid"
        let storage = MemoryControllerCredentialStore(paired)
        let bluetooth = RecordingBluetoothTransport()
        let lan = RecordingLANClient()
        let client = ControllerClientRouter(vault: storage, bluetooth: bluetooth, lan: lan)
        do { try await client.command("charge_now"); XCTFail("Accepted malformed peripheral identity") }
        catch {}
        let bleOperations = await bluetooth.operations
        let lanOperations = await lan.operations
        XCTAssertTrue(bleOperations.isEmpty)
        XCTAssertTrue(lanOperations.isEmpty)
        XCTAssertEqual(try storage.load(), paired)
    }

    func testFailedBluetoothMutationIsNotRetriedOrRedirectedToLAN() async throws {
        var paired = pairedController()
        paired.bluetoothPeripheralID = UUID().uuidString
        let storage = MemoryControllerCredentialStore(paired)
        let bluetooth = RecordingBluetoothTransport(fail: true)
        let lan = RecordingLANClient()
        let client = ControllerClientRouter(vault: storage, bluetooth: bluetooth, lan: lan)
        do { try await client.command("charge_now"); XCTFail("Accepted unavailable Bluetooth transport") }
        catch {}
        let bleOperations = await bluetooth.operations
        let lanOperations = await lan.operations
        XCTAssertEqual(bleOperations, ["charge_now"])
        XCTAssertTrue(lanOperations.isEmpty)
        XCTAssertEqual(try storage.load(), paired)
    }

    @MainActor
    func testSavedBLEPollingCannotReplaceAnOwnersNewBluetoothSetup() async throws {
        var paired = pairedController()
        paired.bluetoothPeripheralID = UUID().uuidString
        let storage = MemoryControllerCredentialStore(paired)
        let manager = ProvisioningManager(vault: storage)
        // This is the same intent transition startScan performs before initializing
        // Core Bluetooth. No radio or OS permission prompt is needed for this test.
        manager.prepareForBluetoothSetup()
        XCTAssertEqual(manager.stage, .scanning)
        XCTAssertFalse(manager.isWorking)
        do {
            _ = try await manager.exchangeBLE(controller: paired, operation: "get_settings", body: [:])
            XCTFail("A live poll replaced a new pairing scan with the old saved controller")
        } catch {}
        XCTAssertEqual(manager.stage, .scanning)
        XCTAssertFalse(manager.isWorking)
        XCTAssertNil(manager.connectedDeviceName)
        await manager.reconnectSavedController()
        XCTAssertEqual(manager.stage, .scanning, "Saved reconnect must not replace new setup either")
        XCTAssertEqual(manager.savedController, paired)
        XCTAssertEqual(try storage.load(), paired)
        manager.cancel()
        XCTAssertEqual(manager.stage, .idle)
    }

    @MainActor
    func testFailedLANConversionPreservesBluetoothPairing() async throws {
        var paired = pairedController()
        paired.bluetoothPeripheralID = UUID().uuidString
        let storage = MemoryControllerCredentialStore(paired)
        let manager = ProvisioningManager(vault: storage, verifyController: { _ in throw ControllerConnectionError.invalidMessage })
        await manager.connectOnLAN(host: "192.168.1.44")
        XCTAssertEqual(try storage.load(), paired)
        XCTAssertEqual(manager.savedController, paired)
    }

    @MainActor
    func testLANConversionClearsBluetoothRouteOnlyAfterVerification() async throws {
        var paired = pairedController()
        paired.bluetoothPeripheralID = UUID().uuidString
        let storage = MemoryControllerCredentialStore(paired)
        let gate = ControllerVerificationGate()
        let manager = ProvisioningManager(vault: storage, verifyController: { try await gate.verify($0) })
        let operation = Task { await manager.connectOnLAN(host: "192.168.1.44") }
        await gate.waitForRequest()
        XCTAssertEqual(try storage.load(), paired)
        await gate.succeed()
        await operation.value
        var expected = paired
        expected.bluetoothPeripheralID = nil
        expected.host = "192.168.1.44"
        XCTAssertEqual(try storage.load(), expected)
        XCTAssertEqual(manager.stage, .ready)
    }

    private func lanClient(_ server: LANTestController, storage: MemoryControllerCredentialStore) -> LocalControllerClient {
        LANTestURLProtocol.state.set(server)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [LANTestURLProtocol.self]
        return LocalControllerClient(vault: storage, configuration: configuration)
    }

    private func pairedController() -> PairedController {
        PairedController(deviceID: info.deviceId, deviceName: info.name, host: "saved.local",
                         setupKey: String(repeating: "a1", count: 32))
    }

    func testControllerStatusReadsWiFiAndToleratesMissingOptionalFields() throws {
        let offline = try XCTUnwrap(ControllerStatus([
            "uptimeSeconds": .number(3_725), "temperatureC": .number(48.3), "bluetooth": .bool(true),
            "wifi": .object(["connected": .bool(false), "ssid": .null, "canSetUp": .bool(true)]),
        ]))
        XCTAssertEqual(offline.uptimeSeconds, 3_725)
        XCTAssertEqual(offline.temperatureC, 48.3)
        XCTAssertEqual(offline.wifi, ControllerStatus.WiFi(connected: false, ssid: nil, canSetUp: true))

        let lanOnly = try XCTUnwrap(ControllerStatus(["uptimeSeconds": .number(5), "bluetooth": .bool(false), "wifi": .null]))
        XCTAssertNil(lanOnly.temperatureC)
        XCTAssertNil(lanOnly.wifi)
        XCTAssertNil(ControllerStatus(["bluetooth": .bool(true)]))
    }

    func testWiFiNetworkListKeepsOnlyNamedPasswordProtectedNetworks() {
        let networks = ControllerWiFiNetwork.list(["networks": .array([
            .object(["ssid": .string("home"), "strength": .number(82), "secured": .bool(true)]),
            .object(["ssid": .string("cafe"), "strength": .number(60), "secured": .bool(false)]),
            .object(["ssid": .string(""), "strength": .number(50), "secured": .bool(true)]),
            .string("garbage"),
        ])])
        XCTAssertEqual(networks, [ControllerWiFiNetwork(ssid: "home", strength: 82)])
        XCTAssertEqual(ControllerWiFiNetwork.list([:]), [])
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

/// A simulated controller decrypts the real request and encrypts a fresh response.
/// It exercises the URLSession transport and production codec, using only public test credentials.
private final class LANTestController: @unchecked Sendable {
    static let key = String(repeating: "a1", count: 32)
    private let lock = NSLock()
    private let info: ControllerInfo
    private let failure: String
    private var recordedOperations: [String] = []
    private var recordedHosts: [String] = []
    private var leakedKey = false
    var operations: [String] { lock.lock(); defer { lock.unlock() }; return recordedOperations }
    var requestHosts: [String] { lock.lock(); defer { lock.unlock() }; return recordedHosts }
    var containsPlaintextKey: Bool { lock.lock(); defer { lock.unlock() }; return leakedKey }
    init(info: ControllerInfo, failure: String = "") { self.info = info; self.failure = failure }

    func respond(to request: URLRequest) throws -> (HTTPURLResponse, Data) {
        lock.lock(); defer { lock.unlock() }
        let url = try XCTUnwrap(request.url)
        recordedHosts.append(url.host ?? "")
        if failure == "redirect" {
            return (HTTPURLResponse(url: url, statusCode: 302, httpVersion: nil,
                                     headerFields: ["Location": "http://example.com/v1/info"])!, Data())
        }
        if url.path == "/v1/info" {
            if failure == "oversize" { return (response(url), Data(repeating: 32, count: 16_385)) }
            var value = try WireValue.dictionary(info)
            if failure == "forged-info" { value["deviceId"] = .string(UUID().uuidString.lowercased()) }
            return (response(url), try ControllerJSON.encoder.encode(value))
        }
        struct Envelope: Codable { let v: Int; let box: String }
        let data: Data
        if let body = request.httpBody { data = body }
        else {
            let stream = try XCTUnwrap(request.httpBodyStream)
            stream.open(); defer { stream.close() }
            var body = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                guard count >= 0 else { throw ControllerConnectionError.invalidMessage }
                if count == 0 { break }
                body.append(contentsOf: buffer.prefix(count))
            }
            data = body
        }
        leakedKey = leakedKey || (String(data: data, encoding: .utf8)?.contains(Self.key) == true)
        let envelope = try JSONDecoder().decode(Envelope.self, from: data)
        let master = SymmetricKey(data: Data(repeating: 0xa1, count: 32))
        let sendKey = HKDF<SHA256>.deriveKey(inputKeyMaterial: master, salt: Data(info.deviceId.utf8),
                                          info: Data("SPARC/1/c2d".utf8), outputByteCount: 32)
        let combined = try XCTUnwrap(Data(base64Encoded: envelope.box))
        let clear = try ChaChaPoly.open(ChaChaPoly.SealedBox(combined: combined), using: sendKey,
                                       authenticating: aad("c2d"))
        let message = try JSONDecoder().decode([String: WireValue].self, from: clear)
        let operation = try XCTUnwrap(message["operation"]?.string)
        recordedOperations.append(operation)
        var body: [String: WireValue] = ["deviceId": .string(info.deviceId), "connected": .bool(true),
                                        "host": .string("attacker.local"), "vehicleAdapterAvailable": .bool(false)]
        if failure == "pair-device" && operation == "pair" || failure == "ping-device" && operation == "ping" {
            body["deviceId"] = .string(UUID().uuidString.lowercased())
        }
        if failure == "ping-disconnected" && operation == "ping" { body["connected"] = .bool(false) }
        let reject = failure == "pair-error" && operation == "pair" || failure == "ping-error" && operation == "ping"
        let reply: [String: WireValue] = ["id": failure == "request-id" ? .string(UUID().uuidString.lowercased()) : message["id"]!,
                                          "ok": .bool(!reject), "body": .object(body),
                                          "error": .object(["code": .string("invalid_request"), "message": .string("Setup window is closed.")])]
        let receiveMaster = failure == "wrong-key" ? SymmetricKey(data: Data(repeating: 0xb2, count: 32)) : master
        let receiveKey = HKDF<SHA256>.deriveKey(inputKeyMaterial: receiveMaster, salt: Data(info.deviceId.utf8),
                                             info: Data("SPARC/1/d2c".utf8), outputByteCount: 32)
        var result = try ChaChaPoly.seal(ControllerJSON.encoder.encode(reply), using: receiveKey,
                                        authenticating: aad("d2c")).combined
        if failure == "tamper" { result[result.count - 1] ^= 1 }
        return (response(url), try JSONEncoder().encode(Envelope(v: 1, box: result.base64EncodedString())))
    }
    private func aad(_ direction: String) -> Data { Data("SPARC/1\n\(info.deviceId)\n\(info.bootId)\n\(direction)".utf8) }
    private func response(_ url: URL) -> HTTPURLResponse {
        HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
    }
}

private final class LANTestURLProtocol: URLProtocol, @unchecked Sendable {
    final class State: @unchecked Sendable {
        private let lock = NSLock()
        private var server: LANTestController?
        func set(_ server: LANTestController) { lock.lock(); defer { lock.unlock() }; self.server = server }
        func get() throws -> LANTestController { lock.lock(); defer { lock.unlock() }; return try XCTUnwrap(server) }
    }
    static let state = State()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (response, data) = try Self.state.get().respond(to: request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}


private actor RecordingBluetoothTransport: BluetoothControllerTransport {
    private(set) var operations: [String] = []
    private(set) var bodies: [[String: WireValue]] = []
    private(set) var deviceIDs: [String] = []
    private let fail: Bool
    init(fail: Bool = false) { self.fail = fail }
    func exchangeBLE(controller: PairedController, operation: String, body: [String: WireValue]) async throws -> [String: WireValue] {
        operations.append(operation); bodies.append(body); deviceIDs.append(controller.deviceID)
        if fail { throw ControllerConnectionError.timeout }
        switch operation {
        case "get_settings": return try WireValue.dictionary(ControllerSettings(weekly: .default, today: nil))
        case "get_snapshot": return try WireValue.dictionary(ChargeSnapshot.demo())
        default: return [:]
        }
    }
}

private actor RecordingLANClient: ControllerClient {
    private(set) var operations: [String] = []
    func fetchSnapshot() async throws -> ChargeSnapshot { operations.append("get_snapshot"); return .demo() }
    func fetchSettings() async throws -> ControllerSettings { operations.append("get_settings"); return ControllerSettings(weekly: .default, today: nil) }
    func saveWeekly(_ value: WeeklySchedule) async throws { operations.append("set_weekly") }
    func saveToday(_ value: TodayOverride?) async throws { operations.append("set_today") }
    func command(_ command: String) async throws { operations.append(command) }
    func setVirtualCarEnabled(_ enabled: Bool) async throws { operations.append("set_virtual_car") }
}
