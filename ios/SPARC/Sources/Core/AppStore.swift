import Foundation
import Combine
import AuthenticationServices
import UIKit

@MainActor
final class AppStore: ObservableObject {
    @Published private(set) var snapshot: ChargeSnapshot
    @Published private(set) var weekly: WeeklySchedule
    @Published private(set) var today: TodayOverride?
    @Published private(set) var records: [ChargingRecord]
    @Published private(set) var historySessions: [HistorySession] = []
    @Published private(set) var historyWeek: HistoryWeek?
    @Published private(set) var historyUpdatedAt: Date?
    @Published private(set) var historyIsStale = true
    @Published private(set) var historyMessage: String?
    @Published private(set) var historyNextCursor: Int?
    @Published private(set) var loadingHistory = false
    /// The Tesla's charges SPARC started, kept apart from the virtual car's history above.
    @Published private(set) var teslaHistory: TeslaHistory?
    @Published private(set) var teslaHistoryIsStale = true
    @Published private(set) var teslaHistoryMessage: String?
    @Published private(set) var loadingTeslaHistory = false
    @Published private(set) var selectedCarProfile: String?
    @Published private(set) var sleepCalendarStatus: SleepCalendarStatus?
    @Published private(set) var campaign: CampaignSchedule?
    @Published private(set) var campaignIsStale = true
    @Published private(set) var developerState: DeveloperVehicleState?
    @Published private(set) var developerStateIsStale = true
    @Published private(set) var control: ControllerControl?
    @Published private(set) var registeredClients: ControllerClients?
    @Published private(set) var connections: ControllerConnections?
    @Published private(set) var connectionsIsStale = true
    @Published private(set) var teslaAuthorizationInProgress = false
    @Published private(set) var teslaCommandMessage: String?
    @Published private(set) var isDemo: Bool
    @Published private(set) var virtualCarEnabled: Bool
    @Published private(set) var controllerName: String?
    @Published private(set) var hasLiveTelemetry = false
    @Published private(set) var isControllerTest = false
    @Published private(set) var busy = false
    @Published private(set) var connectionMessage: String?
    @Published var errorMessage: String?

    @Published private(set) var combinedSavings: CombinedSavingsReport?
    @Published private(set) var combinedSavingsIsStale = true
    private var lastCombinedSavingsAttempt: Date?
    func combinedSavingsReport(kind: String) async throws -> CombinedSavingsReport {
        let owner=controllerID(), current=revision
        let report=try await client.fetchCombinedSavings(kind:kind)
        try report.validate(kind:kind)
        guard owner==controllerID(),current==revision,!isDemo else{throw ControllerConnectionError.cancelled}
        return report
    }
    func refreshCombinedSavings() async {
        guard !isDemo else{return}
        lastCombinedSavingsAttempt=now()
        do {
            let value = try await combinedSavingsReport(kind: "simulation")
            if combinedSavings != value {
                combinedSavings = value
                if let data = try? JSONEncoder().encode(value) { defaults.set(data, forKey: combinedSavingsCacheKey) }
            }
            combinedSavingsIsStale = false
        }
        catch { combinedSavingsIsStale=true }
    }
    @Published var deliveryClass: ComEdDeliveryClass = .singleFamilyElectric {
        didSet { defaults.set(deliveryClass.rawValue, forKey: "sparc.deliveryClass.v1") }
    }
    var currentSupplyRate: Double? {
        showsTelemetry && snapshot.rateAvailable != false ? snapshot.currentRateCents : nil
    }
    var currentCombinedRate: Double? {
        guard let supply = currentSupplyRate, let delivery = deliveryClass.cents(at: now()) else { return nil }
        return supply + delivery
    }
    func rateHistory(day: String) async throws -> ControllerRatePage {
        let owner = controllerID(), requestRevision = revision
        let page = try await client.fetchRates(day: day)
        try page.validate(requestedDay: day)
        guard owner == controllerID(), requestRevision == revision, !isDemo else { throw ControllerConnectionError.cancelled }
        return page
    }

    lazy var sleepCalendar = AppleSleepCalendar(defaults: defaults)
    var calendarControllerID: String? { controllerID() }
    func sendSleepWindows(_ value: SleepWindowUpdate) async throws {
        guard !isDemo, isVirtualCarActive, !connectionsIsStale, activeConnectionMode == .virtual else { throw ControllerConnectionError.unavailable("Connect to your Pi with Virtual Car selected to sync Calendar.") }
        let owner = controllerID()
        try await client.saveSleepWindows(value)
        guard owner == controllerID() else { throw ControllerConnectionError.cancelled }
    }
    /// Read back schedule ownership before presenting manual editing as active.
    func refreshScheduleState() async throws {
        let owner = controllerID(), expectedRevision = revision
        try await readCanonicalState(expectedRevision: expectedRevision)
        guard owner == controllerID(), revision == expectedRevision else { throw ControllerConnectionError.cancelled }
    }
    private let client: any ControllerClient
    private let defaults: UserDefaults
    private let now: () -> Date
    private let controllerID: () -> String?
    private let controllerNameProvider: () -> String?
    private var historyOwnerID: String
    private var refreshing = false
    private var lastHistoryAttempt: Date?
    private let cacheEncoder = ControllerCacheEncoder()
    private var lastPersistedCache: ControllerDataCache?
    private var lastCacheWrite: Date?
    private var cacheWriteGeneration = 0
    private let allowsInterfaceFixtures: Bool
    private var revision = 0
    private var demoCar: VirtualCarState?
    private var forcedDemoState: ChargeState?
    private let teslaAuthentication = TeslaAuthenticationCoordinator()
    private var completingTeslaStates: Set<String> = []
    private var completedTeslaStates: [String] = []
    private var teslaAuthorizationOwner: String?
    private let demoSettingsKey = "sparc.demo.settings.v1"
    private let demoVirtualCarKey = "sparc.demo.virtualCarEnabled.v1"

