import XCTest
@testable import SPARC

final class SnapshotValidationTests: XCTestCase {
    func testDemoStartsWaitingWithUnlitCarAndNoChargingPower() throws {
        let snapshot = ChargeSnapshot.demo()
        try snapshot.validate()
        XCTAssertEqual(snapshot.state, .waiting)
        XCTAssertEqual(snapshot.powerKW, 0)
        XCTAssertEqual(snapshot.batteryPercent, 62)
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
}
