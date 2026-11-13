import XCTest
import Combine
@testable import SPARC

@MainActor
private final class PerformanceClock {
    var date = Date(timeIntervalSince1970: 1_793_975_400)
}

private actor PerformanceController: ControllerClient {
    var settings: ControllerSettings
    var snapshot: ChargeSnapshot
    init(now: Date) {
        settings = ControllerSettings(weekly: .default, today: nil,
            developer: DeveloperVehicleState(enabled: true, simulated: true, batteryPercent: 40,
                                             pluggedIn: true, revision: 1, updatedAt: now))
        snapshot = .demo(now: now)
    }
    func changeBattery(_ value: Double) {
        settings.developer?.batteryPercent = value
        settings.developer?.revision += 1
        snapshot.batteryPercent = Int(value.rounded(.down))
    }
    func fetchSettings() async throws -> ControllerSettings { settings }
    func fetchSnapshot() async throws -> ChargeSnapshot { snapshot }
    func fetchHistory(cursor: Int?) async throws -> ControllerHistoryPage { throw ControllerConnectionError.unavailable("No history yet") }
    func setVirtualCarState(batteryPercent: Double, pluggedIn: Bool) async throws {
        changeBattery(batteryPercent); settings.developer?.pluggedIn = pluggedIn
    }
    func saveWeekly(_ value: WeeklySchedule) async throws { settings.weekly = value }
    func saveToday(_ value: TodayOverride?) async throws { settings.today = value }
    func command(_ command: String) async throws {}
}

final class PerformanceSafetyTests: XCTestCase {
    func testMalformedStatusAndWiFiNumbersAreRejectedWithoutIntegerTraps() {
        for number in [Double.nan, .infinity, -.infinity, 1e300, -1, 0.5] {
            XCTAssertNil(ControllerStatus(["uptimeSeconds": .number(number), "bluetooth": .bool(true)]))
            let network: WireValue = .object(["ssid": .string("Test"), "strength": .number(number), "secured": .bool(true)])
            XCTAssertTrue(ControllerWiFiNetwork.list(["networks": .array([network])]).isEmpty)
        }
        XCTAssertEqual(ControllerStatus(["uptimeSeconds": .number(42), "bluetooth": .bool(true)])?.uptimeSeconds, 42)
        XCTAssertNil(ControllerStatus(["uptimeSeconds": .number(1), "bluetooth": .bool(true), "temperatureC": .number(.infinity)])?.temperatureC)
        XCTAssertEqual(ControllerWiFiNetwork.list(["networks": .array([.object(["ssid": .string("Test"), "strength": .number(75), "secured": .bool(true)])])]).first?.strength, 75)
    }

    func testExtremeRateAndDateFormattingDoesNotTrap() {
        XCTAssertEqual(SPARCFormat.cents(21), "21")
        XCTAssertEqual(SPARCFormat.cents(.nan), "—")
        XCTAssertFalse(SPARCFormat.cents(1e300).isEmpty)
        XCTAssertEqual(SPARCFormat.ago(Date(timeIntervalSince1970: -1e300)), "Earlier")
    }

    @MainActor
    func testRepeatedUnchangedLivePollDoesNotPublishOrRewriteCache() async {
        let suite = "SPARC.Performance.\(UUID())", defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let clock = PerformanceClock(), client = PerformanceController(now: clock.date)
        let store = AppStore(defaults: defaults, client: client, now: { clock.date }, arguments: [],
                             controllerID: { "perf-pi" }, controllerNameProvider: { "Test Pi" })
        await store.refresh()
        let original = defaults.data(forKey: "sparc.controller.history.v1.perf-pi")
        XCTAssertNotNil(original)
        var publications = 0
        let observer = store.objectWillChange.sink { publications += 1 }
        for _ in 0..<10 { await store.refresh() }
        XCTAssertEqual(publications, 0)
        XCTAssertEqual(defaults.data(forKey: "sparc.controller.history.v1.perf-pi"), original)
        withExtendedLifetime(observer) {}
    }

    @MainActor
    func testPollingCacheThrottleKeepsFreshStateAndExplicitMutationPersistsImmediately() async throws {
        let suite = "SPARC.Performance.\(UUID())", defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let clock = PerformanceClock(), client = PerformanceController(now: clock.date)
        let store = AppStore(defaults: defaults, client: client, now: { clock.date }, arguments: [],
                             controllerID: { "perf-pi" }, controllerNameProvider: { "Test Pi" })
        await store.refresh()
        let key = "sparc.controller.history.v1.perf-pi", original = defaults.data(forKey: "sparc.controller.history.v1.perf-pi")
        await client.changeBattery(41)
        clock.date += 4
        await store.refresh()
        XCTAssertEqual(store.developerState?.batteryPercent, 41)
        XCTAssertEqual(defaults.data(forKey: key), original)
        await store.setVirtualCarState(batteryPercent: 55, pluggedIn: true)
        XCTAssertNil(store.errorMessage)
        let data = try XCTUnwrap(defaults.data(forKey: key))
        let cache = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual((cache["developer"] as? [String: Any])?["batteryPercent"] as? Double, 55)
        XCTAssertNotEqual(data, original)
    }

    @MainActor
    func testOversizedLocalCacheIsIgnoredBeforeJSONDecode() {
        let suite = "SPARC.Performance.\(UUID())", defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(Data(repeating: 0x5b, count: 2 * 1024 * 1024 + 1), forKey: "sparc.controller.history.v1.perf-pi")
        let store = AppStore(defaults: defaults, arguments: [], controllerID: { "perf-pi" }, controllerNameProvider: { nil })
        XCTAssertTrue(store.historySessions.isEmpty)
        XCTAssertEqual(store.snapshot.state, .offline)
    }

    func testSharedNotificationStreamRecoversAfterLongNoiseAndPartialFrame() throws {
        var stream = ControllerNotificationStream()
        XCTAssertTrue(stream.append(Data(repeating: 0x41, count: 100_000)).isEmpty)
        let payload = Data("{\"v\":1,\"box\":\"fixture\"}".utf8)
        let frame = try ControllerFrameBuffer.frame(payload)
        XCTAssertTrue(stream.append(Data("other-response-tail".utf8) + frame.prefix(3)).isEmpty)
        XCTAssertEqual(stream.append(Data(frame.dropFirst(3))), [payload])
    }

    @MainActor
    func testRadioQueueIsBoundedAndCancelledWaiterDoesNotRemainQueued() async throws {
        let gate = ControllerExchangeGate(limit: 1)
        try await gate.acquire()
        let waiting = Task { try await gate.acquire(); gate.release() }
        for _ in 0..<20 { if gate.waitingCount == 1 { break }; await Task.yield() }
        XCTAssertEqual(gate.waitingCount, 1)
        do { try await gate.acquire(); XCTFail("The queue must reject requests beyond its bound") } catch {}
        waiting.cancel()
        do { try await waiting.value; XCTFail("A cancelled waiter must not acquire the radio") } catch {}
        XCTAssertEqual(gate.waitingCount, 0)
        gate.release()
        try await gate.acquire()
        gate.release()
    }
}