    init(defaults: UserDefaults = .standard,
         client: any ControllerClient = LocalControllerClient(),
         now: @escaping () -> Date = Date.init,
         arguments: [String] = ProcessInfo.processInfo.arguments,
         controllerID: @escaping () -> String? = { try? ControllerVault.shared.load()?.deviceID },
         controllerNameProvider: @escaping () -> String? = { try? ControllerVault.shared.load()?.deviceName }) {
        self.defaults = defaults
        self.deliveryClass = defaults.string(forKey: "sparc.deliveryClass.v1").flatMap(ComEdDeliveryClass.init(rawValue:)) ?? .singleFamilyElectric
        self.client = client
        self.now = now
        self.controllerID = controllerID
        self.controllerNameProvider = controllerNameProvider
        self.historyOwnerID = controllerID() ?? "local"
        self.selectedCarProfile = defaults.string(forKey: "sparc.carProfile.v1." + (controllerID() ?? "local"))
        #if DEBUG
        let testing = arguments.contains("--uitesting")
        #else
        let testing = false
        #endif
        self.allowsInterfaceFixtures = testing
        let demo = testing
        if !testing { defaults.set(false, forKey: "sparc.demoMode") }
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
        self.controllerName = controllerNameProvider()
        if demo {
            if testing, let index = arguments.firstIndex(of: "--state"), arguments.indices.contains(index + 1) {
                forcedDemoState = ChargeState(rawValue: arguments[index + 1])
            }
            applyDemoGoal()
        } else { restoreCachedControllerData() }
    }

    var effectiveGoal: ScheduleGoal? {
        if profileConnectionMode == .tesla { return nil }
        if !isDemo, let calendar = sleepCalendarStatus, calendar.enabled {
            guard let wake = calendar.nextWake, wake > now() else { return nil }
            return ScheduleGoal(targetPercent: today?.targetPercent ?? weekly.targetPercent, departure: wake,
                                readyBy: wake.addingTimeInterval(-1800), isOverride: false)
        }
        if !isDemo, let campaign {
            guard campaign.enabled else { return nil }
            if let current = campaign.current, current.finalizedAt == nil, current.departure > now() {
                return ScheduleGoal(targetPercent: current.targetBatteryPercent, departure: current.departure,
                                    readyBy: current.departure, isOverride: false)
            }
            if let start = campaign.nextStart {
                let calendar = ScheduleEngine.chicagoCalendar
                if var departure = calendar.date(bySettingHour: campaign.departureMinutes / 60,
                                                 minute: campaign.departureMinutes % 60, second: 0, of: start) {
                    if departure <= start { departure = calendar.date(byAdding: .day, value: 1, to: departure) ?? departure }
                    return ScheduleGoal(targetPercent: campaign.targetBatteryPercent, departure: departure,
                                        readyBy: departure, isOverride: false)
                }
            }
            return nil
        }
        return ScheduleEngine.effectiveGoal(weekly: weekly, today: today, now: now())
    }
    var campaignWindow: DateInterval? {
        if !isDemo, let calendar = sleepCalendarStatus, calendar.enabled, let start = calendar.nextStart, let goal = effectiveGoal {
            return DateInterval(start: start, end: goal.departure)
        }
        guard !isDemo, let campaign, campaign.enabled, let goal = effectiveGoal else { return nil }
        if let current = campaign.current, current.finalizedAt == nil, current.departure > now() {
            return DateInterval(start: current.start, end: current.departure)
        }
        return campaign.nextStart.map { DateInterval(start: $0, end: goal.departure) }
    }
    var activeConnectionMode: ControllerConnectionMode? {
        connections?.activeMode ?? (snapshot.virtualCar == true ? .virtual : nil)
    }
    var profileConnectionMode: ControllerConnectionMode? {
        if selectedCarProfile == "virtual" { return .virtual }
        if let key = selectedCarProfile, key.hasPrefix("tesla:"),
           connections?.tesla.vehicles?.contains(where: { "tesla:" + $0.id == key }) == true { return .tesla }
        return activeConnectionMode
    }
    var profileTeslaID: String? {
        guard profileConnectionMode == .tesla else { return nil }
        if let key = selectedCarProfile, key.hasPrefix("tesla:"), connections?.tesla.vehicles?.contains(where: { "tesla:" + $0.id == key }) == true { return String(key.dropFirst(6)) }
        return connections?.tesla.vehicleId
    }
    var profileTeslaReading: ControllerConnections.Tesla.ChargeState? {
        guard let id = profileTeslaID, id == connections?.tesla.vehicleId else { return nil }
        return connections?.tesla.chargeState
    }
    /// Viewing a profile never requires Tesla's permission to send charging commands.
    var activeCarProfileID: String {
        let owner = controllerID() ?? "local"
        return owner + (profileConnectionMode == .tesla ? ":tesla:" + (profileTeslaID ?? "unselected") : ":virtual")
    }
    func chooseCarProfile(_ key: String) {
        guard key == "virtual" || connections?.tesla.vehicles?.contains(where: { "tesla:" + $0.id == key }) == true else { return }
        selectedCarProfile = key
        defaults.set(key, forKey: "sparc.carProfile.v1." + (controllerID() ?? "local"))
        errorMessage = nil; teslaCommandMessage = nil
    }
    /// Wait for the prior mutation, then prepare only the latest selected car. UI selection is immediate.
    func prepareCarProfile() async {
        let requested = activeCarProfileID, wanted = profileTeslaID
        for _ in 0..<100 {
            guard !Task.isCancelled, activeCarProfileID == requested else { return }
            if !busy && !refreshing { break }
            do { try await Task.sleep(for: .milliseconds(150)) } catch { return }
        }
        guard !Task.isCancelled, activeCarProfileID == requested, !isDemo, !busy, !connectionsIsStale else { return }
        if let wanted {
            if connections?.tesla.vehicleId != wanted {
                if activeConnectionMode == .tesla {
                    await activateConnection(.virtual)
                    guard activeCarProfileID == requested, errorMessage == nil, !Task.isCancelled else { return }
                }
                await selectTeslaVehicle(wanted)
                guard activeCarProfileID == requested, errorMessage == nil, !Task.isCancelled else { return }
            }
            await refreshTesla()
            guard activeCarProfileID == requested, !Task.isCancelled else { return }
            await refreshTeslaHistory()
        } else if activeConnectionMode == .tesla { await activateConnection(.virtual) }
    }
    func refreshProfileTesla() async {
        await prepareCarProfile()
    }
    var activeConnectionTitle: String { activeConnectionMode?.title ?? "Not confirmed" }
    /// The car SPARC is charging with, by the name the owner knows it by.
    var chargingCarName: String {
        switch profileConnectionMode {
        case .tesla?:
            let cars = connections?.tesla.vehicles ?? []
            return cars.first(where: { $0.id == profileTeslaID }).map { $0.name(among: cars) } ?? (profileTeslaID == connections?.tesla.vehicleId ? connections?.tesla.vehicleName : nil) ?? "Your Tesla"
        case .virtual?: return "Virtual \(connections?.virtual.model.title ?? "car")"
        default: return "Not confirmed"
        }
    }
    var canSendTeslaCommands: Bool {
        !isDemo && !connectionsIsStale && activeConnectionMode == .tesla
            && profileConnectionMode == .tesla && profileTeslaID == connections?.tesla.vehicleId
            && connections?.tesla.authorized == true && connections?.tesla.commandsReady == true
    }
    var isVirtualCarActive: Bool { activeConnectionMode != .tesla && snapshot.virtualCar == true && snapshot.state != .offline }
    var canControlCharging: Bool {
        if profileConnectionMode == .tesla { return canSendTeslaCommands }
        return hasLiveTelemetry || isVirtualCarActive || (!isDemo && isControllerTest)
    }

