import XCTest
@testable import SPARC

private func historyDate(_ value: String) -> Date { ISO8601DateFormatter().date(from: value)! }

private func historySession(_ id: UUID = UUID(), cost: Double? = 2, baseline: Double? = 1,
                            savings: Double? = -1) -> HistorySession {
    HistorySession(id: id, date: historyDate("2026-11-06T04:00:00Z"),
                   start: historyDate("2026-11-06T04:00:00Z"), departure: historyDate("2026-11-06T13:00:00Z"),
                   nightDate: "2026-11-05", status: "complete", initialBatteryPercent: 20,
                   targetBatteryPercent: 80, batteryPercent: 80, energyKWh: 60 / 0.9,
                   costUSD: cost, baselineCostUSD: baseline, savingsUSD: savings,
                   savingsPercent: savings.map { $0 * 100 }, costFinal: cost != nil,
                   simulated: true, source: "ComEd", targetReachedByDeadline: true)
}

private func historyPage(_ sessions: [HistorySession], next: Int? = nil) -> ControllerHistoryPage {
    let days = (2...8).map { day in
        HistoryDay(date: "2026-11-0\(day)", energyKWh: day == 5 ? 60 / 0.9 : 0,
                   costUSD: day == 5 ? 2 : 0, baselineCostUSD: day == 5 ? 1 : 0,
                   savingsUSD: day == 5 ? -1 : 0, sessionCount: day == 5 ? 1 : 0)
    }
    return ControllerHistoryPage(schemaVersion: 1, generatedAt: historyDate("2026-11-06T14:00:00Z"),
                                 timezone: "America/Chicago", costScope: "electricity_supply",
                                 benchmark: "immediate_same_energy",
                                 week: HistoryWeek(start: "2026-11-02", end: "2026-11-09", energyKWh: 60 / 0.9,
                                                   costUSD: 2, baselineCostUSD: 1, savingsUSD: -1,
                                                   savingsPercent: -100, pricedSessions: 1, unpricedSessions: 0,
                                                   provisional: false, days: days),
                                 sessions: sessions, nextCursor: next)
}

private func teslaSession(_ id: UUID = UUID(), vehicle: String = "123", energy: Double? = 7.5,
                          cost: Double? = 0.237, ended: Bool = true) -> HistorySession {
    let end: Date? = ended ? historyDate("2026-11-06T05:15:20Z") : nil
    return HistorySession(id: id, date: historyDate("2026-11-06T04:00:20Z"), start: historyDate("2026-11-06T04:00:20Z"),
                          departure: nil, nightDate: "2026-11-05", status: ended ? "complete" : "active",
                          initialBatteryPercent: 40, targetBatteryPercent: 80, batteryPercent: 80, energyKWh: energy,
                          costUSD: cost, baselineCostUSD: nil, savingsUSD: nil, savingsPercent: nil, costFinal: cost != nil,
                          simulated: false, source: "ComEd", targetReachedByDeadline: nil, provisional: !ended,
                          unpricedEnergyKWh: energy.map { _ in 0 }, baselineFinal: nil, car: .tesla(vehicle),
                          vehicleName: "Actual Fleet vehicle", end: end, endReason: ended ? "limit_reached" : nil,
                          lastObservedAt: end, energySource: "vehicle_reported", energyPartial: false)
}

private func teslaPage(_ sessions: [HistorySession], vehicle: String = "123", next: Int? = nil) -> ControllerHistoryPage {
    var page = historyPage(sessions, next: next)
    page.benchmark = "none"; page.car = .tesla(vehicle)
    page.week.costUSD = 0.237; page.week.energyKWh = 7.5
    page.week.baselineCostUSD = nil; page.week.savingsUSD = nil; page.week.savingsPercent = nil
    return page
}

private func teslaConnections(vehicle: String, revision: Int = 1) -> ControllerConnections {
    ControllerConnections(revision: revision, mode: .tesla, activeMode: .virtual,
        virtual: .init(model: .cybertruck, batteryPercent: 20, pluggedIn: true),
        smartPlug: .init(address: nil, vehicleSource: .virtual, status: "not_configured"),
        tesla: .init(configured: true, authorized: true, status: "connected", vehicleName: "Actual Fleet vehicle",
                     error: nil, vehicleId: vehicle, vehicles: [], keyPairingURL: nil, commandsReady: true, chargeState: nil))
}

