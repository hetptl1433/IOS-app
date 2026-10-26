import Foundation
import Combine

@MainActor
final class AppStore: ObservableObject {
    @Published private(set) var snapshot: ChargeSnapshot
    @Published private(set) var weekly: WeeklySchedule
    @Published private(set) var today: TodayOverride?
    @Published private(set) var records: [ChargingRecord]
    @Published private(set) var isDemo: Bool
    @Published private(set) var controllerName: String?
    @Published private(set) var hasLiveTelemetry = false
    @Published private(set) var busy = false
    @Published private(set) var connectionMessage: String?
    @Published var errorMessage: String?

    private let client: any ControllerClient
    private let defaults: UserDefaults
    private let now: () -> Date
    private var refreshing = false
    private var revision = 0
    private let demoSettingsKey = "sparc.demo.settings.v1"

    init(defaults: UserDefaults = .standard,
         client: any ControllerClient = LocalControllerClient(),
         now: @escaping () -> Date = Date.init,
         arguments: [String] = ProcessInfo.processInfo.arguments) {
        self.defaults = defaults
        self.client = client
        self.now = now
        let testing = arguments.contains("--uitesting")
        let demo = testing || defaults.object(forKey: "sparc.demoMode") == nil
            || defaults.bool(forKey: "sparc.demoMode")
        self.isDemo = demo
        var settings = ControllerSettings(weekly: .default, today: nil)
        if !testing, let data = defaults.data(forKey: demoSettingsKey),
           let saved = try? JSONDecoder().decode(ControllerSettings.self, from: data),
           (try? saved.weekly.validate()) != nil {
            settings = saved
        }
        self.weekly = demo ? settings.weekly : .default
        self.today = demo && settings.today.map({ (try? $0.validate(now: now())) != nil }) == true
            ? settings.today : nil
        self.snapshot = demo ? .demo(now: now()) : .disconnected(now: now())
        self.records = demo ? ChargingRecord.demo(now: now()) : []
        self.controllerName = try? ControllerVault.shared.load()?.deviceName
        if demo {
            applyDemoGoal()
            if testing, let index = arguments.firstIndex(of: "--state"),
               arguments.indices.contains(index + 1),
               let state = ChargeState(rawValue: arguments[index + 1]) {
                snapshot.state = state
                snapshot.powerKW = state == .charging ? 11.5 : 0
                if state == .complete { snapshot.batteryPercent = snapshot.targetPercent }
            }
        }
    }

    var effectiveGoal: ScheduleGoal? {
        ScheduleEngine.effectiveGoal(weekly: weekly, today: today, now: now())
    }

    func setDemoMode(_ enabled: Bool) {
        guard !busy, enabled != isDemo else { return }
        revision += 1
        isDemo = enabled
        defaults.set(enabled, forKey: "sparc.demoMode")
        errorMessage = nil
        connectionMessage = nil
        hasLiveTelemetry = false
        today = nil
        weekly = .default
        if enabled {
            if let data = defaults.data(forKey: demoSettingsKey),
               let saved = try? JSONDecoder().decode(ControllerSettings.self, from: data),
               (try? saved.weekly.validate()) != nil {
                weekly = saved.weekly
                today = saved.today.map({ (try? $0.validate(now: now())) != nil }) == true ? saved.today : nil
            }
            snapshot = .demo(now: now())
            records = ChargingRecord.demo(now: now())
            applyDemoGoal()
        } else {
            snapshot = .disconnected(now: now())
            records = []
        }
        controllerName = try? ControllerVault.shared.load()?.deviceName
    }

    func refresh() async {
        if isDemo {
            if let today, today.departure <= now() {
                self.today = nil
                persistDemoSettings()
            }
            applyDemoGoal()
            return
        }
        guard !refreshing, !busy else { return }
        refreshing = true
        let currentRevision = revision
        defer { refreshing = false }
        controllerName = try? ControllerVault.shared.load()?.deviceName
        do {
            let settings = try await client.fetchSettings()
            try settings.weekly.validate()
            if let override = settings.today, override.departure > now() { try override.validate(now: now()) }
            guard revision == currentRevision, !isDemo, !busy else { return }
            // Schedule storage is available even before a vehicle adapter supplies telemetry.
            weekly = settings.weekly
            today = (settings.today?.departure ?? .distantPast) > now() ? settings.today : nil
            let update = try await client.fetchSnapshot()
            try update.validate()
            guard revision == currentRevision, !isDemo, !busy else { return }
            snapshot = update
            hasLiveTelemetry = true
            connectionMessage = nil
        } catch {
            guard revision == currentRevision, !isDemo, !busy else { return }
            snapshot.state = .offline
            snapshot.powerKW = 0
            hasLiveTelemetry = false
            connectionMessage = error.localizedDescription
        }
    }