    func setDemoMode(_ enabled: Bool) {
        guard !enabled || allowsInterfaceFixtures else { return }
        guard !busy, enabled != isDemo else { return }
        revision += 1
        invalidateCacheWrites()
        isDemo = enabled
        defaults.set(enabled, forKey: "sparc.demoMode")
        errorMessage = nil; connectionMessage = nil
        hasLiveTelemetry = false; isControllerTest = false
        today = nil; weekly = .default; demoCar = nil; forcedDemoState = nil
        campaign = nil; campaignIsStale = true
        developerState = nil; developerStateIsStale = true; control = nil; registeredClients = nil
        connections = nil; connectionsIsStale = true
        historySessions = []; historyWeek = nil; historyUpdatedAt = nil
        historyNextCursor = nil; historyIsStale = true; historyMessage = nil
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
            restoreCachedControllerData()
        }
        let name = controllerNameProvider()
        if controllerName != name { controllerName = name }
    }

    func setVirtualCarEnabled(_ enabled: Bool) async {
        guard !busy, enabled != virtualCarEnabled else { return }
        guard isDemo || activeConnectionMode != .tesla else {
            errorMessage = "Choose Virtual Car as the charging provider before changing the simulated car."; return
        }
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
        let name = controllerNameProvider()
        if controllerName != name { controllerName = name }
        ensureHistoryOwner()
        do {
            try await readCanonicalState(expectedRevision: currentRevision)
            guard revision == currentRevision, !isDemo, !busy else { return }
            if lastHistoryAttempt == nil || now().timeIntervalSince(lastHistoryAttempt!) >= 30 { await refreshHistory() }
            await sleepCalendar.refresh(store: self)
            if lastCombinedSavingsAttempt == nil || now().timeIntervalSince(lastCombinedSavingsAttempt!) >= 60 { await refreshCombinedSavings() }
        } catch {
            guard revision == currentRevision, !isDemo, !busy else { return }
            markDisconnected()
            if connectionMessage != error.localizedDescription { connectionMessage = error.localizedDescription }
        }
    }

    func refreshHistory(loadMore: Bool = false) async {
        guard !isDemo, !busy, !loadingHistory else { return }
        if loadMore && historyNextCursor == nil { return }
        ensureHistoryOwner()
        let requestRevision = revision, owner = historyOwnerID
        let cursor = loadMore ? historyNextCursor : nil
        loadingHistory = true
        lastHistoryAttempt = now()
        defer { loadingHistory = false }
        do {
            let page = try await client.fetchHistory(cursor: cursor)
            try page.validate()
            guard !isDemo, revision == requestRevision, historyOwnerID == owner else { return }
            var combined = (loadMore || page.nextCursor != nil) ? historySessions : []
            for session in page.sessions {
                if let index = combined.firstIndex(where: { $0.id == session.id }) { combined[index] = session }
                else { combined.append(session) }
            }
            let sessions = Array(combined.sorted { $0.date > $1.date }.prefix(1000))
            if historySessions != sessions {
                historySessions = sessions
                records = sessions.compactMap(\.pricedRecord)
            }
            if historyWeek != page.week { historyWeek = page.week }
            if historyUpdatedAt != page.generatedAt { historyUpdatedAt = page.generatedAt }
            let cursor = page.nextCursor.map { max($0, sessions.count) }
            if historyNextCursor != cursor { historyNextCursor = cursor }
            if historyIsStale { historyIsStale = false }
            if historyMessage != nil { historyMessage = nil }
            await persistControllerData()
        } catch {
            guard !isDemo, revision == requestRevision, historyOwnerID == owner else { return }
            if !historyIsStale { historyIsStale = true }
            if historyMessage != error.localizedDescription { historyMessage = error.localizedDescription }
        }
    }

    private var historyTeslaID: String? {
        profileConnectionMode == .tesla ? profileTeslaID : connections?.tesla.vehicleId
    }
    /// A Tesla profile owns its history. Explicit background reads can also retain the Pi's selected Tesla cache.
    var selectedTeslaHistory: TeslaHistory? {
        guard let vehicleId = historyTeslaID, teslaHistory?.vehicleId == vehicleId else { return nil }
        return teslaHistory
    }

    func refreshTeslaHistory(loadMore: Bool = false) async {
        guard !isDemo, !busy, !loadingTeslaHistory else { return }
        ensureHistoryOwner()
        guard let vehicleId = historyTeslaID else { return }
        if teslaHistory != nil, teslaHistory?.vehicleId != vehicleId {
            teslaHistory = nil; teslaHistoryIsStale = true; teslaHistoryMessage = nil
        }
        if loadMore && teslaHistory?.nextCursor == nil { return }
        let requestRevision = revision, owner = historyOwnerID
        let cursor = loadMore ? teslaHistory?.nextCursor : nil
        loadingTeslaHistory = true
        defer { loadingTeslaHistory = false }
        do {
            let page = try await client.fetchTeslaHistory(vehicleId: vehicleId, cursor: cursor)
            try page.validate(teslaVehicleId: vehicleId)
            guard !isDemo, revision == requestRevision, historyOwnerID == owner,
                  historyTeslaID == vehicleId else { return }
            var combined = (loadMore || page.nextCursor != nil) ? (teslaHistory?.sessions ?? []) : []
            for session in page.sessions {
                if let index = combined.firstIndex(where: { $0.id == session.id }) { combined[index] = session }
                else { combined.append(session) }
            }
            let sessions = Array(combined.sorted { $0.start > $1.start }.prefix(1000))
            let value = TeslaHistory(vehicleId: vehicleId, generatedAt: page.generatedAt, week: page.week,
                                     sessions: sessions, nextCursor: page.nextCursor.map { max($0, sessions.count) })
            if teslaHistory != value { teslaHistory = value }
            if teslaHistoryIsStale { teslaHistoryIsStale = false }
            if teslaHistoryMessage != nil { teslaHistoryMessage = nil }
            await persistControllerData()
        } catch {
            guard !isDemo, revision == requestRevision, historyOwnerID == owner else { return }
            if !teslaHistoryIsStale { teslaHistoryIsStale = true }
            // A Pi without Tesla history rejects the car filter as an invalid page.
            let message = error.localizedDescription == "Choose a valid history page."
                ? LocalControllerClient.teslaHistoryUpdateMessage : error.localizedDescription
            if teslaHistoryMessage != message { teslaHistoryMessage = message }
        }
    }

    func saveCampaign(_ value: CampaignUpdate) async {
        guard !isDemo, !busy else { return }
        do { try value.validate() } catch { errorMessage = error.localizedDescription; return }
        await perform {
            try await self.client.saveCampaign(value)
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch { self.markDisconnected(); throw error }
            guard self.campaign != nil else {
                throw ControllerConnectionError.unavailable("The controller has not confirmed the nightly schedule. Refresh before trying again.")
            }
        }
        // Re-read the canonical revision after both successful and rejected edits.
        if errorMessage != nil { await refresh() }
    }

    func setVirtualCarState(batteryPercent: Double, pluggedIn: Bool) async {
        guard !isDemo, !busy, activeConnectionMode == .virtual, !developerStateIsStale, developerState?.enabled == true,
              batteryPercent.isFinite, (0...100).contains(batteryPercent) else {
            errorMessage = "Connect to the Pi and choose a battery percentage from 0 to 100."
            return
        }
        await perform {
            try await self.client.setVirtualCarState(batteryPercent: batteryPercent, pluggedIn: pluggedIn)
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch {
                self.markDisconnected()
                self.connectionMessage = "The car-state request was sent, but the latest controller state could not be confirmed. Reconnect and refresh."
                throw error
            }
        }
    }

    func saveConnectionSettings(_ value: ConnectionSettingsUpdate) async {
        guard !isDemo, !busy, !connectionsIsStale else {
            errorMessage = "Connect to the Pi before changing its connections."; return
        }
        do { try value.validate() } catch { errorMessage = error.localizedDescription; return }
        await perform {
            try await self.client.saveConnectionSettings(value)
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch { self.markDisconnected(); throw error }
        }
        if errorMessage != nil { await refresh() }
    }

    func checkSmartPlug(password: String? = nil) async {
        guard !isDemo, !busy, !connectionsIsStale else {
            errorMessage = "Connect to the Pi before checking the smart plug."; return
        }
        await perform {
            try await self.client.checkSmartPlug(password: password)
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch { self.markDisconnected(); throw error }
        }
    }

    func setSmartPlugPower(on: Bool) async {
        guard !isDemo, !busy, !connectionsIsStale, connections?.smartPlug.verified == true else {
            errorMessage = "Connect to the Pi and verify the Shelly plug first."; return
        }
        await perform {
            try await self.client.setSmartPlugPower(on: on)
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch { self.markDisconnected(); throw error }
        }
    }

    /// `freshSignIn` opens Tesla's sign-in without Safari's saved Tesla login, so the owner can choose another account.
    func authorizeTesla(freshSignIn: Bool = false) async {
        guard !isDemo, !busy, !teslaAuthorizationInProgress, !connectionsIsStale, connections?.tesla.configured == true else {
            errorMessage = "Tesla needs private configuration on the Pi before authorization."; return
        }
        teslaAuthorizationInProgress = true
        defer { teslaAuthorizationInProgress = false }
        var url: URL?
        await perform {
            let authorization = try await self.client.authorizeTesla()
            try authorization.validate()
            try await self.readCanonicalState(expectedRevision: self.revision)
            url = authorization.authorizationURL
        }
        guard let url else { return }
        teslaAuthorizationOwner = historyOwnerID
        defer { teslaAuthorizationOwner = nil }
        do {
            let callback = try await teslaAuthentication.authenticate(url, freshSignIn: freshSignIn)
            await completeTeslaCallback(callback)
        } catch {
            if (error as? ASWebAuthenticationSessionError)?.code == .canceledLogin { return }
            if let connectionError = error as? ControllerConnectionError, case .cancelled = connectionError { return }
            errorMessage = error.localizedDescription
        }
    }

    func handleTeslaCallback(_ url: URL) async {
        guard TeslaCallback.isCallback(url) else { return }
        if teslaAuthentication.receiveExternalCallback(url) { return }
        await completeTeslaCallback(url)
    }

    private func completeTeslaCallback(_ url: URL) async {
        let callback: TeslaCallback
        do { callback = try TeslaCallback(url) }
        catch {
            if let connectionError = error as? ControllerConnectionError, case .cancelled = connectionError { return }
            errorMessage = error.localizedDescription; return
        }
        guard !completingTeslaStates.contains(callback.state), !completedTeslaStates.contains(callback.state) else { return }
        guard teslaAuthorizationOwner == nil || teslaAuthorizationOwner == (controllerID() ?? "local") else {
            errorMessage = "The paired Pi changed during Tesla approval. Connect Tesla again for this Pi."; return
        }
        completingTeslaStates.insert(callback.state)
        defer { completingTeslaStates.remove(callback.state) }
        // A callback may arrive while a local save is completing. Do not lose it
        // or send the same authorization code twice through two callback routes.
        for _ in 0..<1000 {
            if !busy { break }
            do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
        }
        guard !busy else { errorMessage = "Finish the current change, then reconnect Tesla."; return }
        await perform {
            // A code is single-use. Even an uncertain response is followed by a
            // refresh or a new authorization, never an automatic second send.
            self.completedTeslaStates.append(callback.state)
            self.completedTeslaStates = Array(self.completedTeslaStates.suffix(16))
            try await self.client.completeTeslaAuthorization(code: callback.code, state: callback.state)
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch { self.markDisconnected(); throw error }
        }
    }

    func refreshTesla() async {
        await teslaOperation {
            do { try await self.client.refreshTesla() }
            catch {
                // The Pi saves the car list and approval status before a sleeping car fails its battery read; show them.
                try? await self.readCanonicalState(expectedRevision: self.revision)
                throw error
            }
        }
    }
    func selectTeslaVehicle(_ id: String) async {
        guard !id.isEmpty, id.utf8.count <= 128 else { return }
        teslaCommandMessage = nil
        await teslaOperation { try await self.client.selectTeslaVehicle(id) }
    }
    /// Home must never point at a car SPARC can no longer reach, so the virtual car takes over before Tesla is removed.
    func disconnectTesla() async {
        teslaCommandMessage = nil
        guard await leaveTeslaForVirtualCar() else { return }
        await teslaOperation { try await self.client.disconnectTesla(accountId: nil) }
    }
    /// On a Pi that holds one Tesla account, a new sign-in replaces the car list, so charging moves to the
    /// virtual car until a car from that account is chosen. A Pi that holds several accounts adds it instead.
    func switchTeslaAccount() async {
        if teslaHoldsSeveralAccounts { await addTeslaAccount(); return }
        guard await leaveTeslaForVirtualCar() else { return }
        await authorizeTesla(freshSignIn: true)
    }
    /// True when the Pi lists its Tesla accounts, so one more can be added and each removed on its own.
    var teslaHoldsSeveralAccounts: Bool { connections?.tesla.accounts != nil }
    var teslaAccounts: [ControllerConnections.Tesla.Account] { connections?.tesla.accounts ?? [] }
    /// Signs in to one more Tesla account. The car SPARC charges with stays as it is.
    func addTeslaAccount() async {
        guard teslaHoldsSeveralAccounts else { await switchTeslaAccount(); return }
        await authorizeTesla(freshSignIn: true)
    }
    /// Forgets one Tesla account. If it owns the car in use, charging moves to the virtual car first.
    func removeTeslaAccount(_ id: String) async {
        teslaCommandMessage = nil
        guard teslaHoldsSeveralAccounts else { await disconnectTesla(); return }
        guard teslaAccounts.contains(where: { $0.id == id }) else { return }
        if connections?.tesla.accountId == id || teslaAccounts.count == 1 {
            guard await leaveTeslaForVirtualCar() else { return }
        }
        await teslaOperation { try await self.client.disconnectTesla(accountId: id) }
    }
    /// Charges with one Tesla. The Pi only changes car while the virtual car is in use, so a different
    /// Tesla goes through the virtual car first; if the new car can't take over, the virtual car stays.
    func useTeslaCar(_ id: String) async {
        teslaCommandMessage = nil
        guard connections?.tesla.vehicles?.contains(where: { $0.id == id }) == true else { return }
        if activeConnectionMode == .tesla, connections?.tesla.vehicleId == id { return }
        if connections?.tesla.vehicleId != id {
            guard await leaveTeslaForVirtualCar() else { return }
            await selectTeslaVehicle(id)
            guard errorMessage == nil, connections?.tesla.vehicleId == id else { return }
        }
        await useTeslaForCharging()
    }
    /// Reads the car again first, because the Pi only switches to a Tesla whose current state it has just received.
    func useTeslaForCharging() async {
        await refreshTesla()
        guard errorMessage == nil else { return }
        await activateConnection(.tesla)
    }
    private func leaveTeslaForVirtualCar() async -> Bool {
        guard activeConnectionMode == .tesla else { return true }
        await activateConnection(.virtual)
        return errorMessage == nil && activeConnectionMode == .virtual
    }
    func activateConnection(_ mode: ControllerConnectionMode) async {
        guard !isDemo, !busy, !connectionsIsStale, mode == .virtual || mode == .tesla else {
            errorMessage = "Connect to the Pi before selecting a charging provider."; return
        }
        if mode == .tesla, connections?.tesla.commandsReady != true {
            errorMessage = "Approve SPARC in the Tesla app before charging with your Tesla."; return
        }
        await perform {
            try await self.client.activateConnection(mode)
            do {
                try await self.readCanonicalState(expectedRevision: self.revision)
                guard self.connections?.activeMode == mode else {
                    throw ControllerConnectionError.unavailable("The Pi has not confirmed the active charging provider. Refresh before using charging controls.")
                }
                self.teslaCommandMessage = nil
            } catch { self.markDisconnected(); throw error }
        }
    }
    func sendTeslaCommand(_ command: TeslaChargeCommand, value: Int? = nil) async {
        guard !busy, canSendTeslaCommands else {
            errorMessage = "Use Tesla for charging and refresh verification before sending a command."; return
        }
        do { try command.validate(value: value) } catch { errorMessage = error.localizedDescription; return }
        teslaCommandMessage = nil
        await perform {
            try await self.client.teslaCommand(command, value: value)
            self.teslaCommandMessage = command == .setLimit
                ? "Tesla accepted the \(value!)% charge limit. Refresh for the latest vehicle reading."
                : "Tesla accepted \(command.title.lowercased()). Refresh for the latest vehicle reading."
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch {
                self.markDisconnected()
                self.connectionMessage = "Tesla accepted the command, but the latest controller state could not be confirmed. Reconnect and refresh."
                throw error
            }
        }
    }
    private func teslaOperation(_ operation: () async throws -> Void) async {
        guard !isDemo, !busy, !connectionsIsStale else { errorMessage = "Connect to the Pi before changing Tesla access."; return }
        await perform {
            try await operation()
            do { try await self.readCanonicalState(expectedRevision: self.revision) }
            catch { self.markDisconnected(); throw error }
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
            defaults.removeObject(forKey: historyCacheKey)
            invalidateCacheWrites()
            revision += 1; controllerName = nil
            if !isDemo {
                snapshot = .disconnected(now: now()); hasLiveTelemetry = false; isControllerTest = false
                virtualCarEnabled = true; weekly = .default; today = nil; records = []
                historySessions = []; historyWeek = nil; historyUpdatedAt = nil
                historyNextCursor = nil; historyIsStale = true; historyMessage = nil
                teslaHistory = nil; teslaHistoryIsStale = true; teslaHistoryMessage = nil
                campaign = nil; campaignIsStale = true
                developerState = nil; developerStateIsStale = true; control = nil; registeredClients = nil
                connections = nil; connectionsIsStale = true
            }
            errorMessage = nil; connectionMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    private func command(_ value: String) async {
        if !isDemo, activeConnectionMode == .tesla {
            await sendTeslaCommand(value == "pause" || value == "end" ? .stop : .start)
            return
        }
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
                    try await self.readCanonicalState(expectedRevision: self.revision)
                } catch {
                    self.markDisconnected()
                    self.connectionMessage = "Your request was sent, but its charging state could not be confirmed. Refresh to reconnect."
                    throw error
                }
            }
        }
    }

    private func applySnapshot(_ update: ChargeSnapshot) {
        if let incoming = update.control?.sequence, let current = control?.sequence, incoming < current { return }
        if let incoming = update.control, control != incoming { control = incoming }
        if snapshot != update { snapshot = update }
        let live = update.virtualCar != true && update.controlOnly != true && update.state != .offline && update.telemetryStale != true
        let test = update.virtualCar == true || update.controlOnly == true
        if hasLiveTelemetry != live { hasLiveTelemetry = live }
        if isControllerTest != test { isControllerTest = test }
    }
    private func markDisconnected() {
        combinedSavingsIsStale=true
        if snapshot.state != .offline { snapshot.state = .offline }
        if snapshot.powerKW != 0 { snapshot.powerKW = 0 }
        if hasLiveTelemetry { hasLiveTelemetry = false }
        if isControllerTest { isControllerTest = false }
        if !historyIsStale { historyIsStale = true }
        if !teslaHistoryIsStale { teslaHistoryIsStale = true }
        if !campaignIsStale { campaignIsStale = true }
        if !developerStateIsStale { developerStateIsStale = true }
        if !connectionsIsStale { connectionsIsStale = true }
    }
    private func readCanonicalState(expectedRevision: Int) async throws {
        let settings = try await client.fetchSettings()
        try settings.weekly.validate(); try settings.campaign?.validate()
        try settings.developer?.validate(); try settings.control?.validate()
        try settings.connections?.validate()
        try settings.sleepCalendar?.validate()
        if let override = settings.today, override.departure > now() { try override.validate(now: now()) }
        guard revision == expectedRevision, !isDemo else { return }
        let override = (settings.today?.departure ?? .distantPast) > now() ? settings.today : nil
        let previousProfile = activeCarProfileID
        let configurationChanged = weekly != settings.weekly || today != override || campaign != settings.campaign
            || control?.sequence != settings.control?.sequence || connections?.revision != settings.connections?.revision
        // Settings are independently authenticated and useful even if vehicle
        // telemetry is temporarily unavailable. Keep their confirmed values.
        if (settings.control?.sequence ?? Int.max) >= (control?.sequence ?? 0) {
            if weekly != settings.weekly { weekly = settings.weekly }
            if campaign != settings.campaign { campaign = settings.campaign }
            if campaignIsStale { campaignIsStale = false }
            if today != override { today = override }
            if developerState != settings.developer { developerState = settings.developer }
            if developerStateIsStale != (settings.developer == nil) { developerStateIsStale = settings.developer == nil }
            if let value = settings.control, control != value { control = value }
            if registeredClients != settings.clients { registeredClients = settings.clients }
            if connections != settings.connections { connections = settings.connections }
            if sleepCalendarStatus != settings.sleepCalendar { sleepCalendarStatus = settings.sleepCalendar }
            if activeCarProfileID != previousProfile {
                snapshot = .disconnected(now: now()); hasLiveTelemetry = false; isControllerTest = false
                teslaHistory = nil; teslaHistoryIsStale = true
                errorMessage = nil
            }
            if connectionsIsStale != (settings.connections == nil) { connectionsIsStale = settings.connections == nil }
            if let value = settings.virtualCarEnabled, virtualCarEnabled != value { virtualCarEnabled = value }
        }
        let update: ChargeSnapshot
        do {
            update = try await client.fetchSnapshot()
            try update.validate()
        } catch {
            await persistControllerData(force: busy || configurationChanged)
            throw error
        }
        guard revision == expectedRevision, !isDemo else { return }
        if settings.virtualCarEnabled == nil, let value = update.virtualCar, virtualCarEnabled != value { virtualCarEnabled = value }
        applySnapshot(update)
        if connectionMessage != nil { connectionMessage = nil }
        await persistControllerData(force: busy || configurationChanged)
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

    private var historyCacheKey: String { "sparc.controller.history.v1." + historyOwnerID }
    private func ensureHistoryOwner() {
        let owner = controllerID() ?? "local"
        guard owner != historyOwnerID else { return }
        invalidateCacheWrites()
        historyOwnerID = owner
        selectedCarProfile = defaults.string(forKey: "sparc.carProfile.v1." + owner)
        combinedSavings=nil;combinedSavingsIsStale=true;lastCombinedSavingsAttempt=nil
        sleepCalendarStatus=nil
        records = []; historySessions = []; historyWeek = nil; historyUpdatedAt = nil
        historyNextCursor = nil; campaign = nil; historyIsStale = true; campaignIsStale = true
        developerState = nil; developerStateIsStale = true; control = nil; registeredClients = nil; lastHistoryAttempt = nil
        connections = nil; connectionsIsStale = true
        historyMessage = nil
        teslaHistory = nil; teslaHistoryIsStale = true; teslaHistoryMessage = nil
        restoreCachedControllerData()
    }
    private var combinedSavingsCacheKey: String { "sparc.combinedSavings.v1." + historyOwnerID }
    private func restoreCachedControllerData() {
        if !isDemo, let data = defaults.data(forKey: combinedSavingsCacheKey), data.count <= 65536,
           let report = try? JSONDecoder().decode(CombinedSavingsReport.self, from: data),
           (try? report.validate(kind: "simulation")) != nil {
            combinedSavings = report; combinedSavingsIsStale = true
        }
        guard !isDemo, let data = defaults.data(forKey: historyCacheKey),
              data.count <= 2 * 1024 * 1024,
              let cache = try? JSONDecoder().decode(ControllerDataCache.self, from: data),
              cache.sessions.count <= 1000,
              (try? cache.week?.validate()) != nil || cache.week == nil,
              cache.sessions.allSatisfy({ (try? $0.validate()) != nil }),
              (try? cache.campaign?.validate()) != nil || cache.campaign == nil else { return }
        historySessions = cache.sessions; historyWeek = cache.week; historyUpdatedAt = cache.generatedAt
        historyNextCursor = cache.nextCursor; campaign = cache.campaign
        if (try? cache.developer?.validate()) != nil { developerState = cache.developer }
        if (try? cache.control?.validate()) != nil { control = cache.control }
        registeredClients = cache.clients
        if let value = cache.connections, (try? value.validate()) != nil { connections = value }
        if let value = cache.tesla, (try? value.validate()) != nil { teslaHistory = value; teslaHistoryIsStale = true }
        records = cache.sessions.compactMap(\.pricedRecord)
        historyIsStale = true; campaignIsStale = true
        developerStateIsStale = true
        connectionsIsStale = true
        lastPersistedCache = cache; lastCacheWrite = now()
    }
    private func invalidateCacheWrites() {
        cacheWriteGeneration += 1; lastPersistedCache = nil; lastCacheWrite = nil
    }
    private func persistControllerData(force: Bool = true) async {
        guard !isDemo else { return }
        let cache = ControllerDataCache(generatedAt: historyUpdatedAt, week: historyWeek,
                                        sessions: historySessions, nextCursor: historyNextCursor, campaign: campaign,
                                        developer: developerState, control: control, clients: registeredClients, connections: connections,
                                        tesla: teslaHistory)
        guard cache != lastPersistedCache else { return }
        if !force, let lastCacheWrite, now().timeIntervalSince(lastCacheWrite) < 30 { return }
        cacheWriteGeneration += 1
        let generation = cacheWriteGeneration, key = historyCacheKey, owner = historyOwnerID
        guard let data = await cacheEncoder.encode(cache), data.count <= 2 * 1024 * 1024,
              generation == cacheWriteGeneration, owner == historyOwnerID, !isDemo else { return }
        defaults.set(data, forKey: key)
        lastPersistedCache = cache; lastCacheWrite = now()
    }
}