private func historyCampaign() -> CampaignSchedule {
    CampaignSchedule(campaignId: "campaign-2026-11-05", startDate: "2026-11-05", nights: 7,
                     startMinutes: 1320, departureMinutes: 420, initialBatteryPercent: 20,
                     targetBatteryPercent: 80, screenSleepMinutes: 5, strategy: "day_ahead_live",
                     timezone: "America/Chicago", enabled: true, revision: 4,
                     firstStart: historyDate("2026-11-06T04:00:00Z"), finalDeparture: historyDate("2026-11-12T13:00:00Z"),
                     currentSessionId: "campaign-2026-11-05:2026-11-05", phase: "running", nextStart: nil,
                     current: CampaignCurrentSession(start: historyDate("2026-11-06T04:00:00Z"),
                                                     departure: historyDate("2026-11-06T13:00:00Z"),
                                                     initialBatteryPercent: 20, targetBatteryPercent: 80))
}

private actor HistoryClient: ControllerClient {
    var offline = false
    var rejectHistory = false
    var pages: [Int: ControllerHistoryPage]
    var cursors: [Int] = []
    var savedCampaign: CampaignUpdate?
    var settings = ControllerSettings(weekly: .default, today: nil, campaign: historyCampaign())
    init(pages: [Int: ControllerHistoryPage]) { self.pages = pages }
    func setOffline(_ value: Bool) { offline = value }
    func failHistory() { rejectHistory = true }
    func fetchSettings() async throws -> ControllerSettings {
        if offline { throw ControllerConnectionError.timeout }
        return settings
    }
    func fetchSnapshot() async throws -> ChargeSnapshot {
        if offline { throw ControllerConnectionError.timeout }
        return .demo(now: historyDate("2026-11-06T05:00:00Z"))
    }
    func fetchHistory(cursor: Int?) async throws -> ControllerHistoryPage {
        if offline || rejectHistory { throw ControllerConnectionError.timeout }
        cursors.append(cursor ?? 0)
        return pages[cursor ?? 0]!
    }
    var teslaPages: [Int: ControllerHistoryPage] = [:]
    var teslaRequests: [String] = []
    var teslaError: String?
    func setTesla(pages: [Int: ControllerHistoryPage], connections: ControllerConnections?) {
        teslaPages = pages; settings.connections = connections
    }
    func rejectTesla(_ message: String) { teslaError = message }
    func fetchTeslaHistory(vehicleId: String, cursor: Int?) async throws -> ControllerHistoryPage {
        if offline { throw ControllerConnectionError.timeout }
        if let teslaError { throw ControllerConnectionError.unavailable(teslaError) }
        teslaRequests.append("\(vehicleId)@\(cursor ?? 0)")
        return teslaPages[cursor ?? 0]!
    }
    func saveCampaign(_ value: CampaignUpdate) async throws {
        savedCampaign = value
        if offline { throw ControllerConnectionError.timeout }
        guard settings.campaign?.revision == value.expectedRevision else {
            throw ControllerConnectionError.unavailable("The schedule changed. Refresh and review it.")
        }
        settings.campaign?.revision += 1
        settings.campaign?.targetBatteryPercent = value.targetBatteryPercent
        settings.campaign?.enabled = value.enabled
    }
    func saveWeekly(_ value: WeeklySchedule) async throws {}
    func saveToday(_ value: TodayOverride?) async throws {}
    func command(_ command: String) async throws {}
}

private struct HistoryCredentials: ControllerCredentialStore {
    let controller: PairedController
    func load() throws -> PairedController? { controller }
    func save(_ controller: PairedController) throws {}
    func clear() throws {}
}

private actor HistoryBluetooth: BluetoothControllerTransport {
    var requests: [(String, [String: WireValue])] = []
    func exchangeBLE(controller: PairedController, operation: String,
                     body: [String: WireValue]) async throws -> [String: WireValue] {
        requests.append((operation, body))
        if operation == "get_history" { return try WireValue.dictionary(historyPage([historySession()])) }
        return [:]
    }
}

