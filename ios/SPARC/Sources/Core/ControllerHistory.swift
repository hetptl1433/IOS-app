import Foundation

/// The car a history page or session belongs to. Older controllers omit it; that means the virtual car.
struct HistoryCarIdentity: Codable, Equatable, Hashable, Sendable {
    var kind: String
    var vehicleId: String?
    static let virtual = HistoryCarIdentity(kind: "virtual", vehicleId: nil)
    static func tesla(_ vehicleId: String) -> HistoryCarIdentity { HistoryCarIdentity(kind: "tesla", vehicleId: vehicleId) }
    var isTesla: Bool { kind == "tesla" }
}

struct ControllerHistoryPage: Codable, Equatable, Sendable {
    var schemaVersion: Int
    var generatedAt: Date
    var timezone: String
    var costScope: String
    var benchmark: String
    var week: HistoryWeek
    var sessions: [HistorySession]
    var nextCursor: Int?
    var car: HistoryCarIdentity? = nil

    /// Validates a virtual-car page, or with `teslaVehicleId` a page that holds only that Tesla's charges.
    /// A page that mixes cars is rejected, so one car's history never shows under another.
    func validate(teslaVehicleId: String? = nil) throws {
        guard schemaVersion == 1, timezone == "America/Chicago",
              costScope == "electricity_supply",
              benchmark == (teslaVehicleId == nil ? "immediate_same_energy" : "none"),
              generatedAt.timeIntervalSince1970.isFinite, sessions.count <= 8,
              nextCursor == nil || nextCursor! >= 0,
              Set(sessions.map(\.id)).count == sessions.count else { throw HistoryError.invalid }
        if let teslaVehicleId {
            guard car == .tesla(teslaVehicleId), sessions.allSatisfy({ $0.car == .tesla(teslaVehicleId) })
            else { throw HistoryError.invalid }
        } else {
            guard car == nil || car == .virtual, sessions.allSatisfy({ $0.car == nil || $0.car == .virtual })
            else { throw HistoryError.invalid }
        }
        try week.validate()
        try sessions.forEach { try $0.validate() }
    }
}

struct HistoryWeek: Codable, Equatable, Sendable {
    var start: String
    var end: String
    var energyKWh: Double
    var costUSD: Double?
    var baselineCostUSD: Double?
    var savingsUSD: Double?
    var savingsPercent: Double?
    var pricedSessions: Int
    var unpricedSessions: Int
    var provisional: Bool
    var days: [HistoryDay]

    func validate() throws {
        guard CampaignUpdate.date(start) != nil, CampaignUpdate.date(end) != nil,
              start < end, energyKWh.isFinite, energyKWh >= 0,
              pricedSessions >= 0, unpricedSessions >= 0, days.count == 7,
              Set(days.map(\.date)).count == days.count,
              [costUSD, baselineCostUSD, savingsUSD, savingsPercent].compactMap({ $0 }).allSatisfy(\.isFinite)
        else { throw HistoryError.invalid }
        try days.forEach { try $0.validate() }
    }
}

struct HistoryDay: Codable, Equatable, Identifiable, Sendable {
    var date: String
    var energyKWh: Double
    var costUSD: Double?
    var baselineCostUSD: Double?
    var savingsUSD: Double?
    var sessionCount: Int
    var id: String { date }

    /// Derived locally so existing controller responses and saved history keep their schema.
    var savingsPercent: Double? {
        guard sessionCount > 0, let savingsUSD, let baselineCostUSD,
              savingsUSD.isFinite, baselineCostUSD.isFinite, abs(baselineCostUSD) > 1e-9 else { return nil }
        let percent = savingsUSD / abs(baselineCostUSD) * 100
        return percent.isFinite ? percent : nil
    }

    func validate() throws {
        guard CampaignUpdate.date(date) != nil, energyKWh.isFinite, energyKWh >= 0, sessionCount >= 0,
              [costUSD, baselineCostUSD, savingsUSD].compactMap({ $0 }).allSatisfy(\.isFinite)
        else { throw HistoryError.invalid }
    }
}