private struct ControllerDataCache: Codable, Equatable, Sendable {
    var generatedAt: Date?
    var week: HistoryWeek?
    var sessions: [HistorySession]
    var nextCursor: Int?
    var campaign: CampaignSchedule?
    var developer: DeveloperVehicleState? = nil
    var control: ControllerControl? = nil
    var clients: ControllerClients? = nil
    var connections: ControllerConnections? = nil
    var tesla: TeslaHistory? = nil
}

private actor ControllerCacheEncoder {
    func encode(_ cache: ControllerDataCache) -> Data? { try? JSONEncoder().encode(cache) }
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

private struct TeslaCallback {
    let code: String
    let state: String
    static func isCallback(_ url: URL) -> Bool {
        url.scheme?.lowercased() == "sparc" && url.host?.lowercased() == "tesla" && url.path == "/callback"
    }
    init(_ url: URL) throws {
        guard Self.isCallback(url), url.user == nil, url.password == nil, url.port == nil, url.fragment == nil,
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              Set(items.map(\.name)).count == items.count,
              Set(items.map(\.name)).isSubset(of: ["code", "state", "error"]) else { throw ControllerConnectionError.invalidMessage }
        if let failure = items.first(where: { $0.name == "error" })?.value {
            if failure == "authorization_cancelled" || failure == "access_denied" { throw ControllerConnectionError.cancelled }
            throw ControllerConnectionError.unavailable("Tesla did not authorize this connection. Try connecting again.")
        }
        guard let code = items.first(where: { $0.name == "code" })?.value,
              let state = items.first(where: { $0.name == "state" })?.value,
              !code.isEmpty, code.utf8.count <= 4096,
              !code.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
              state.utf8.count == 64,
              state.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) })
        else { throw ControllerConnectionError.invalidMessage }
        self.code = code; self.state = state
    }
}