final class ControllerHistoryTests: XCTestCase {
    func testSavedBluetoothRouteCarriesHistoryCursorAndCampaignRevision() async throws {
        let controller = PairedController(deviceID: UUID().uuidString, deviceName: "Test Pi", host: "sparc.local",
                                          setupKey: String(repeating: "0", count: 64), bluetoothPeripheralID: UUID().uuidString)
        let bluetooth = HistoryBluetooth(), lan = HistoryClient(pages: [:])
        let router = ControllerClientRouter(vault: HistoryCredentials(controller: controller), bluetooth: bluetooth, lan: lan)
        _ = try await router.fetchHistory(cursor: 8)
        try await router.saveCampaign(CampaignUpdate(historyCampaign()))
        let requests = await bluetooth.requests
        XCTAssertEqual(requests.map { $0.0 }, ["get_history", "set_campaign"])
        XCTAssertEqual(requests[0].1["cursor"], .number(8))
        XCTAssertEqual(requests[0].1["limit"], .number(4))
        XCTAssertNil(requests[0].1["car"], "Older controllers reject unknown fields; the virtual car is the default")
        XCTAssertEqual(requests[1].1["expectedRevision"], .number(4))
        let lanCursors = await lan.cursors
        XCTAssertTrue(lanCursors.isEmpty)
    }

    func testTeslaHistoryRequestNamesTheCarOverBluetooth() async throws {
        let controller = PairedController(deviceID: UUID().uuidString, deviceName: "Test Pi", host: "sparc.local",
                                          setupKey: String(repeating: "0", count: 64), bluetoothPeripheralID: UUID().uuidString)
        let bluetooth = HistoryBluetooth(), lan = HistoryClient(pages: [:])
        let router = ControllerClientRouter(vault: HistoryCredentials(controller: controller), bluetooth: bluetooth, lan: lan)
        _ = try await router.fetchTeslaHistory(vehicleId: "123", cursor: 4)
        let requests = await bluetooth.requests
        XCTAssertEqual(requests.map { $0.0 }, ["get_history"])
        XCTAssertEqual(requests[0].1, ["limit": .number(4), "cursor": .number(4), "car": .string("tesla"), "vehicleId": .string("123")])
        XCTAssertEqual(LocalControllerClient.historyBody(limit: 8, cursor: nil, teslaVehicleId: "9"),
                       ["limit": .number(8), "car": .string("tesla"), "vehicleId": .string("9")])
    }

