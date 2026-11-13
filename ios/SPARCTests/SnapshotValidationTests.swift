import XCTest
@testable import SPARC

final class SnapshotValidationTests: XCTestCase {
    func testDemoStartsWaitingWithUnlitCarAndNoChargingPower() throws {
        let snapshot = ChargeSnapshot.demo()
        try snapshot.validate()
        XCTAssertEqual(snapshot.state, .waiting)
        XCTAssertEqual(snapshot.powerKW, 0)
        XCTAssertEqual(snapshot.batteryPercent, 40)
        XCTAssertEqual(snapshot.targetPercent, 80)
    }

    func testInvalidTelemetryIsRejected() {
        let invalidMutations: [(inout ChargeSnapshot) -> Void] = [
            { $0.batteryPercent = -1 }, { $0.batteryPercent = 101 },
            { $0.targetPercent = 101 }, { $0.powerKW = -.infinity },
            { $0.energyKWh = -1 }, { $0.monthlySavings = .nan },
            { $0.currentRateCents = .infinity }, { $0.remainingMinutes = -1 },
            { $0.remainingMinutes = 10081 }, { $0.vehicleName = "  " },
            { $0.vehicleName = String(repeating: "a", count: 129) },
            { $0.windowEnd = $0.windowStart.addingTimeInterval(-1) },
            { $0.readyBy = Date(timeIntervalSince1970: .nan) },
            { $0.updatedAt = Date(timeIntervalSince1970: .infinity) },
        ]
        for (index, mutate) in invalidMutations.enumerated() {
            var snapshot = ChargeSnapshot.demo()
            mutate(&snapshot)
            XCTAssertThrowsError(try snapshot.validate(), "Mutation \(index) was accepted")
        }
    }

    func testNegativeElectricityPriceIsValid() throws {
        var snapshot = ChargeSnapshot.demo()
        snapshot.currentRateCents = -2.5
        try snapshot.validate()
    }

    func testUnknownChargeStateDoesNotDecodeAsWaiting() throws {
        let data = try JSONEncoder().encode(ChargeSnapshot.demo())
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        object["state"] = "telemetry-unavailable"
        let changed = try JSONSerialization.data(withJSONObject: object)
        XCTAssertThrowsError(try JSONDecoder().decode(ChargeSnapshot.self, from: changed))
    }

    func testControlOnlySnapshotCannotSmuggleSampleTelemetry() throws {
        var snapshot = ChargeSnapshot.disconnected()
        snapshot.controlOnly = true
        snapshot.state = .charging
        try snapshot.validate()
        snapshot.powerKW = 11.5
        XCTAssertThrowsError(try snapshot.validate())
        snapshot.powerKW = 0
        snapshot.batteryPercent = 62
        XCTAssertThrowsError(try snapshot.validate())
    }

    func testSnapshotsWithoutControlOnlyFlagRemainCompatible() throws {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(ChargeSnapshot.demo())) as? [String: Any])
        object.removeValue(forKey: "controlOnly")
        let decoded = try JSONDecoder().decode(ChargeSnapshot.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertNil(decoded.controlOnly)
        try decoded.validate()
    }
    func testLegacySnapshotAndSettingsDecodeWithoutVirtualMarkers() throws {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(ChargeSnapshot.demo())) as? [String: Any])
        object.removeValue(forKey: "controlOnly")
        object.removeValue(forKey: "virtualCar")
        let snapshot = try JSONDecoder().decode(ChargeSnapshot.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertNil(snapshot.virtualCar)
        XCTAssertNil(snapshot.controlOnly)
        let settings = try JSONDecoder().decode(ControllerSettings.self,
            from: JSONEncoder().encode(ControllerSettings(weekly: .default, today: nil)))
        XCTAssertNil(settings.virtualCarEnabled)
        try snapshot.validate()
    }

    func testVirtualAndControlOnlyMarkersCannotBothBeTrue() {
        var snapshot = ChargeSnapshot.noVehicle()
        snapshot.virtualCar = true
        XCTAssertThrowsError(try snapshot.validate())
    }

}
