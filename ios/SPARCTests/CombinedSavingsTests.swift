import XCTest
@testable import SPARC

final class CombinedSavingsTests: XCTestCase {
    private func report() throws -> CombinedSavingsReport {
        let json = #"{"schemaVersion":1,"kind":"simulation","benchmark":"fixed_supply_standard_delivery","costScope":"electricity_supply_distribution_estimate","generatedAt":"2026-11-12T16:36:32.859628+00:00","start":"2026-10-28","end":"2026-11-11","deliveryClass":"singleFamilyElectric","assumptions":{"initialBatteryPercent":20,"targetBatteryPercent":80,"capacityKWh":100,"powerKW":11.5,"efficiency":0.9,"startMinutes":1320,"departureMinutes":420,"readyBufferMinutes":30},"fixedSupplyCents":8.413,"source":"ComEd","tariffSource":"https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/blt86ebee5fe6ed02f8/6ad911e1630c34d7f475f5cd/2026_Ratebook.pdf?branch=prod_alias","strategySourceCommit":"28d838d0d68ee175b56861622dbd0847a9cdced1","summary":{"energyKWh":933.3333333333334,"baselineCostUSD":107.57333333333332,"hourlyStandardCostUSD":49.080708333333334,"hourlyTODCostUSD":36.889375,"systemCostUSD":35.96216666603616,"savingsUSD":71.61116666729717,"hourlySavingsUSD":58.49262499999999,"deliverySavingsUSD":12.191333333333334,"schedulingSavingsUSD":0.9272083339638397,"nightCount":15,"pricedNightCount":14,"unpricedNightCount":1,"savingsPercent":66.56962692175617},"nights":[{"energyKWh":66.66666666666667,"baselineCostUSD":7.754,"hourlyStandardCostUSD":3.0968333333333335,"hourlyTODCostUSD":2.196166666666667,"systemCostUSD":2.2164583332920036,"savingsUSD":5.537541666707996,"savingsPercent":71.41529103311835,"hourlySavingsUSD":4.657166666666666,"deliverySavingsUSD":0.9006666666666665,"schedulingSavingsUSD":-0.020291666625336546,"priced":true,"id":"2026-10-28","date":"2026-10-28","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.754,"hourlyStandardCostUSD":3.8168333333333333,"hourlyTODCostUSD":2.9161666666666672,"systemCostUSD":2.699499999939597,"savingsUSD":5.054500000060402,"savingsPercent":65.18571060175913,"hourlySavingsUSD":3.9371666666666663,"deliverySavingsUSD":0.9006666666666661,"schedulingSavingsUSD":0.21666666672707002,"priced":true,"id":"2026-10-29","date":"2026-10-29","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.754,"hourlyStandardCostUSD":3.580166666666667,"hourlyTODCostUSD":2.6795,"systemCostUSD":2.5571249999682073,"savingsUSD":5.196875000031792,"savingsPercent":67.0218596857337,"hourlySavingsUSD":4.173833333333333,"deliverySavingsUSD":0.900666666666667,"schedulingSavingsUSD":0.12237500003179269,"priced":true,"id":"2026-10-30","date":"2026-10-30","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.695486666666666,"hourlyStandardCostUSD":null,"hourlyTODCostUSD":null,"systemCostUSD":null,"savingsUSD":null,"savingsPercent":null,"hourlySavingsUSD":null,"deliverySavingsUSD":null,"schedulingSavingsUSD":null,"priced":false,"id":"2026-10-31","date":"2026-10-31","ready":true,"simulated":true,"fallbackSlots":0,"note":"Missing historical billing prices"},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":3.934041666666667,"hourlyTODCostUSD":3.071375,"systemCostUSD":2.803583333279809,"savingsUSD":4.8610833333868575,"savingsPercent":63.42197964756272,"hourlySavingsUSD":3.7306249999999994,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":0.2677916667201914,"priced":true,"id":"2026-11-01","date":"2026-11-01","ready":true,"simulated":true,"fallbackSlots":12,"note":"Deadline-protection fallback used"},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":2.794,"hourlyTODCostUSD":1.9313333333333333,"systemCostUSD":1.9370833333012665,"savingsUSD":5.7275833333654,"savingsPercent":74.7271027228677,"hourlySavingsUSD":4.870666666666667,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":-0.005749999967933128,"priced":true,"id":"2026-11-02","date":"2026-11-02","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":2.564,"hourlyTODCostUSD":1.7013333333333334,"systemCostUSD":1.6797499999774697,"savingsUSD":5.984916666689196,"savingsPercent":78.08450030472119,"hourlySavingsUSD":5.100666666666666,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":0.02158333335586371,"priced":true,"id":"2026-11-03","date":"2026-11-03","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":2.718166666666667,"hourlyTODCostUSD":1.8555000000000001,"systemCostUSD":1.7602499999774694,"savingsUSD":5.904416666689197,"savingsPercent":77.03422632020349,"hourlySavingsUSD":4.9464999999999995,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":0.0952500000225307,"priced":true,"id":"2026-11-04","date":"2026-11-04","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":3.5778333333333334,"hourlyTODCostUSD":2.7151666666666667,"systemCostUSD":2.7707083332762323,"savingsUSD":4.893958333390434,"savingsPercent":63.850895886628265,"hourlySavingsUSD":4.086833333333333,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":-0.05554166660956561,"priced":true,"id":"2026-11-05","date":"2026-11-05","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":3.9043333333333337,"hourlyTODCostUSD":3.041666666666667,"systemCostUSD":3.069333333283385,"savingsUSD":4.595333333383281,"savingsPercent":59.95477081042813,"hourlySavingsUSD":3.7603333333333326,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":-0.02766666661671824,"priced":true,"id":"2026-11-06","date":"2026-11-06","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":3.0123333333333333,"hourlyTODCostUSD":2.1496666666666666,"systemCostUSD":1.9726666666322157,"savingsUSD":5.692000000034451,"savingsPercent":74.2628511790178,"hourlySavingsUSD":4.652333333333333,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":0.17700000003445093,"priced":true,"id":"2026-11-07","date":"2026-11-07","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":3.5246666666666666,"hourlyTODCostUSD":2.662,"systemCostUSD":2.6993333332941143,"savingsUSD":4.965333333372552,"savingsPercent":64.782117074531,"hourlySavingsUSD":4.14,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":-0.03733333329411437,"priced":true,"id":"2026-11-08","date":"2026-11-08","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":3.104166666666667,"hourlyTODCostUSD":2.2415000000000003,"systemCostUSD":2.2414999999560123,"savingsUSD":5.423166666710654,"savingsPercent":70.75541445651892,"hourlySavingsUSD":4.560499999999999,"deliverySavingsUSD":0.8626666666666667,"schedulingSavingsUSD":4.39879244140684e-11,"priced":true,"id":"2026-11-09","date":"2026-11-09","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":5.365500000000001,"hourlyTODCostUSD":4.502833333333333,"systemCostUSD":4.327041666582147,"savingsUSD":3.337625000084519,"savingsPercent":43.545598852977115,"hourlySavingsUSD":2.2991666666666655,"deliverySavingsUSD":0.8626666666666676,"schedulingSavingsUSD":0.17579166675118607,"priced":true,"id":"2026-11-10","date":"2026-11-10","ready":true,"simulated":true,"fallbackSlots":0,"note":null},{"energyKWh":66.66666666666667,"baselineCostUSD":7.664666666666666,"hourlyStandardCostUSD":4.087833333333334,"hourlyTODCostUSD":3.225166666666667,"systemCostUSD":3.227833333276233,"savingsUSD":4.4368333333904335,"savingsPercent":57.886840045974175,"hourlySavingsUSD":3.576833333333332,"deliverySavingsUSD":0.8626666666666671,"schedulingSavingsUSD":-0.0026666666095658265,"priced":true,"id":"2026-11-11","date":"2026-11-11","ready":true,"simulated":true,"fallbackSlots":0,"note":null}],"limitations":["Supply and distribution subtotal; capacity, other supply charges, taxes and fixed fees excluded.","One daylight-saving night has missing public billing prices and is excluded from priced totals.","Historical replay uses published-time guards and fits decay before each night; it cannot prove actual vehicle savings."]}"#
        return try ControllerJSON.decoder.decode(CombinedSavingsReport.self, from: Data(json.utf8))
    }
    func testControllerReplayShowsFourteenPricedNightsAndAnUnpricedDSTNight() throws {
        let value = try report(); try value.validate(kind: "simulation")
        XCTAssertEqual(value.nights.count,15); XCTAssertEqual(value.summary.pricedNightCount,14)
        XCTAssertEqual(value.summary.savingsUSD!,71.6111666673,accuracy:0.00001)
        XCTAssertEqual(value.summary.savingsPercent!,66.56962692,accuracy:0.00001)
        XCTAssertFalse(value.nights.first { $0.date == "2026-10-31" }!.priced)
    }
    func testCombinedComparisonPreservesSchedulingLossesAndRejectsDoubleCounting() throws {
        var value = try report()
        XCTAssertLessThan(value.nights[0].schedulingSavingsUSD!,0)
        value.summary.savingsUSD! += 1
        XCTAssertThrowsError(try value.validate(kind:"simulation"))
    }
    func testSummaryCannotDivergeFromRowsAndRecordedCannotMasqueradeAsSimulation() throws {
        var value = try report(); value.nights[0].energyKWh += 1
        XCTAssertThrowsError(try value.validate(kind:"simulation"))
        value = try report(); XCTAssertThrowsError(try value.validate(kind:"recorded"))
    }
    func testSavingsFollowTheChosenComparison() throws {
        var value = try report()
        let fixed = try XCTUnwrap(value.totals(.fixedPlan))
        XCTAssertEqual(fixed.savedUSD, value.summary.savingsUSD!, accuracy: 0.00001)
        XCTAssertEqual(fixed.parts.reduce(0, +), fixed.savedUSD, accuracy: 0.00001)
        XCTAssertEqual(fixed.nightCount, 14); XCTAssertEqual(fixed.unpricedNightCount, 1)
        XCTAssertEqual(fixed.savedPercent!, value.summary.savingsPercent!, accuracy: 0.00001)
        XCTAssertEqual(SPARCFormat.percent(fixed.savedPercent!), "67%")
        XCTAssertEqual(SPARCFormat.percent(-4.4), "4%")
        let plugIn = try XCTUnwrap(value.totals(.hourlyAtPlugIn))
        XCTAssertEqual(plugIn.baselineUSD, 49.080708333333334, accuracy: 0.00001)
        XCTAssertEqual(plugIn.parts[0], value.summary.deliverySavingsUSD!, accuracy: 0.00001)
        XCTAssertEqual(plugIn.parts[1], value.summary.schedulingSavingsUSD!, accuracy: 0.00001)
        XCTAssertNil(value.totals(.hourlyAt6PM))
        value.nights[0].at6PM = .init(hourlyStandardCostUSD: 4.387333, hourlyTODCostUSD: 4.083172)
        let six = try XCTUnwrap(value.totals(.hourlyAt6PM))
        XCTAssertEqual(six.nightCount, 1); XCTAssertEqual(six.unpricedNightCount, 14)
        XCTAssertEqual(six.savedUSD, 4.387333 - 2.2164583332920036, accuracy: 1e-9)
        XCTAssertEqual(six.parts.reduce(0, +), six.savedUSD, accuracy: 1e-9)
        XCTAssertEqual(value.nights[0].saved(.hourlyAt6PM)!, six.savedUSD, accuracy: 1e-9)
        try value.validate(kind: "simulation")
        value.nights[0].at6PM = .init(hourlyStandardCostUSD: .nan, hourlyTODCostUSD: 1)
        XCTAssertThrowsError(try value.validate(kind: "simulation"))
    }
    func testSleepWindowsUseAbsoluteEventTimesAndRejectOverlapsAndExpiredEvents() throws {
        let now = Date(timeIntervalSince1970:1794506400)
        let w = SleepWindow(id:"sleep_one",start:now.addingTimeInterval(3600),departure:now.addingTimeInterval(9*3600))
        try SleepWindowUpdate(enabled:true,windows:[w],targetPercent:80).validate(now:now)
        var other=w;other.id="sleep_two"
        XCTAssertThrowsError(try SleepWindowUpdate(enabled:true,windows:[w,other],targetPercent:80).validate(now:now))
        XCTAssertThrowsError(try w.validate(now:now.addingTimeInterval(10*3600)))
        XCTAssertThrowsError(try SleepWindowUpdate(enabled:true,windows:[w],targetPercent:49).validate(now:now))
    }
    @MainActor
    func testReplayRemainsOnPhoneOfflineAndNeverRestoresUnderAnotherPi() async throws {
        let name="SPARC.ReplayCache."+UUID().uuidString
        let defaults=UserDefaults(suiteName:name)!
        defer{defaults.removePersistentDomain(forName:name)}
        let fixture=try report(), client=ReplayCacheClient(report:try report())
        let store=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{"pi-a"})
        await store.refreshCombinedSavings()
        XCTAssertEqual(store.combinedSavings,fixture);XCTAssertFalse(store.combinedSavingsIsStale)
        let offline=ReplayCacheClient(report:nil)
        let reopened=AppStore(defaults:defaults,client:offline,arguments:[],controllerID:{"pi-a"})
        XCTAssertEqual(reopened.combinedSavings,fixture);XCTAssertTrue(reopened.combinedSavingsIsStale)
        await reopened.refreshCombinedSavings()
        XCTAssertEqual(reopened.combinedSavings,fixture);XCTAssertTrue(reopened.combinedSavingsIsStale)
        let anotherPi=AppStore(defaults:defaults,client:offline,arguments:[],controllerID:{"pi-b"})
        XCTAssertNil(anotherPi.combinedSavings)
    }

    func testCalendarPayloadContainsOnlyWindowIDsTimesAndTarget() throws {
        let now=Date(); let value=SleepWindowUpdate(enabled:true,windows:[SleepWindow(id:"opaque",start:now,departure:now.addingTimeInterval(8*3600))],targetPercent:80)
        let body=try WireValue.dictionary(value)
        XCTAssertEqual(Set(body.keys),Set(["enabled","windows","targetPercent"]))
        guard case let .array(windows)=body["windows"],case let .object(window)=windows[0] else { return XCTFail("Missing windows") }
        XCTAssertEqual(Set(window.keys),Set(["id","start","departure"]))
    }
}

private actor ReplayCacheClient: ControllerClient {
    let report: CombinedSavingsReport?
    init(report: CombinedSavingsReport?) { self.report=report }
    func fetchCombinedSavings(kind: String) async throws -> CombinedSavingsReport {
        guard let report else { throw ControllerConnectionError.unavailable("Offline") };return report
    }
    func fetchSnapshot() async throws -> ChargeSnapshot { .disconnected() }
    func fetchSettings() async throws -> ControllerSettings { ControllerSettings(weekly:.default,today:nil) }
    func saveWeekly(_ value: WeeklySchedule) async throws {}
    func saveToday(_ value: TodayOverride?) async throws {}
    func command(_ command: String) async throws {}
}
