import Foundation

enum ChargeState: String, Codable, CaseIterable, Sendable {
    case waiting, charging, paused, complete, unplugged, offline
}

enum ChargingMode: String, Codable, CaseIterable, Sendable {
    case smart, fixed
}

enum ScheduleValidationError: LocalizedError, Equatable {
    case target, time, days, pastDeparture, distantDeparture

    var errorDescription: String? {
        switch self {
        case .target: return "Choose a battery target between 50% and 100%."
        case .time: return "Choose a valid time."
        case .days: return "Choose at least one departure day."
        case .pastDeparture: return "Choose a departure in the future."
        case .distantDeparture: return "Choose a departure within the next 48 hours."
        }
    }
}

struct WeeklySchedule: Codable, Equatable, Sendable {
    var targetPercent: Int
    var departureMinutes: Int
    /// ISO weekdays: Monday = 1, Sunday = 7. Times are in America/Chicago.
    var days: Set<Int>
    var enabled: Bool
    var mode: ChargingMode
    var fixedStartMinutes: Int

    static let `default` = WeeklySchedule(targetPercent: 80, departureMinutes: 420,
                                         days: [1, 2, 3, 4, 5], enabled: true,
                                         mode: .smart, fixedStartMinutes: 60)

    func validate() throws {
        guard (50...100).contains(targetPercent) else { throw ScheduleValidationError.target }
        guard (0..<1440).contains(departureMinutes), (0..<1440).contains(fixedStartMinutes)
        else { throw ScheduleValidationError.time }
        guard days.isSubset(of: Set(1...7)), !enabled || !days.isEmpty
        else { throw ScheduleValidationError.days }
    }
}

struct TodayOverride: Codable, Equatable, Sendable {
    var targetPercent: Int
    /// An absolute instant. It expires at departure and never changes the weekly routine.
    var departure: Date

    func validate(now: Date = Date()) throws {
        guard (50...100).contains(targetPercent) else { throw ScheduleValidationError.target }
        guard departure.timeIntervalSince1970.isFinite, departure > now
        else { throw ScheduleValidationError.pastDeparture }
        guard departure.timeIntervalSince(now) <= 48 * 3600 else { throw ScheduleValidationError.distantDeparture }
    }
}

struct ControllerSettings: Codable, Equatable, Sendable {
    var weekly: WeeklySchedule
    var today: TodayOverride?
}

struct ScheduleGoal: Equatable, Sendable {
    var targetPercent: Int
    var departure: Date
    var readyBy: Date
    var isOverride: Bool
}

struct ChargeSnapshot: Codable, Equatable, Sendable {
    var state: ChargeState
    var batteryPercent: Int
    var targetPercent: Int
    var readyBy: Date
    var windowStart: Date
    var windowEnd: Date
    var powerKW: Double
    var energyKWh: Double
    var remainingMinutes: Int
    var monthlySavings: Double
    var currentRateCents: Double
    var estimatedCost: Double
    var sessionSavings: Double
    var updatedAt: Date
    var vehicleName: String

    /// Values shown only in the explicitly labeled design demo.
    static func demo(now: Date = Date()) -> ChargeSnapshot {
        let goal = ScheduleEngine.effectiveGoal(weekly: .default, today: nil, now: now)!
        let window = ScheduleEngine.demoWindow(for: goal, weekly: .default,
                                               remainingMinutes: 129, now: now)
        return ChargeSnapshot(state: .waiting, batteryPercent: 62, targetPercent: 80,
                              readyBy: goal.readyBy, windowStart: window.start,
                              windowEnd: window.end,
                              powerKW: 0, energyKWh: 0, remainingMinutes: 129,
                              monthlySavings: 34.60, currentRateCents: 21,
                              estimatedCost: 2.07, sessionSavings: 3.10,
                              updatedAt: now, vehicleName: "Cybertruck")
    }

    static func disconnected(now: Date = Date()) -> ChargeSnapshot {
        ChargeSnapshot(state: .offline, batteryPercent: 0, targetPercent: 80,
                       readyBy: now, windowStart: now, windowEnd: now,
                       powerKW: 0, energyKWh: 0, remainingMinutes: 0,
                       monthlySavings: 0, currentRateCents: 0, estimatedCost: 0,
                       sessionSavings: 0, updatedAt: now, vehicleName: "Cybertruck")
    }

    func validate() throws {
        guard (0...100).contains(batteryPercent), (50...100).contains(targetPercent),
              !vehicleName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              vehicleName.utf8.count <= 128, windowEnd >= windowStart,
              [readyBy, windowStart, windowEnd, updatedAt].allSatisfy({ $0.timeIntervalSince1970.isFinite }),
              powerKW.isFinite, energyKWh.isFinite, monthlySavings.isFinite,
              currentRateCents.isFinite, estimatedCost.isFinite, sessionSavings.isFinite,
              powerKW >= 0, energyKWh >= 0, remainingMinutes >= 0,
              remainingMinutes <= 10080 else { throw SnapshotError.invalid }
    }
}

enum SnapshotError: LocalizedError {
    case invalid
    var errorDescription: String? { "The controller sent an invalid charging update. Try refreshing." }
}

struct ChargingRecord: Identifiable, Codable, Equatable, Sendable {
    var id: UUID
    var date: Date
    var energyKWh: Double
    var cost: Double
    var savings: Double

    static func demo(now: Date = Date()) -> [ChargingRecord] {
        (1...6).map { offset in
            ChargingRecord(id: UUID(), date: now.addingTimeInterval(Double(-offset) * 86400),
                           energyKWh: 24.6 + Double(offset), cost: 2.07 + Double(offset) * 0.11,
                           savings: 3.1 - Double(offset) * 0.12)
        }
    }
}