struct HistorySession: Codable, Equatable, Identifiable, Sendable {
    var id: UUID
    var date: Date
    var start: Date
    /// The virtual car's ready-by time. A Tesla charge started from SPARC has none.
    var departure: Date?
    var nightDate: String
    var status: String
    var initialBatteryPercent: Double
    /// Nil only for a Tesla that did not report its charge limit.
    var targetBatteryPercent: Double?
    var batteryPercent: Double
    /// Nil only for a Tesla that did not report its energy counter: unknown, never zero.
    var energyKWh: Double?
    var costUSD: Double?
    var baselineCostUSD: Double?
    var savingsUSD: Double?
    var savingsPercent: Double?
    var costFinal: Bool
    var simulated: Bool
    var source: String
    var targetReachedByDeadline: Bool?
    var provisional: Bool? = nil
    var unpricedEnergyKWh: Double? = nil
    var baselineFinal: Bool? = nil
    var car: HistoryCarIdentity? = nil
    var vehicleName: String? = nil
    var end: Date? = nil
    var endReason: String? = nil
    var lastObservedAt: Date? = nil
    var energySource: String? = nil
    var energyPartial: Bool? = nil

    static let teslaEndReasons: Set<String> = ["limit_reached", "stopped", "unplugged", "no_power", "not_observed"]
    var isTesla: Bool { car?.isTesla == true }
    var isProvisional: Bool {
        // A Tesla session with unknown energy has no price to wait for; the Pi says when one is pending.
        if isTesla { return provisional == true }
        return provisional == true || !costFinal || baselineFinal == false
    }
    var pricedRecord: ChargingRecord? {
        guard let costUSD, let savingsUSD, let energyKWh else { return nil }
        return ChargingRecord(id: id, date: date, energyKWh: energyKWh, cost: costUSD, savings: savingsUSD)
    }
    func validate() throws {
        guard [date, start].allSatisfy({ $0.timeIntervalSince1970.isFinite }),
              CampaignUpdate.date(nightDate) != nil,
              initialBatteryPercent.isFinite, (0...100).contains(initialBatteryPercent),
              batteryPercent.isFinite, (0...100).contains(batteryPercent),
              source == "ComEd",
              [costUSD, baselineCostUSD, savingsUSD, savingsPercent, unpricedEnergyKWh, energyKWh, targetBatteryPercent]
                .compactMap({ $0 }).allSatisfy(\.isFinite),
              (unpricedEnergyKWh ?? 0) >= 0, (energyKWh ?? 0) >= 0,
              targetBatteryPercent.map({ (50...100).contains($0) }) ?? true else { throw HistoryError.invalid }
        if isTesla {
            // Real charges SPARC started: no simulated data, no deadline, and no claimed savings.
            guard let vehicle = car?.vehicleId, !vehicle.isEmpty, vehicle.count <= 32, vehicle.allSatisfy(\.isASCII),
                  vehicle.allSatisfy(\.isNumber), !simulated, departure == nil,
                  ["active", "complete", "incomplete"].contains(status),
                  (end == nil) == (status == "active"), end.map({ $0 >= start }) ?? true,
                  endReason.map(Self.teslaEndReasons.contains) ?? true, end != nil || endReason == nil,
                  energySource == "vehicle_reported", energyKWh != nil || costUSD == nil,
                  baselineCostUSD == nil, savingsUSD == nil, savingsPercent == nil,
                  vehicleName.map({ $0.utf8.count <= 128 }) ?? true else { throw HistoryError.invalid }
        } else {
            guard car == nil || car == .virtual, let departure, departure.timeIntervalSince1970.isFinite, departure > start,
                  ["scheduled", "active", "complete", "incomplete"].contains(status),
                  targetBatteryPercent != nil, energyKWh != nil, simulated else { throw HistoryError.invalid }
        }
    }
}

