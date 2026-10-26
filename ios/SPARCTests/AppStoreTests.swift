import XCTest
import Combine
@testable import SPARC

private enum MockFailure: Error { case unavailable }

private actor MockControllerClient: ControllerClient {
    var snapshot: ChargeSnapshot
    var settings: ControllerSettings
    var failedOperations: Set<String>
    func setFailedOperations(_ operations: Set<String>) { failedOperations = operations }

    init(snapshot: ChargeSnapshot = .demo(), settings: ControllerSettings = ControllerSettings(weekly: .default, today: nil),
         failedOperations: Set<String> = []) {
        self.snapshot = snapshot
        self.settings = settings
        self.failedOperations = failedOperations
    }
    func fetchSnapshot() async throws -> ChargeSnapshot {
        if failedOperations.contains("snapshot") { throw MockFailure.unavailable }
        return snapshot
    }
    func fetchSettings() async throws -> ControllerSettings {
        if failedOperations.contains("settings") { throw MockFailure.unavailable }
        return settings
    }
    func saveWeekly(_ value: WeeklySchedule) async throws {
        if failedOperations.contains("save") { throw MockFailure.unavailable }
        settings.weekly = value
    }
    func saveToday(_ value: TodayOverride?) async throws {
        if failedOperations.contains("save") { throw MockFailure.unavailable }
        settings.today = value
    }
    func command(_ command: String) async throws {
        if failedOperations.contains("command") { throw MockFailure.unavailable }
        snapshot.state = .charging
        snapshot.powerKW = 11.5
    }
}

final class AppStoreTests: XCTestCase {
    private func isolatedDefaults() -> (UserDefaults, String) {
        let name = "SPARC.Tests.\(UUID().uuidString)"
        return (UserDefaults(suiteName: name)!, name)
    }