    /// Verbatim output of the Pi's `get_history {car:"tesla", vehicleId:"123"}` (tesla_history.py):
    /// one charge that reached its limit, and one still open whose car did not report its energy counter.
    func testControllerTeslaPageDecodesAndValidatesForThatCarOnly() throws {
        let json = """
        {"benchmark":"none","car":{"kind":"tesla","vehicleId":"123"},"costScope":"electricity_supply","generatedAt":"2026-11-10T05:45:20Z","nextCursor":null,"schemaVersion":1,"timezone":"America/Chicago",
         "week":{"baselineCostUSD":null,"costUSD":null,"end":"2026-11-16","energyKWh":7.5,"pricedSessions":1,"provisional":true,"savingsPercent":null,"savingsUSD":null,"start":"2026-11-09","unpricedSessions":1,"days":[
           {"baselineCostUSD":null,"costUSD":null,"date":"2026-11-09","energyKWh":7.5,"savingsUSD":null,"sessionCount":2},
           {"baselineCostUSD":0,"costUSD":0,"date":"2026-11-10","energyKWh":0,"savingsUSD":0,"sessionCount":0},
           {"baselineCostUSD":0,"costUSD":0,"date":"2026-11-11","energyKWh":0,"savingsUSD":0,"sessionCount":0},
           {"baselineCostUSD":0,"costUSD":0,"date":"2026-11-12","energyKWh":0,"savingsUSD":0,"sessionCount":0},
           {"baselineCostUSD":0,"costUSD":0,"date":"2026-11-13","energyKWh":0,"savingsUSD":0,"sessionCount":0},
           {"baselineCostUSD":0,"costUSD":0,"date":"2026-11-14","energyKWh":0,"savingsUSD":0,"sessionCount":0},
           {"baselineCostUSD":0,"costUSD":0,"date":"2026-11-15","energyKWh":0,"savingsUSD":0,"sessionCount":0}]},
         "sessions":[
          {"baselineCostUSD":null,"baselineFinal":null,"batteryPercent":64.0,"car":{"kind":"tesla","vehicleId":"123"},"costFinal":false,"costUSD":null,"date":"2026-11-10T05:30:20Z","departure":null,"end":null,"endReason":null,"energyKWh":null,"energyPartial":false,"energySource":"vehicle_reported","id":"44a18c3c-8ca5-4829-8773-fe1f1f75f290","initialBatteryPercent":60.0,"lastObservedAt":"2026-11-10T05:45:20Z","nightDate":"2026-11-09","provisional":true,"savingsPercent":null,"savingsUSD":null,"simulated":false,"source":"ComEd","start":"2026-11-10T05:30:20Z","status":"active","targetBatteryPercent":null,"targetReachedByDeadline":null,"unpricedEnergyKWh":null,"vehicleName":"Actual Fleet vehicle"},
          {"baselineCostUSD":null,"baselineFinal":null,"batteryPercent":80.0,"car":{"kind":"tesla","vehicleId":"123"},"costFinal":true,"costUSD":0.2378,"date":"2026-11-10T04:00:20Z","departure":null,"end":"2026-11-10T05:15:20Z","endReason":"limit_reached","energyKWh":7.5,"energyPartial":false,"energySource":"vehicle_reported","id":"b6ea254e-facd-48a1-887e-24d8c1823295","initialBatteryPercent":40.0,"lastObservedAt":"2026-11-10T05:15:20Z","nightDate":"2026-11-09","provisional":false,"savingsPercent":null,"savingsUSD":null,"simulated":false,"source":"ComEd","start":"2026-11-10T04:00:20Z","status":"complete","targetBatteryPercent":80.0,"targetReachedByDeadline":null,"unpricedEnergyKWh":0.0,"vehicleName":"Actual Fleet vehicle"}]}
        """
        let page = try ControllerJSON.decoder.decode(ControllerHistoryPage.self, from: Data(json.utf8))
        try page.validate(teslaVehicleId: "123")
        XCTAssertThrowsError(try page.validate(), "A Tesla page is never accepted as the virtual car's history")
        XCTAssertThrowsError(try page.validate(teslaVehicleId: "456"), "Each Tesla keeps its own history")
        let active = page.sessions[0], done = page.sessions[1]
        XCTAssertNil(active.energyKWh); XCTAssertNil(active.targetBatteryPercent); XCTAssertNil(active.departure)
        XCTAssertTrue(active.isProvisional)
        XCTAssertFalse(done.isProvisional, "A settled Tesla charge has no baseline to wait for")
        XCTAssertEqual(done.energyKWh, 7.5); XCTAssertEqual(done.costUSD, 0.2378)
        XCTAssertEqual(done.endReason, "limit_reached")
        XCTAssertNil(done.pricedRecord, "Manual Tesla charges never enter smart-charging savings")
    }

    func testPagesThatMixCarsOrClaimTeslaSavingsAreRejected() throws {
        try teslaPage([teslaSession()]).validate(teslaVehicleId: "123")
        try historyPage([historySession()]).validate()
        var virtualWithCar = historyPage([historySession()]); virtualWithCar.car = .virtual
        virtualWithCar.sessions[0].car = .virtual
        try virtualWithCar.validate()
        XCTAssertThrowsError(try historyPage([historySession()]).validate(teslaVehicleId: "123"))
        XCTAssertThrowsError(try historyPage([teslaSession()]).validate(), "A Tesla charge never shows under the virtual car")
        XCTAssertThrowsError(try teslaPage([historySession()]).validate(teslaVehicleId: "123"))
        XCTAssertThrowsError(try teslaPage([teslaSession(vehicle: "456")]).validate(teslaVehicleId: "123"))
        var tampered = teslaSession(); tampered.savingsUSD = 1
        XCTAssertThrowsError(try tampered.validate())
        tampered = teslaSession(); tampered.simulated = true
        XCTAssertThrowsError(try tampered.validate())
        tampered = teslaSession(); tampered.departure = historyDate("2026-11-06T13:00:00Z")
        XCTAssertThrowsError(try tampered.validate())
        tampered = teslaSession(energy: nil, cost: 1)
        XCTAssertThrowsError(try tampered.validate(), "A price without measured energy is invented")
        tampered = teslaSession(ended: false); tampered.end = historyDate("2026-11-06T05:00:00Z")
        XCTAssertThrowsError(try tampered.validate())
        tampered = teslaSession(); tampered.endReason = "exploded"
        XCTAssertThrowsError(try tampered.validate())
        var virtualSession = historySession(); virtualSession.departure = nil
        XCTAssertThrowsError(try virtualSession.validate(), "The virtual car's sessions keep their deadline")
    }