/// One Tesla's synced charging history, saved apart from the virtual car's.
struct TeslaHistory: Codable, Equatable, Sendable {
    var vehicleId: String
    var generatedAt: Date
    var week: HistoryWeek
    var sessions: [HistorySession]
    var nextCursor: Int?

    func validate() throws {
        guard !vehicleId.isEmpty, generatedAt.timeIntervalSince1970.isFinite, sessions.count <= 1000,
              nextCursor == nil || nextCursor! >= 0,
              sessions.allSatisfy({ $0.car == .tesla(vehicleId) }) else { throw HistoryError.invalid }
        try week.validate()
        try sessions.forEach { try $0.validate() }
    }
}

enum HistoryError: LocalizedError {
    case invalid
    var errorDescription: String? { "The controller sent an invalid history update. Your saved results were kept." }
}

struct CampaignCurrentSession: Codable, Equatable, Sendable {
    var start: Date
    var departure: Date
    var initialBatteryPercent: Int
    var targetBatteryPercent: Int
    var sessionId: String?
    var finalizedAt: Date?
}

struct CampaignSchedule: Codable, Equatable, Sendable {
    var campaignId: String
    var startDate: String
    var nights: Int
    var startMinutes: Int
    var departureMinutes: Int
    var initialBatteryPercent: Int
    var targetBatteryPercent: Int
    var screenSleepMinutes: Int
    var strategy: String
    var timezone: String
    var enabled: Bool
    var revision: Int
    var firstStart: Date
    var finalDeparture: Date
    var currentSessionId: String?
    var phase: String
    var nextStart: Date?
    var current: CampaignCurrentSession?

    func validate() throws {
        try CampaignUpdate(self).validate()
        guard !campaignId.isEmpty, campaignId.count <= 128, revision >= 0,
              strategy == "day_ahead_live", timezone == "America/Chicago",
              firstStart.timeIntervalSince1970.isFinite, finalDeparture.timeIntervalSince1970.isFinite,
              finalDeparture > firstStart, nextStart?.timeIntervalSince1970.isFinite != false
        else { throw CampaignError.invalid }
        if let current {
            guard current.departure > current.start, (0...99).contains(current.initialBatteryPercent),
                  (50...100).contains(current.targetBatteryPercent) else { throw CampaignError.invalid }
        }
    }
}

/// Only editable fields and the optimistic revision travel back to the controller.
struct CampaignUpdate: Codable, Equatable, Sendable {
    var enabled: Bool
    var startDate: String
    var nights: Int
    var startMinutes: Int
    var departureMinutes: Int
    var initialBatteryPercent: Int
    var targetBatteryPercent: Int
    var screenSleepMinutes: Int
    var expectedRevision: Int

    init(_ value: CampaignSchedule) {
        enabled = value.enabled; startDate = value.startDate; nights = value.nights
        startMinutes = value.startMinutes; departureMinutes = value.departureMinutes
        initialBatteryPercent = value.initialBatteryPercent; targetBatteryPercent = value.targetBatteryPercent
        screenSleepMinutes = value.screenSleepMinutes; expectedRevision = value.revision
    }
    func validate() throws {
        guard Self.date(startDate) != nil, (1...90).contains(nights),
              (0..<1440).contains(startMinutes), (0..<1440).contains(departureMinutes),
              (0...99).contains(initialBatteryPercent), (50...100).contains(targetBatteryPercent),
              initialBatteryPercent < targetBatteryPercent, (1...120).contains(screenSleepMinutes),
              expectedRevision >= 0 else { throw CampaignError.invalid }
    }
    static func date(_ value: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "America/Chicago")
        formatter.dateFormat = "yyyy-MM-dd"; formatter.isLenient = false
        guard let date = formatter.date(from: value), formatter.string(from: date) == value else { return nil }
        return date
    }
    static func dayString(_ value: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "America/Chicago")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: value)
    }
}

enum CampaignError: LocalizedError {
    case invalid
    var errorDescription: String? { "Choose valid dates, 1–90 nights, battery percentages and screen sleep minutes." }
}