    func saveWeekly(_ schedule: WeeklySchedule) async {
        guard !busy else { return }
        do { try schedule.validate() } catch { errorMessage = error.localizedDescription; return }
        await perform {
            if !self.isDemo { try await self.client.saveWeekly(schedule) }
            self.weekly = schedule
            if self.isDemo { self.applyDemoGoal(); self.persistDemoSettings() }
        }
    }

    func saveToday(target: Int, departure: Date) async {
        guard !busy else { return }
        let value = TodayOverride(targetPercent: target, departure: departure)
        do { try value.validate(now: now()) } catch { errorMessage = error.localizedDescription; return }
        await perform {
            if !self.isDemo { try await self.client.saveToday(value) }
            self.today = value
            if self.isDemo { self.applyDemoGoal(); self.persistDemoSettings() }
        }
    }

    func clearToday() async {
        await perform {
            if !self.isDemo { try await self.client.saveToday(nil) }
            self.today = nil
            if self.isDemo { self.applyDemoGoal(); self.persistDemoSettings() }
        }
    }

    func chargeNow() async { await command("charge_now", demoState: .charging) }
    func pauseCharging() async { await command("pause", demoState: .paused) }
    func resumeCharging() async { await command("resume", demoState: .charging) }
    func endCharging() async { await command("end", demoState: .waiting) }

    func forgetController() {
        guard !busy else { return }
        do {
            try ControllerVault.shared.clear()
            revision += 1
            controllerName = nil
            if !isDemo {
                snapshot = .disconnected(now: now())
                hasLiveTelemetry = false
                weekly = .default
                today = nil
                records = []
            }
            errorMessage = nil
            connectionMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    private func command(_ value: String, demoState: ChargeState) async {
        await perform {
            if self.isDemo {
                guard self.snapshot.state != .unplugged else { throw DemoError.unplugged }
                guard self.snapshot.state != .offline else { throw DemoError.offline }
                guard self.snapshot.state != .complete || value != "charge_now" else { return }
                self.snapshot.state = demoState
                self.snapshot.powerKW = demoState == .charging ? 11.5 : 0
                self.snapshot.updatedAt = self.now()
            } else {
                try await self.client.command(value)
                // Display only the controller's confirmed state after it acknowledges a command.
                do {
                    let update = try await self.client.fetchSnapshot()
                    try update.validate()
                    self.snapshot = update
                    self.hasLiveTelemetry = true
                    self.connectionMessage = nil
                } catch {
                    self.snapshot.state = .offline
                    self.snapshot.powerKW = 0
                    self.hasLiveTelemetry = false
                    self.connectionMessage = "Your request was sent, but its charging state could not be confirmed. Refresh to reconnect."
                    throw error
                }
            }
        }
    }

    private func perform(_ operation: () async throws -> Void) async {
        guard !busy else { return }
        busy = true
        revision += 1
        errorMessage = nil
        defer { busy = false }
        do { try await operation() }
        catch { errorMessage = error.localizedDescription }
    }

    private func applyDemoGoal() {
        guard isDemo, let goal = effectiveGoal else { return }
        var next = snapshot
        next.targetPercent = goal.targetPercent
        next.readyBy = goal.readyBy
        let window = ScheduleEngine.demoWindow(for: goal, weekly: weekly,
                                               remainingMinutes: next.remainingMinutes, now: now())
        next.windowStart = window.start
        next.windowEnd = window.end
        if next.state == .complete { next.batteryPercent = goal.targetPercent }
        if next != snapshot { snapshot = next }
    }

    private func persistDemoSettings() {
        if let data = try? JSONEncoder().encode(ControllerSettings(weekly: weekly, today: today)) {
            defaults.set(data, forKey: demoSettingsKey)
        }
    }
}

private enum DemoError: LocalizedError {
    case unplugged, offline
    var errorDescription: String? {
        switch self {
        case .unplugged: return "Connect the charging cable before starting a charge."
        case .offline: return "Your controller is unavailable. Check its connection and try again."
        }
    }
}