@MainActor
private final class TeslaAuthenticationCoordinator: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?
    private var continuation: CheckedContinuation<URL, Error>?
    func authenticate(_ url: URL, freshSignIn: Bool) async throws -> URL {
        guard session == nil else { throw ControllerConnectionError.unavailable("Finish the current Tesla sign-in first.") }
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                self.continuation = continuation
                let session = ASWebAuthenticationSession(url: url, callbackURLScheme: "sparc") { [weak self] callback, error in
                    Task { @MainActor in
                        if let callback { self?.finish(.success(callback)) }
                        else { self?.finish(.failure(error ?? ControllerConnectionError.cancelled)) }
                    }
                }
                session.presentationContextProvider = self
                session.prefersEphemeralWebBrowserSession = freshSignIn
                self.session = session
                if !session.start() { finish(.failure(ControllerConnectionError.unavailable("Tesla sign-in could not open. Try again while the app is active."))) }
            }
        }, onCancel: { [weak self] in Task { @MainActor in self?.finish(.failure(ControllerConnectionError.cancelled)) } })
    }
    func receiveExternalCallback(_ url: URL) -> Bool {
        guard session != nil, continuation != nil else { return false }
        finish(.success(url)); return true
    }
    private func finish(_ result: Result<URL, Error>) {
        let continuation = self.continuation; self.continuation = nil
        let session = self.session; self.session = nil
        session?.cancel()
        continuation?.resume(with: result)
    }
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows).first(where: \.isKeyWindow) ?? ASPresentationAnchor()
    }
}