    @MainActor
    func testTeslaHistoryLoadsForTheSelectedTeslaAndStaysApartFromVirtualHistory() async throws {
        let name = "SPARC.History.\(UUID())"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let virtual = historySession(), charge = teslaSession()
        let client = HistoryClient(pages: [0: historyPage([virtual])])
        await client.setTesla(pages: [0: teslaPage([charge])], connections: teslaConnections(vehicle: "123"))
        let store = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        await store.refresh()
        XCTAssertNil(store.selectedTeslaHistory)
        await store.refreshTeslaHistory()
        XCTAssertEqual(store.historySessions.map(\.id), [virtual.id])
        XCTAssertEqual(store.selectedTeslaHistory?.sessions.map(\.id), [charge.id])
        XCTAssertEqual(store.selectedTeslaHistory?.week.costUSD, 0.237)
        XCTAssertFalse(store.teslaHistoryIsStale)
        XCTAssertEqual(store.records.map(\.id), [virtual.id], "Insights use only the virtual car's smart charges")
        let requests = await client.teslaRequests
        XCTAssertEqual(requests, ["123@0"])

        let relaunched = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        XCTAssertEqual(relaunched.selectedTeslaHistory?.sessions.map(\.id), [charge.id])
        XCTAssertTrue(relaunched.teslaHistoryIsStale)
        XCTAssertEqual(relaunched.historySessions.map(\.id), [virtual.id])
        let otherPi = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-b" })
        XCTAssertNil(otherPi.selectedTeslaHistory)

        await client.setTesla(pages: [0: teslaPage([], vehicle: "456")], connections: teslaConnections(vehicle: "456", revision: 2))
        await store.refresh()
        XCTAssertNil(store.selectedTeslaHistory, "The first Tesla's charges never show under another Tesla")
        await store.refreshTeslaHistory()
        XCTAssertEqual(store.selectedTeslaHistory?.vehicleId, "456")
        XCTAssertEqual(store.selectedTeslaHistory?.sessions.count, 0)
    }

    @MainActor
    func testRejectedOrOlderControllerTeslaHistoryKeepsSavedChargesAndExplainsTheUpdate() async throws {
        let defaults = UserDefaults(suiteName: "SPARC.History.\(UUID())")!
        defaults.set(false, forKey: "sparc.demoMode")
        let charge = teslaSession()
        let client = HistoryClient(pages: [0: historyPage([])])
        await client.setTesla(pages: [0: teslaPage([charge])], connections: teslaConnections(vehicle: "123"))
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await store.refreshTeslaHistory()
        await client.setTesla(pages: [0: teslaPage([teslaSession(vehicle: "999")])], connections: teslaConnections(vehicle: "123"))
        await store.refreshTeslaHistory()
        XCTAssertTrue(store.teslaHistoryIsStale)
        XCTAssertEqual(store.selectedTeslaHistory?.sessions.map(\.id), [charge.id])
        await client.rejectTesla("Choose a valid history page.")
        await store.refreshTeslaHistory()
        XCTAssertEqual(store.teslaHistoryMessage, LocalControllerClient.teslaHistoryUpdateMessage)
        XCTAssertFalse(store.historyIsStale, "A Tesla history failure leaves the virtual history alone")
    }
    func testNullableAndNegativeSupplyAmountsSurviveWireDecoding() throws {
        let expected = historyPage([historySession(), historySession(cost: nil, baseline: nil, savings: nil)])
        let decoded = try ControllerJSON.decoder.decode(ControllerHistoryPage.self,
                                                        from: ControllerJSON.encoder.encode(expected))
        try decoded.validate()
        XCTAssertEqual(decoded.sessions[0].savingsUSD, -1)
        XCTAssertNil(decoded.sessions[1].costUSD)
        XCTAssertNil(decoded.sessions[1].pricedRecord)
        XCTAssertEqual(decoded.week.days.count, 7)
    }

