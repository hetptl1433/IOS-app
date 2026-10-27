import Foundation
import Combine

@MainActor
final class AppStore: ObservableObject {
    @Published private(set) var snapshot: ChargeSnapshot
    @Published private(set) var weekly: WeeklySchedule
    @Published private(set) var today: TodayOverride?
    @Published private(set) var records: [ChargingRecord]
    @Published private(set) var isDemo: Bool
    @Published private(set) var virtualCarEnabled: Bool
    @Published private(set) var controllerName: String?
    @Published private(set) var hasLiveTelemetry = false
    @Published private(set) var isControllerTest = false
    @Published private(set) var busy = false
    @Published private(set) var connectionMessage: String?
    @Published var errorMessage: String?

    private let client: any ControllerClient
    private let defaults: UserDefaults
    private let now: () -> Date
    private var refreshing = false
    private var revision = 0
    private var demoCar: VirtualCarState?
    private var forcedDemoState: ChargeState?
    private let demoSettingsKey = "sparc.demo.settings.v1"
    private let demoVirtualCarKey = "sparc.demo.virtualCarEnabled.v1"

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
        let virtual = demo && !testing && defaults.object(forKey: demoVirtualCarKey) != nil
            ? defaults.bool(forKey: demoVirtualCarKey) : true
        self.virtualCarEnabled = virtual
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
        self.records = demo && virtual ? ChargingRecord.demo(now: now()) : []
        self.controllerName = try? ControllerVault.shared.load()?.deviceName
        if demo {
            if testing, let index = arguments.firstIndex(of: "--state"), arguments.indices.contains(index + 1) {
                forcedDemoState = ChargeState(rawValue: arguments[index + 1])
            }
            applyDemoGoal()
        }
    }

    var effectiveGoal: ScheduleGoal? {
        ScheduleEngine.effectiveGoal(weekly: weekly, today: today, now: now())
    }
    var isVirtualCarActive: Bool { snapshot.virtualCar == true && snapshot.state != .offline }
    var canControlCharging: Bool { hasLiveTelemetry || isVirtualCarActive || (!isDemo && isControllerTest) }

    func setDemoMode(_ enabled: Bool) {
        guard !busy, enabled != isDemo else { return }
        revision += 1
        isDemo = enabled
        defaults.set(enabled, forKey: "sparc.demoMode")
        errorMessage = nil; connectionMessage = nil
        hasLiveTelemetry = false; isControllerTest = false
        today = nil; weekly = .default; demoCar = nil; forcedDemoState = nil
        if enabled {
            virtualCarEnabled = defaults.object(forKey: demoVirtualCarKey) == nil
                || defaults.bool(forKey: demoVirtualCarKey)
            if let data = defaults.data(forKey: demoSettingsKey),
               let saved = try? JSONDecoder().decode(ControllerSettings.self, from: data),
               (try? saved.weekly.validate()) != nil {
                weekly = saved.weekly
                today = saved.today.map({ (try? $0.validate(now: now())) != nil }) == true ? saved.today : nil
            }
            records = virtualCarEnabled ? ChargingRecord.demo(now: now()) : []
            applyDemoGoal()
        } else {
            virtualCarEnabled = true
            snapshot = .disconnected(now: now()); records = []
        }
        controllerName = try? ControllerVault.shared.load()?.deviceName
    }

    func setVirtualCarEnabled(_ enabled: Bool) async {
        guard !busy, enabled != virtualCarEnabled else { return }
        await perform {
            if self.isDemo {
                self.virtualCarEnabled = enabled
                self.defaults.set(enabled, forKey: self.demoVirtualCarKey)
                self.demoCar = nil; self.forcedDemoState = nil
                self.records = enabled ? ChargingRecord.demo(now: self.now()) : []
                self.applyDemoGoal()
                self.persistDemoSettings()
                return
            }
            try await self.client.setVirtualCarEnabled(enabled)
            do {
                let settings = try await self.client.fetchSettings()
                try settings.weekly.validate()
                let update = try await self.client.fetchSnapshot()
                try update.validate()
                let confirmed = settings.virtualCarEnabled ?? update.virtualCar
                guard confirmed == enabled, update.virtualCar == enabled else {
                    throw ControllerConnectionError.unavailable("The controller has not confirmed the Virtual car change. Refresh and retry.")
                }
                self.weekly = settings.weekly
                self.today = (settings.today?.departure ?? .distantPast) > self.now() ? settings.today : nil
                self.virtualCarEnabled = enabled
                self.applySnapshot(update)
                self.connectionMessage = nil
            } catch {
                self.markDisconnected()
                self.connectionMessage = "The Virtual car change was sent, but its state could not be confirmed. Refresh to reconnect."
                throw error
            }
        }
    }

    func refresh() async {
        if isDemo {
            if let today, today.departure <= now() { self.today = nil; persistDemoSettings() }
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
            weekly = settings.weekly
            today = (settings.today?.departure ?? .distantPast) > now() ? settings.today : nil
            if let value = settings.virtualCarEnabled, virtualCarEnabled != value { virtualCarEnabled = value }
            let update = try await client.fetchSnapshot()
            try update.validate()
            guard revision == currentRevision, !isDemo, !busy else { return }
            if settings.virtualCarEnabled == nil, let value = update.virtualCar, virtualCarEnabled != value { virtualCarEnabled = value }
            applySnapshot(update)
            connectionMessage = nil
        } catch {
            guard revision == currentRevision, !isDemo, !busy else { return }
            markDisconnected()
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
        if !isDemo && errorMessage == nil { await refresh() }
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
        if !isDemo && errorMessage == nil { await refresh() }
    }
    func clearToday() async {
        await perform {
            if !self.isDemo { try await self.client.saveToday(nil) }
            self.today = nil
            if self.isDemo { self.applyDemoGoal(); self.persistDemoSettings() }
        }
        if !isDemo && errorMessage == nil { await refresh() }
    }

    func chargeNow() async { await command("charge_now") }
    func pauseCharging() async { await command("pause") }
    func resumeCharging() async { await command("resume") }
    func endCharging() async { await command("end") }

    func forgetController() {
        guard !busy else { return }
        do {
            try ControllerVault.shared.clear()
            revision += 1; controllerName = nil
            if !isDemo {
                snapshot = .disconnected(now: now()); hasLiveTelemetry = false; isControllerTest = false
                virtualCarEnabled = true; weekly = .default; today = nil; records = []
            }
            errorMessage = nil; connectionMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    private func command(_ value: String) async {
        await perform {
            if self.isDemo {
                guard self.virtualCarEnabled, self.snapshot.state != .unplugged else { throw DemoError.unplugged }
                guard self.snapshot.state != .offline else { throw DemoError.offline }
                self.forcedDemoState = nil
                self.applyDemoGoal()
                guard var car = self.demoCar else { throw DemoError.unplugged }
                try car.command(value, now: self.now())
                self.demoCar = car
                self.applySnapshot(car.snapshot(now: self.now()))
            } else {
                try await self.client.command(value)
                do {
                    let update = try await self.client.fetchSnapshot()
                    try update.validate()
                    self.applySnapshot(update)
                    self.connectionMessage = nil
                } catch {
                    self.markDisconnected()
                    self.connectionMessage = "Your request was sent, but its charging state could not be confirmed. Refresh to reconnect."
                    throw error
                }
            }
        }
    }

    private func applySnapshot(_ update: ChargeSnapshot) {
        if snapshot != update { snapshot = update }
        let live = update.virtualCar != true && update.controlOnly != true
        let test = update.virtualCar == true || update.controlOnly == true
        if hasLiveTelemetry != live { hasLiveTelemetry = live }
        if isControllerTest != test { isControllerTest = test }
    }
    private func markDisconnected() {
        snapshot.state = .offline; snapshot.powerKW = 0
        hasLiveTelemetry = false; isControllerTest = false
    }
    private func perform(_ operation: () async throws -> Void) async {
        guard !busy else { return }
        busy = true; revision += 1; errorMessage = nil
        defer { busy = false }
        do { try await operation() }
        catch { errorMessage = error.localizedDescription }
    }
    private func applyDemoGoal() {
        guard isDemo else { return }
        guard virtualCarEnabled else {
            demoCar = nil
            applySnapshot(.noVehicle(now: now(), target: effectiveGoal?.targetPercent ?? weekly.targetPercent))
            return
        }
        var car = demoCar ?? VirtualCarState(now: now())
        car.update(weekly: weekly, today: today, now: now())
        demoCar = car
        var update = car.snapshot(now: now())
        if let forcedDemoState {
            update.state = forcedDemoState
            update.powerKW = forcedDemoState == .charging ? VirtualCarState.powerKW : 0
            if forcedDemoState == .complete { update.batteryPercent = update.targetPercent }
        }
        applySnapshot(update)
    }
    private func persistDemoSettings() {
        if let data = try? JSONEncoder().encode(ControllerSettings(weekly: weekly, today: today, virtualCarEnabled: virtualCarEnabled)) {
            defaults.set(data, forKey: demoSettingsKey)
        }
    }
}

private enum DemoError: LocalizedError {
    case unplugged, offline
    var errorDescription: String? {
        switch self {
        case .unplugged: return "Connect the charging cable or turn on Virtual car before starting a charge."
        case .offline: return "Your controller is unavailable. Check its connection and try again."
        }
    }
}
