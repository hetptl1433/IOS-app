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
    /// Missing on older controllers; new virtual-car controllers default to enabled.
    var virtualCarEnabled: Bool? = nil
    var campaign: CampaignSchedule? = nil
    var developer: DeveloperVehicleState? = nil
    var control: ControllerControl? = nil
    var clients: ControllerClients? = nil
    var connections: ControllerConnections? = nil
    var sleepCalendar: SleepCalendarStatus? = nil
}

enum ControllerConnectionMode: String, Codable, CaseIterable, Identifiable, Sendable {
    case virtual, tesla
    case smartPlug = "smart_plug"
    var id: String { rawValue }
    var title: String { switch self { case .virtual: return "Virtual Car"; case .tesla: return "Tesla Car"; case .smartPlug: return "Smart Plug" } }
}

enum VirtualVehicleModel: String, Codable, CaseIterable, Identifiable, Sendable {
    case cybertruck
    case modelX = "model_x"
    case model3 = "model_3"
    var id: String { rawValue }
    var title: String { switch self { case .cybertruck: return "Cybertruck"; case .modelX: return "Model X"; case .model3: return "Model 3" } }
}

enum VehicleDataSource: String, Codable, CaseIterable, Identifiable, Sendable {
    case virtual, tesla
    var id: String { rawValue }
    var title: String { self == .virtual ? "Virtual Car" : "Tesla Car" }
}

enum TeslaChargeCommand: String, Sendable {
    case start = "charge_start", stop = "charge_stop", setLimit = "set_charge_limit"

    var title: String {
        switch self {
        case .start: return "Start charging"
        case .stop: return "Stop charging"
        case .setLimit: return "Set charge limit"
        }
    }

    func validate(value: Int?) throws {
        if self == .setLimit {
            guard let value, (50...100).contains(value) else { throw ScheduleValidationError.target }
        } else if value != nil { throw ControllerConnectionError.invalidMessage }
    }
}

struct TeslaCommandReceipt: Decodable, Sendable {
    var accepted: Bool
    var command: String

    func validate(expected: TeslaChargeCommand) throws {
        guard accepted, command == expected.rawValue else {
            throw ControllerConnectionError.unavailable("Tesla did not confirm this charging command. Refresh before trying again.")
        }
    }
}

struct ControllerConnections: Codable, Equatable, Sendable {
    struct Virtual: Codable, Equatable, Sendable {
        var model: VirtualVehicleModel
        var batteryPercent: Double
        var pluggedIn: Bool
        var profileOnly: Bool? = nil
    }
    struct SmartPlug: Codable, Equatable, Sendable {
        var address: String?
        var vehicleSource: VehicleDataSource
        var status: String
        var lastCheckedAt: Date?
        var powerW: Double?
        var error: String?
        var verified: Bool?
        var switchOn: Bool?
        var followVirtual: Bool?
    }
    struct Tesla: Codable, Equatable, Sendable {
        struct ChargeState: Codable, Equatable, Sendable {
            var batteryPercent: Double
            var pluggedIn: Bool
            var powerKW: Double
            var state: String
            var updatedAt: Date
            var vehicleName: String
            var source: String
            var timestampSource: String
            var stale: Bool?
        }
        struct Vehicle: Codable, Equatable, Identifiable, Sendable {
            var id: String
            var vin: String
            var displayName: String
            /// The Tesla account this car comes from. A Pi that holds a single account leaves it out.
            var accountId: String?
        }
        /// One Tesla sign-in on the Pi. A Pi that holds a single account doesn't send this list.
        struct Account: Codable, Equatable, Identifiable, Sendable {
            var id: String
            var label: String
            var authorized: Bool
            var vehicleCount: Int
            var error: String?
        }
        var configured: Bool
        var authorized: Bool
        var status: String
        var vehicleName: String?
        var error: String?
        var vehicleId: String?
        var vehicles: [Vehicle]?
        var keyPairingURL: URL?
        var commandsReady: Bool?
        var chargeState: ChargeState?
        var accounts: [Account]?
        /// The account that owns the selected car; charging commands go through it.
        var accountId: String?
    }
    var revision: Int
    var mode: ControllerConnectionMode
    var activeMode: ControllerConnectionMode?
    var virtual: Virtual
    var smartPlug: SmartPlug
    var tesla: Tesla
    func validate() throws {
        guard revision >= 0, virtual.batteryPercent.isFinite, (0...100).contains(virtual.batteryPercent),
              smartPlug.lastCheckedAt?.timeIntervalSince1970.isFinite != false,
              smartPlug.powerW?.isFinite != false,
              (smartPlug.address?.utf8.count ?? 0) <= 512 else { throw SnapshotError.invalid }
        if let url = tesla.keyPairingURL {
            guard url.scheme?.lowercased() == "https", ["tesla.com", "www.tesla.com"].contains(url.host?.lowercased() ?? ""),
                  url.path.hasPrefix("/_ak/"), url.user == nil, url.password == nil,
                  url.port == nil || url.port == 443 else { throw SnapshotError.invalid }
        }
        if let accounts = tesla.accounts {
            guard accounts.count <= 32, Set(accounts.map(\.id)).count == accounts.count,
                  accounts.allSatisfy({ !$0.id.isEmpty && $0.id.utf8.count <= 64 && $0.label.utf8.count <= 256
                      && (0...1000).contains($0.vehicleCount) }) else { throw SnapshotError.invalid }
        }
        if let value = tesla.chargeState {
            guard value.batteryPercent.isFinite, (0...100).contains(value.batteryPercent), value.powerKW.isFinite,
                  value.updatedAt.timeIntervalSince1970.isFinite, value.source == "TeslaFleetAPI",
                  ["vehicle", "received"].contains(value.timestampSource) else { throw SnapshotError.invalid }
        }
    }
}