    @MainActor
    func testRepeatedUnchangedDemoRefreshDoesNotPublishTelemetryUpdates() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let now = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        let store = AppStore(defaults: defaults, client: MockControllerClient(), now: { now }, arguments: [])
        let initialSnapshot = store.snapshot
        var publicationCount = 0
        let observation = store.objectWillChange.sink { publicationCount += 1 }
        defer { observation.cancel() }
        for _ in 0..<3 { await store.refresh() }
        XCTAssertEqual(publicationCount, 0, "An unchanged polling refresh must not invalidate the interface")
        XCTAssertEqual(store.snapshot, initialSnapshot)
    }

    @MainActor
    func testFixedLateEveningStartBelongsToNightBeforeMorningDeparture() async throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let now = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        let store = AppStore(defaults: defaults, client: MockControllerClient(), now: { now }, arguments: [])
        var weekly = WeeklySchedule.default
        weekly.mode = .fixed
        weekly.fixedStartMinutes = 23 * 60
        await store.saveWeekly(weekly)
        let goal = try XCTUnwrap(store.effectiveGoal)
        XCTAssertEqual(goal.departure, ISO8601DateFormatter().date(from: "2026-10-27T12:00:00Z"))
        XCTAssertEqual(store.snapshot.windowStart, ISO8601DateFormatter().date(from: "2026-10-27T04:00:00Z"))
        XCTAssertLessThanOrEqual(store.snapshot.windowEnd, goal.readyBy)
    }

    @MainActor
    func testEarlyOneTimeDepartureKeepsDemoWindowBeforeReadyBuffer() async throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let now = ISO8601DateFormatter().date(from: "2026-10-27T00:00:00Z")!
        let store = AppStore(defaults: defaults, client: MockControllerClient(), now: { now }, arguments: [])
        let departure = ISO8601DateFormatter().date(from: "2026-10-27T06:00:00Z")!
        await store.saveToday(target: 80, departure: departure)
        let goal = try XCTUnwrap(store.effectiveGoal)
        XCTAssertGreaterThanOrEqual(store.snapshot.windowStart, now)
        XCTAssertGreaterThan(store.snapshot.windowEnd, store.snapshot.windowStart)
        XCTAssertLessThanOrEqual(store.snapshot.windowEnd, goal.readyBy)
    }

    @MainActor
    func testDisabledDemoRoutineRemovesScheduledDeadlineAndWindow() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        var weekly = WeeklySchedule.default
        weekly.enabled = false
        await store.saveWeekly(weekly)
        XCTAssertNil(store.effectiveGoal)
        XCTAssertEqual(store.displayedReadyTime, "Not scheduled")
        XCTAssertEqual(store.displayedWindow, "No charging window scheduled")
        await store.chargeNow()
        XCTAssertEqual(store.snapshot.state, .charging)
    }

    @MainActor
    func testUnconfirmedAcceptedCommandClearsLiveTelemetryAndCanRecover() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let client = MockControllerClient()
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertTrue(store.hasLiveTelemetry)
        await client.setFailedOperations(["snapshot"])
        await store.chargeNow()
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertEqual(store.snapshot.powerKW, 0)
        XCTAssertNotNil(store.connectionMessage)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.busy)
        await client.setFailedOperations([])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .charging)
        XCTAssertTrue(store.hasLiveTelemetry)
        XCTAssertNil(store.connectionMessage)
    }

    @MainActor
    func testFreshDemoLaunchAlwaysWaitsAndKeepsSavedRoutine() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient()
        let first = AppStore(defaults: defaults, client: client, arguments: [])
        var changed = WeeklySchedule.default
        changed.targetPercent = 90
        await first.saveWeekly(changed)
        await first.chargeNow()
        XCTAssertEqual(first.snapshot.state, .charging)
        let relaunched = AppStore(defaults: defaults, client: client, arguments: [])
        XCTAssertEqual(relaunched.snapshot.state, .waiting)
        XCTAssertEqual(relaunched.snapshot.powerKW, 0)
        XCTAssertEqual(relaunched.weekly.targetPercent, 90)
    }

    @MainActor
    func testTodayOverrideExpiresWithoutRewritingSavedWeeklySchedule() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        var clock = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        let store = AppStore(defaults: defaults, client: MockControllerClient(), now: { clock }, arguments: [])
        var weekly = WeeklySchedule.default
        weekly.targetPercent = 85
        await store.saveWeekly(weekly)
        let departure = clock.addingTimeInterval(3600)
        await store.saveToday(target: 95, departure: departure)
        XCTAssertEqual(store.snapshot.targetPercent, 95)
        XCTAssertEqual(store.weekly, weekly)
        clock = departure
        await store.refresh()
        XCTAssertNil(store.today)
        XCTAssertEqual(store.snapshot.targetPercent, 85)
        XCTAssertEqual(store.weekly, weekly)
        let relaunched = AppStore(defaults: defaults, client: MockControllerClient(), now: { clock }, arguments: [])
        XCTAssertNil(relaunched.today)
        XCTAssertEqual(relaunched.weekly, weekly)
    }

    @MainActor
    func testLiveModeStartsWithNoDemoMetrics() {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        XCTAssertFalse(store.isDemo)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertEqual(store.snapshot.batteryPercent, 0)
        XCTAssertEqual(store.snapshot.monthlySavings, 0)
        XCTAssertEqual(store.snapshot.currentRateCents, 0)
        XCTAssertTrue(store.records.isEmpty)
    }

    @MainActor
    func testFailedLiveCommandDoesNotOptimisticallyChangeChargingState() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults,
            client: MockControllerClient(failedOperations: ["command"]), arguments: [])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .waiting)
        await store.chargeNow()
        XCTAssertEqual(store.snapshot.state, .waiting)
        XCTAssertEqual(store.snapshot.powerKW, 0)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.busy)
    }

    @MainActor
    func testFailedLiveSettingsSaveLeavesOldRoutine() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults,
            client: MockControllerClient(failedOperations: ["save"]), arguments: [])
        var changed = WeeklySchedule.default
        changed.targetPercent = 95
        await store.saveWeekly(changed)
        XCTAssertEqual(store.weekly, .default)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.busy)
    }

    @MainActor
    func testFailedLiveRefreshDoesNotShowSampleDataAsCurrent() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults,
            client: MockControllerClient(failedOperations: ["settings"]), arguments: [])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertTrue(store.records.isEmpty)
        XCTAssertEqual(store.weekly, .default)
        XCTAssertEqual(store.snapshot.monthlySavings, 0)
        XCTAssertNotNil(store.connectionMessage)
        XCTAssertNil(store.errorMessage)
    }

    @MainActor
    func testControllerSettingsLoadEvenWhenVehicleTelemetryIsUnavailable() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let now = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        var weekly = WeeklySchedule.default
        weekly.targetPercent = 85
        let today = TodayOverride(targetPercent: 95, departure: now.addingTimeInterval(3600))
        let client = MockControllerClient(settings: ControllerSettings(weekly: weekly, today: today),
                                          failedOperations: ["snapshot"])
        let store = AppStore(defaults: defaults, client: client, now: { now }, arguments: [])
        await store.refresh()
        XCTAssertEqual(store.weekly, weekly)
        XCTAssertEqual(store.today, today)
        XCTAssertEqual(store.displayedTarget, "95%")
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertTrue(store.records.isEmpty)
        XCTAssertNotNil(store.connectionMessage)
    }

    @MainActor
    func testMalformedStoredSettingsFallBackToDefaults() throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        var bad = WeeklySchedule.default
        bad.targetPercent = 150
        defaults.set(try JSONEncoder().encode(ControllerSettings(weekly: bad, today: nil)), forKey: "sparc.demo.settings.v1")
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        XCTAssertEqual(store.weekly, .default)
        XCTAssertEqual(store.snapshot.targetPercent, 80)
    }

    @MainActor
    func testMalformedStoredOverrideIsDiscarded() throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let invalid = TodayOverride(targetPercent: 101, departure: .now.addingTimeInterval(3600))
        defaults.set(try JSONEncoder().encode(ControllerSettings(weekly: .default, today: invalid)), forKey: "sparc.demo.settings.v1")
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        XCTAssertNil(store.today)
        XCTAssertEqual(store.snapshot.targetPercent, 80)
    }
}
