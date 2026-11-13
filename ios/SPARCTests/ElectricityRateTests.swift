import XCTest
@testable import SPARC

final class ElectricityRateTests: XCTestCase {
    private func date(_ value: String) -> Date { ISO8601DateFormatter().date(from: value)! }
    func testElectricHeatDeliveryUsesPublishedAdjustedRatesAtEveryPeriodBoundary() {
        let tariff = ComEdDeliveryClass.singleFamilyElectric
        for (time, expected) in [("05:59:00",1.790),("06:00:00",2.289),("12:59:00",2.289),("13:00:00",5.999),("18:59:00",5.999),("19:00:00",2.168),("20:59:00",2.168),("21:00:00",1.790)] {
            XCTAssertEqual(tariff.cents(at: date("2026-11-12T\(time)-06:00"))!,expected,accuracy: 0.000001)
        }
    }
    func testDeliveryUsesChicagoTimeAndDoesNotExtendAnExpiredTariff() {
        let tariff = ComEdDeliveryClass.singleFamilyElectric
        XCTAssertEqual(tariff.cents(at: date("2026-11-12T12:00:00Z")),2.289)
        XCTAssertNil(tariff.cents(at: date("2026-10-31T23:00:00-05:00")))
        XCTAssertNil(tariff.cents(at: date("2027-01-01T00:00:00-06:00")))
        XCTAssertEqual(ComEdDeliveryClass.singleFamilyNonElectric.cents(at: date("2026-11-12T06:00:00-06:00")),4.297)
    }
    func testRatePagePreservesNegativeUnknownAndRepeatedFallBackHours() throws {
        let start = date("2026-11-01T00:00:00-05:00")
        let hours = (0..<25).map { ControllerRatePage.Hour(start:start.addingTimeInterval(Double($0)*3600), supplyCents:$0==0 ? -4.5:nil, status:$0==0 ? "settled":"unavailable") }
        let page = ControllerRatePage(schemaVersion:1,source:"ComEd",timezone:"America/Chicago",day:"2026-11-01",generatedAt:date("2026-11-04T12:00:00Z"),currentSupplyCents:nil,observedAt:nil,hours:hours)
        try page.validate(requestedDay:"2026-11-01")
        XCTAssertEqual(page.hours[0].supplyCents,-4.5)
        XCTAssertNil(page.hours[1].supplyCents)
        var truncated = page; truncated.hours.removeLast()
        XCTAssertThrowsError(try truncated.validate(requestedDay:page.day))
        var duplicate = page; duplicate.hours[1] = duplicate.hours[0]
        XCTAssertThrowsError(try duplicate.validate(requestedDay:page.day))
        var fakeZero = page; fakeZero.hours[1].supplyCents = 0
        XCTAssertThrowsError(try fakeZero.validate(requestedDay:page.day))
        XCTAssertThrowsError(try page.validate(requestedDay:"2026-11-02"))
    }
    func testActualControllerRatePayloadDecodesAndValidates() throws {
        let json = #"{"schemaVersion":1,"source":"ComEd","timezone":"America/Chicago","day":"2026-11-12","generatedAt":"2026-11-12T16:10:00+00:00","currentSupplyCents":2.4,"observedAt":"2026-11-12T16:10:00+00:00","hours":[{"start":"2026-11-12T06:00:00+00:00","supplyCents":-4.5,"status":"settled"},{"start":"2026-11-12T07:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T08:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T09:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T10:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T11:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T12:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T13:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T14:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T15:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T16:00:00+00:00","supplyCents":2.4,"status":"current"},{"start":"2026-11-12T17:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T18:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T19:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T20:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T21:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T22:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-12T23:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-13T00:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-13T01:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-13T02:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-13T03:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-13T04:00:00+00:00","supplyCents":null,"status":"unavailable"},{"start":"2026-11-13T05:00:00+00:00","supplyCents":null,"status":"unavailable"}]}"#
        let page = try ControllerJSON.decoder.decode(ControllerRatePage.self,from:Data(json.utf8))
        try page.validate(requestedDay:"2026-11-12")
        XCTAssertEqual(page.currentSupplyCents,2.4)
        XCTAssertEqual(page.hours[0].supplyCents,-4.5)
        XCTAssertNil(page.hours[1].supplyCents)
    }
    func testRateHistoryUsesSavedEncryptedBluetoothRouteAndRequestedDay() async throws {
        let start = date("2026-11-12T00:00:00-06:00")
        let hours = (0..<24).map { ControllerRatePage.Hour(start:start.addingTimeInterval(Double($0)*3600),supplyCents:nil,status:"unavailable") }
        let page = ControllerRatePage(schemaVersion:1,source:"ComEd",timezone:"America/Chicago",day:"2026-11-12",generatedAt:start,currentSupplyCents:nil,observedAt:nil,hours:hours)
        let controller = PairedController(deviceID:UUID().uuidString,deviceName:"Test Pi",host:"sparc.local",setupKey:String(repeating:"0",count:64),bluetoothPeripheralID:UUID().uuidString)
        let bluetooth = RateBluetooth(page:page)
        let router = ControllerClientRouter(vault:RateCredentials(controller:controller),bluetooth:bluetooth)
        let result = try await router.fetchRates(day:"2026-11-12")
        try result.validate(requestedDay:"2026-11-12")
        let requests = await bluetooth.requests
        XCTAssertEqual(requests.count,1)
        XCTAssertEqual(requests[0].0,"get_rates")
        XCTAssertEqual(requests[0].1,["day":.string("2026-11-12")])
    }
    func testSpringForwardRatePageContains23ActualHours() throws {
        let start = date("2026-03-08T00:00:00-06:00")
        let hours = (0..<23).map { ControllerRatePage.Hour(start:start.addingTimeInterval(Double($0)*3600),supplyCents:2,status:"observed") }
        let page = ControllerRatePage(schemaVersion:1,source:"ComEd",timezone:"America/Chicago",day:"2026-03-08",generatedAt:start,currentSupplyCents:nil,observedAt:nil,hours:hours)
        try page.validate(requestedDay:page.day)
    }
    @MainActor func testDeliveryClassPersistsAndStartsWithOwnersChosenClass() {
        let name = "rate-test-\(UUID())"
        let defaults = UserDefaults(suiteName:name)!
        defer { defaults.removePersistentDomain(forName:name) }
        let store = AppStore(defaults:defaults,arguments:[])
        XCTAssertEqual(store.deliveryClass,.singleFamilyElectric)
        store.deliveryClass = .multiFamilyElectric
        XCTAssertEqual(AppStore(defaults:defaults,arguments:[]).deliveryClass,.multiFamilyElectric)
    }
}

private struct RateCredentials: ControllerCredentialStore {
    let controller: PairedController
    func load() throws -> PairedController? { controller }
    func save(_ controller: PairedController) throws {}
    func clear() throws {}
}
private actor RateBluetooth: BluetoothControllerTransport {
    let page: ControllerRatePage
    var requests: [(String,[String:WireValue])] = []
    init(page: ControllerRatePage) { self.page=page }
    func exchangeBLE(controller: PairedController,operation:String,body:[String:WireValue]) async throws -> [String:WireValue] {
        requests.append((operation,body)); return try WireValue.dictionary(page)
    }
}