struct ConnectionSettingsUpdate: Encodable, Sendable {
    struct Virtual: Encodable, Sendable {
        var model: VirtualVehicleModel?
        var batteryPercent: Double?
        var pluggedIn: Bool?
    }
    struct SmartPlug: Encodable, Sendable {
        var address: String
        var vehicleSource: VehicleDataSource
        var followVirtual: Bool? = nil
    }
    var mode: ControllerConnectionMode
    var virtual: Virtual? = nil
    var smartPlug: SmartPlug? = nil
    var expectedRevision: Int
    func validate() throws {
        guard expectedRevision >= 0 else { throw SnapshotError.invalid }
        if let battery = virtual?.batteryPercent {
            guard battery.isFinite, (0...100).contains(battery) else { throw SnapshotError.invalid }
        }
        if let smartPlug {
            guard !smartPlug.address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  smartPlug.address.utf8.count <= 512 else {
                throw ControllerConnectionError.unavailable("Enter your Shelly plug's local address.")
            }
        }
    }
}

struct TeslaAuthorization: Decodable, Sendable {
    var authorizationURL: URL
    var expiresAt: Date?
    var stateExpiresAt: Date?
    func validate() throws {
        let host = authorizationURL.host?.lowercased() ?? ""
        guard authorizationURL.scheme?.lowercased() == "https", host == "auth.tesla.com",
              authorizationURL.user == nil, authorizationURL.password == nil,
              authorizationURL.port == nil || authorizationURL.port == 443,
              expiresAt?.timeIntervalSince1970.isFinite != false,
              stateExpiresAt?.timeIntervalSince1970.isFinite != false else { throw ControllerConnectionError.invalidMessage }
    }
}

struct DeveloperVehicleState: Codable, Equatable, Sendable {
    var enabled: Bool
    var simulated: Bool
    var batteryPercent: Double
    var pluggedIn: Bool
    var revision: Int
    var updatedAt: Date?
    func validate() throws {
        guard simulated, batteryPercent.isFinite, (0...100).contains(batteryPercent), revision >= 0,
              updatedAt?.timeIntervalSince1970.isFinite != false else { throw SnapshotError.invalid }
    }
}

struct ControllerControl: Codable, Equatable, Sendable {
    var sequence: Int
    var operation: String?
    var updatedAt: Date?
    var clientId: String?
    var clientName: String?
    func validate() throws {
        guard sequence >= 0, updatedAt?.timeIntervalSince1970.isFinite != false,
              clientId == nil || UUID(uuidString: clientId!) != nil else { throw SnapshotError.invalid }
    }
}

struct ControllerClients: Codable, Equatable, Sendable {
    var pairedCount: Int
    var currentClientId: String?
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
    /// Acknowledged controller state for app testing, without vehicle telemetry.
    var controlOnly: Bool? = nil
    /// Explicitly virtual values must never be treated as physical vehicle telemetry.
    var virtualCar: Bool? = nil
    var realPricing: Bool? = nil
    var rateAvailable: Bool? = nil
    var costAvailable: Bool? = nil
    var costFinal: Bool? = nil
    var unpricedEnergyKWh: Double? = nil
    var control: ControllerControl? = nil