    func testDailySavingsPercentagePreservesNegativeNightsWithoutChangingHistorySchema() throws {
        let day = HistoryDay(date: "2026-11-05", energyKWh: 30, costUSD: 2.01,
                             baselineCostUSD: 2, savingsUSD: -0.01, sessionCount: 1)
        XCTAssertEqual(try XCTUnwrap(day.savingsPercent), -0.5, accuracy: 0.000001)
        let data = try ControllerJSON.encoder.encode(day)
        let encoded = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertNil(encoded["savingsPercent"], "The derived percentage must not change the controller/cache schema")
        let decoded = try ControllerJSON.decoder.decode(HistoryDay.self, from: data)
        XCTAssertEqual(decoded, day)
        XCTAssertEqual(try XCTUnwrap(decoded.savingsPercent), -0.5, accuracy: 0.000001)
    }

    func testDailyPercentageDoesNotTurnNoChargingMissingPricesOrZeroBaselineIntoSavings() {
        var day = HistoryDay(date: "2026-11-05", energyKWh: 30, costUSD: nil,
                             baselineCostUSD: nil, savingsUSD: nil, sessionCount: 1)
        XCTAssertNil(day.savingsPercent)
        day.costUSD = 0; day.baselineCostUSD = 0; day.savingsUSD = 0
        XCTAssertNil(day.savingsPercent)
        day.baselineCostUSD = 2; day.savingsUSD = 1; day.sessionCount = 0
        XCTAssertNil(day.savingsPercent)
    }

    func testNegativePriceBaselineUsesSameAbsoluteDenominatorAsController() throws {
        let day = HistoryDay(date: "2026-11-05", energyKWh: 30, costUSD: -0.6,
                             baselineCostUSD: -0.4, savingsUSD: 0.2, sessionCount: 1)
        XCTAssertEqual(try XCTUnwrap(day.savingsPercent), 50, accuracy: 0.000001)
    }

    @MainActor
    func testWeeklyPercentageKeepsWeightedControllerTotalInsteadOfAveragingDailyPercentages() async throws {
        let name = "SPARC.History.\(UUID())"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        var page = historyPage([])
        page.week.days[2] = HistoryDay(date: "2026-11-04", energyKWh: 30, costUSD: 0.9,
                                      baselineCostUSD: 1.2, savingsUSD: 0.3, sessionCount: 1)
        page.week.days[3] = HistoryDay(date: "2026-11-05", energyKWh: 30, costUSD: 2.01,
                                      baselineCostUSD: 2, savingsUSD: -0.01, sessionCount: 1)
        page.week.energyKWh = 60; page.week.costUSD = 2.91
        page.week.baselineCostUSD = 3.2; page.week.savingsUSD = 0.29
        page.week.savingsPercent = 0.29 / 3.2 * 100; page.week.pricedSessions = 2
        let client = HistoryClient(pages: [0: page])
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        let week = try XCTUnwrap(store.historyWeek)
        XCTAssertEqual(try XCTUnwrap(week.savingsPercent), 9.0625, accuracy: 0.000001)
        XCTAssertEqual(try XCTUnwrap(week.days[2].savingsPercent), 25, accuracy: 0.000001)
        XCTAssertEqual(try XCTUnwrap(week.days[3].savingsPercent), -0.5, accuracy: 0.000001)
        XCTAssertNotEqual(week.savingsPercent, (25 - 0.5) / 2)
    }

    func testDifferentCostScopeOrNonfiniteAmountsAreRejected() {
        var page = historyPage([historySession()])
        page.costScope = "whole_bill"
        XCTAssertThrowsError(try page.validate())
        page.costScope = "electricity_supply"
        page.sessions[0].energyKWh = .infinity
        XCTAssertThrowsError(try page.validate())
    }

    func testCampaignRequestContainsOnlyEditableFieldsAndRevision() throws {
        let value = CampaignUpdate(historyCampaign())
        try value.validate()
        let body = try WireValue.dictionary(value)
        XCTAssertEqual(body["expectedRevision"], .number(4))
        XCTAssertNil(body["current"])
        XCTAssertNil(body["campaignId"])
        XCTAssertEqual(body["departureMinutes"], .number(420))
        XCTAssertEqual(CampaignUpdate.dayString(CampaignUpdate.date("2026-11-05")!), "2026-11-05")
        var bad = value; bad.nights = 91
        XCTAssertThrowsError(try bad.validate())
    }