    /// Tesla reports cached vehicle measurements separately from unavailable session totals.
    var manualOnly: Bool? = nil
    var source: String? = nil
    var telemetryStale: Bool? = nil
    var energyAvailable: Bool? = nil
    var savingsAvailable: Bool? = nil
    var scheduleAvailable: Bool? = nil
    var targetAvailable: Bool? = nil

    /// Values shown only in the explicitly labeled design demo.
    static func demo(now: Date = Date()) -> ChargeSnapshot {
        var car = VirtualCarState(now: now)
        car.update(weekly: .default, today: nil, now: now)
        return car.snapshot(now: now)
    }

    static func noVehicle(now: Date = Date(), target: Int = 80) -> ChargeSnapshot {
        var result = disconnected(now: now)
        result.state = .unplugged
        result.targetPercent = target
        result.vehicleName = "No vehicle connected"
        result.controlOnly = true
        result.virtualCar = false
        return result
    }

    static func disconnected(now: Date = Date()) -> ChargeSnapshot {
        ChargeSnapshot(state: .offline, batteryPercent: 0, targetPercent: 80,
                       readyBy: now, windowStart: now, windowEnd: now,
                       powerKW: 0, energyKWh: 0, remainingMinutes: 0,
                       monthlySavings: 0, currentRateCents: 0, estimatedCost: 0,
                       sessionSavings: 0, updatedAt: now, vehicleName: "Cybertruck")
    }

    func validate() throws {
        try control?.validate()
        guard (0...100).contains(batteryPercent), (50...100).contains(targetPercent),
              !vehicleName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              vehicleName.utf8.count <= 128, windowEnd >= windowStart,
              [readyBy, windowStart, windowEnd, updatedAt].allSatisfy({ $0.timeIntervalSince1970.isFinite }),
              powerKW.isFinite, energyKWh.isFinite, monthlySavings.isFinite,
              currentRateCents.isFinite, estimatedCost.isFinite, sessionSavings.isFinite,
              powerKW >= 0, energyKWh >= 0, remainingMinutes >= 0,
              remainingMinutes <= 10080 else { throw SnapshotError.invalid }
        if virtualCar == true, controlOnly == true { throw SnapshotError.invalid }
        if manualOnly == true {
            guard virtualCar == false, controlOnly != true, source == "TeslaFleetAPI",
                  energyAvailable == false, costAvailable == false, savingsAvailable == false,
                  scheduleAvailable == false else { throw SnapshotError.invalid }
        }
        if controlOnly == true {
            guard batteryPercent == 0, powerKW == 0, energyKWh == 0,
                  remainingMinutes == 0, monthlySavings == 0, currentRateCents == 0,
                  estimatedCost == 0, sessionSavings == 0 else { throw SnapshotError.invalid }
        }
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


/// A local virtual vehicle for the design demo. No device or Tesla API is called.
/// The Pi uses the same battery, AC power, efficiency and example price model.
struct VirtualCarState {
    static let capacityKWh = 100.0
    static let powerKW = 11.5
    static let efficiency = 0.9
    private var battery = 40.0
    private var energy = 0.0
    private var cost = 0.0
    private var lastUpdate: Date
    private var state: ChargeState = .waiting
    private var manualState: ChargeState?
    private var manualUntil: Date?
    private var target = 80
    private var weekly: WeeklySchedule?
    private var today: TodayOverride?
    private var goal: ScheduleGoal?
    private var window: DateInterval?

    init(now: Date) { lastUpdate = now }

    mutating func update(weekly: WeeklySchedule, today: TodayOverride?, now: Date) {
        advance(to: now)
        let changed = self.weekly != weekly || self.today != today
        self.weekly = weekly; self.today = today
        if changed { manualState = nil; manualUntil = nil }
        updatePlan(at: now)
        // A fresh demo launch is visibly waiting; elapsed time or an explicit
        // command activates the model instead of restoring a previous session.
        if changed { state = battery >= Double(target) ? .complete : .waiting }
    }

    mutating func command(_ command: String, now: Date) throws {
        advance(to: now)
        updatePlan(at: now)
        switch command {
        case "charge_now", "resume": manualState = .charging
        case "pause": manualState = .paused
        case "end": manualState = .waiting
        default: throw ControllerConnectionError.invalidMessage
        }
        if let goal {
            manualUntil = command == "end" || goal.readyBy <= now ? goal.departure : goal.readyBy
        } else { manualUntil = nil }
        state = battery >= Double(target) ? .complete : manualState ?? .waiting
    }

    private func remainingMinutes(target: Int) -> Int {
        Int(ceil(max(0, Double(target) - battery) / 100 * Self.capacityKWh
                 / (Self.powerKW * Self.efficiency) * 60))
    }

    private mutating func updatePlan(at now: Date) {
        guard let weekly else { return }
        goal = ScheduleEngine.effectiveGoal(weekly: weekly, today: today, now: now)
        target = goal?.targetPercent ?? weekly.targetPercent
        window = goal.map { ScheduleEngine.virtualWindow(for: $0, weekly: weekly,
            remainingMinutes: remainingMinutes(target: target), now: now) }
    }

    private func state(at instant: Date, goal: ScheduleGoal?, window: DateInterval?, target: Int) -> ChargeState {
        if battery >= Double(target) - 0.0000001 { return .complete }
        if let manualState, manualUntil == nil || instant < manualUntil! { return manualState }
        if let goal, let window, instant >= window.start, instant < goal.readyBy { return .charging }
        return .waiting
    }

    private mutating func advance(to now: Date) {
        guard now > lastUpdate, let weekly else { return }
        var cursor = lastUpdate
        lastUpdate = now
        // Integrate each active interval across rate, schedule, manual-expiry
        // and departure boundaries, including time spent in the background.
        while cursor < now {
            let currentGoal = ScheduleEngine.effectiveGoal(weekly: weekly, today: today, now: cursor)
            let currentTarget = currentGoal?.targetPercent ?? weekly.targetPercent
            let currentWindow = currentGoal.map { ScheduleEngine.virtualWindow(for: $0, weekly: weekly,
                remainingMinutes: remainingMinutes(target: currentTarget), now: cursor) }
            let active = state(at: cursor, goal: currentGoal, window: currentWindow, target: currentTarget)
            let calendar = ScheduleEngine.chicagoCalendar
            var boundary = min(now, calendar.dateInterval(of: .hour, for: cursor)?.end ?? now)
            if let manualUntil, manualUntil > cursor { boundary = min(boundary, manualUntil) }
            if let currentGoal {
                for instant in [currentWindow?.start, currentGoal.readyBy, currentGoal.departure].compactMap({ $0 }) where instant > cursor {
                    boundary = min(boundary, instant)
                }
            }
            guard boundary > cursor else { break }
            if active == .charging {
                let needed = max(0, Double(currentTarget) - battery) / 100 * Self.capacityKWh / Self.efficiency
                let supplied = min(needed, Self.powerKW * boundary.timeIntervalSince(cursor) / 3600)
                energy += supplied
                cost += supplied * Self.exampleRate(at: cursor) / 100
                battery = min(100, battery + supplied * Self.efficiency / Self.capacityKWh * 100)
            }
            cursor = boundary
            let maximumTarget = max(weekly.targetPercent,
                today.flatMap { $0.departure > cursor ? $0.targetPercent : nil } ?? weekly.targetPercent)
            if battery >= Double(maximumTarget) - 0.0000001 { break }
        }
        if let manualUntil, now >= manualUntil { manualState = nil; self.manualUntil = nil }
        updatePlan(at: now)
        state = state(at: now, goal: goal, window: window, target: target)
    }

    static func exampleRate(at date: Date) -> Double {
        let hour = ScheduleEngine.chicagoCalendar.component(.hour, from: date)
        return (1..<5).contains(hour) ? 7 : 21
    }

    func snapshot(now: Date) -> ChargeSnapshot {
        let minutes = remainingMinutes(target: target)
        let start = window?.start ?? now
        let end: Date
        if manualState == .charging, state == .charging {
            let forecast = now.addingTimeInterval(Double(minutes) * 60)
            end = manualUntil.map { min($0, forecast) } ?? forecast
        } else { end = window?.end ?? now }
        return ChargeSnapshot(state: state, batteryPercent: Int(floor(battery + 0.0000001)),
                              targetPercent: target, readyBy: goal?.readyBy ?? now,
                              windowStart: start, windowEnd: max(start, end),
                              powerKW: state == .charging ? Self.powerKW : 0,
                              energyKWh: energy, remainingMinutes: minutes,
                              monthlySavings: 0, currentRateCents: Self.exampleRate(at: now),
                              estimatedCost: cost, sessionSavings: max(0, energy * 0.21 - cost),
                              updatedAt: now, vehicleName: "Virtual car", controlOnly: false, virtualCar: true)
    }
}