    @MainActor
    func testFinishedNightShowsNextCampaignDepartureInsteadOfDisabledWeeklyGoal() async {
        let defaults = UserDefaults(suiteName: "SPARC.History.\(UUID())")!
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults, client: HistoryInterfaceFixture(),
                             now: { HistoryInterfaceFixture.now }, arguments: [])
        await store.refresh()
        XCTAssertEqual(store.effectiveGoal?.departure, historyDate("2026-11-07T13:00:00Z"))
        XCTAssertEqual(store.campaignWindow?.start, historyDate("2026-11-07T04:00:00Z"))
        XCTAssertEqual(store.displayedReadyTime, "7:00 AM")
    }

    @MainActor
    func testLiveRefreshLoadsRecordsAndMissingPricesWithoutFakingZeros() async {
        let name = "SPARC.History.\(UUID())"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let priced = historySession(), pending = historySession(cost: nil, baseline: nil, savings: nil)
        let client = HistoryClient(pages: [0: historyPage([priced, pending])])
        let store = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        await store.refresh()
        XCTAssertEqual(store.historySessions.count, 2)
        XCTAssertEqual(store.records.count, 1)
        XCTAssertEqual(store.records.first?.savings, -1)
        XCTAssertFalse(store.historyIsStale)
        XCTAssertEqual(store.campaign?.nights, 7)
    }

    @MainActor
    func testOfflineRefreshAndAppRestartKeepCachedResultsAndMarkThemStale() async {
        let name = "SPARC.History.\(UUID())"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let client = HistoryClient(pages: [0: historyPage([historySession()])])
        let store = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        await store.refresh()
        await client.setOffline(true)
        await store.refresh()
        XCTAssertEqual(store.historySessions.count, 1)
        XCTAssertTrue(store.historyIsStale)
        XCTAssertEqual(store.snapshot.state, .offline)
        let relaunched = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        XCTAssertEqual(relaunched.historySessions, store.historySessions)
        XCTAssertTrue(relaunched.historyIsStale)
        XCTAssertTrue(relaunched.campaignIsStale)
        let differentPi = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-b" })
        XCTAssertTrue(differentPi.historySessions.isEmpty)
        XCTAssertNil(differentPi.campaign)
    }

    @MainActor
    func testHistoryFailureDoesNotDisconnectOtherwiseConfirmedTelemetry() async {
        let defaults = UserDefaults(suiteName: "SPARC.History.\(UUID())")!
        defaults.set(false, forKey: "sparc.demoMode")
        let client = HistoryClient(pages: [0: historyPage([])])
        await client.failHistory()
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertNotEqual(store.snapshot.state, .offline)
        XCTAssertTrue(store.historyIsStale)
        XCTAssertNotNil(store.historyMessage)
        XCTAssertFalse(store.campaignIsStale)
    }

    @MainActor
    func testPaginationMergesStableIDsAndKeepsWholeWeekTotals() async {
        let defaults = UserDefaults(suiteName: "SPARC.History.\(UUID())")!
        defaults.set(false, forKey: "sparc.demoMode")
        let a = historySession(), b = historySession(), c = historySession()
        let client = HistoryClient(pages: [0: historyPage([a, b], next: 2), 2: historyPage([b, c])])
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await store.refreshHistory(loadMore: true)
        XCTAssertEqual(Set(store.historySessions.map(\.id)), Set([a.id, b.id, c.id]))
        XCTAssertEqual(store.historyWeek?.savingsUSD, -1)
        XCTAssertNil(store.historyNextCursor)
        let cursors = await client.cursors
        XCTAssertEqual(cursors, [0, 2])
    }

    @MainActor
    func testCampaignSaveUsesExpectedRevisionThenReadsCanonicalSettings() async throws {
        let defaults = UserDefaults(suiteName: "SPARC.History.\(UUID())")!
        defaults.set(false, forKey: "sparc.demoMode")
        let client = HistoryClient(pages: [0: historyPage([])])
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        var update = CampaignUpdate(try XCTUnwrap(store.campaign))
        update.targetBatteryPercent = 85
        await store.saveCampaign(update)
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(store.campaign?.revision, 5)
        XCTAssertEqual(store.campaign?.targetBatteryPercent, 85)
        let sent = await client.savedCampaign
        XCTAssertEqual(sent?.expectedRevision, 4)
        await store.saveCampaign(update)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertEqual(store.campaign?.revision, 5)
    }
}
