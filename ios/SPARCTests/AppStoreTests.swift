import XCTest
import Combine
@testable import SPARC

private enum MockFailure: Error { case unavailable }

private actor MockControllerClient: ControllerClient {
    var snapshot: ChargeSnapshot
    var settings: ControllerSettings
    var failedOperations: Set<String>
    private(set) var teslaCommands: [String] = []
    private(set) var activationRequests: [ControllerConnectionMode] = []
    /// Tesla calls and activations in the order the store sent them.
    private(set) var teslaCalls: [String] = []
    private(set) var virtualStateEdits = 0
    private(set) var sleepWindowUpdates: [SleepWindowUpdate] = []
    func saveSleepWindows(_ value: SleepWindowUpdate) async throws {
        if failedOperations.contains("sleepWindows") { throw MockFailure.unavailable }
        sleepWindowUpdates.append(value)
    }
    var confirmsActivation = true
    func setFailedOperations(_ operations: Set<String>) { failedOperations = operations }
    func setConfirmsActivation(_ value: Bool) { confirmsActivation = value }
    func setSettings(_ value: ControllerSettings) { settings = value }

    init(snapshot: ChargeSnapshot? = nil, settings: ControllerSettings = ControllerSettings(weekly: .default, today: nil),
         failedOperations: Set<String> = []) {
        var legacy = ChargeSnapshot.demo()
        legacy.virtualCar = nil; legacy.controlOnly = nil
        self.snapshot = snapshot ?? legacy
        self.settings = settings
        self.failedOperations = failedOperations
    }
    func fetchSnapshot() async throws -> ChargeSnapshot {
        if failedOperations.contains("snapshot") { throw MockFailure.unavailable }
        return snapshot
    }
    func fetchSettings() async throws -> ControllerSettings {
        if failedOperations.contains("settings") { throw MockFailure.unavailable }
        return settings
    }
    func saveWeekly(_ value: WeeklySchedule) async throws {
        if failedOperations.contains("save") { throw MockFailure.unavailable }
        settings.weekly = value
    }
    func saveToday(_ value: TodayOverride?) async throws {
        if failedOperations.contains("save") { throw MockFailure.unavailable }
        settings.today = value
    }
    func setVirtualCarEnabled(_ enabled: Bool) async throws {
        if failedOperations.contains("virtual") { throw MockFailure.unavailable }
        settings.virtualCarEnabled = enabled
        snapshot = enabled ? .demo() : .noVehicle()
    }
    func command(_ command: String) async throws {
        if failedOperations.contains("command") { throw MockFailure.unavailable }
        snapshot.state = command == "pause" ? .paused : command == "end" ? .waiting : .charging
        snapshot.powerKW = snapshot.controlOnly == true || snapshot.state != .charging ? 0 : 11.5
    }
    func refreshTesla() async throws {
        teslaCalls.append("refresh")
        if failedOperations.contains("tesla_refresh") { throw MockFailure.unavailable }
    }
    func disconnectTesla(accountId: String?) async throws {
        let mode = settings.connections?.activeMode?.rawValue ?? "none"
        guard let accountId else {
            teslaCalls.append("disconnect:\(mode)")
            settings.connections?.tesla.authorized = false
            return
        }
        teslaCalls.append("disconnect:\(accountId):\(mode)")
        guard var tesla = settings.connections?.tesla else { return }
        tesla.accounts?.removeAll { $0.id == accountId }
        tesla.vehicles?.removeAll { $0.accountId == accountId }
        if tesla.accountId == accountId { tesla.accountId = nil; tesla.vehicleId = nil; tesla.vehicleName = nil }
        tesla.authorized = tesla.accounts?.isEmpty == false
        settings.connections?.tesla = tesla
    }
    func selectTeslaVehicle(_ vehicleId: String) async throws {
        teslaCalls.append("select:\(vehicleId)")
        if failedOperations.contains("tesla_select") { throw MockFailure.unavailable }
        guard var tesla = settings.connections?.tesla, let car = tesla.vehicles?.first(where: { $0.id == vehicleId }) else { return }
        tesla.vehicleId = car.id; tesla.vehicleName = car.displayName; tesla.accountId = car.accountId
        tesla.commandsReady = !unapprovedCars.contains(car.id)
        settings.connections?.tesla = tesla
    }
    var unapprovedCars: Set<String> = []
    func setUnapprovedCars(_ ids: Set<String>) { unapprovedCars = ids }
    func activateConnection(_ mode: ControllerConnectionMode) async throws {
        activationRequests.append(mode)
        teslaCalls.append("activate:\(mode.rawValue)")
        if confirmsActivation { settings.connections?.activeMode = mode }
    }
    func teslaCommand(_ command: TeslaChargeCommand, value: Int?) async throws {
        teslaCommands.append(command.rawValue)
        if failedOperations.contains("tesla_command") { throw MockFailure.unavailable }
    }
    func setVirtualCarState(batteryPercent: Double, pluggedIn: Bool) async throws { virtualStateEdits += 1 }
}

final class AppStoreTests: XCTestCase {
    private func isolatedDefaults() -> (UserDefaults, String) {
        let name = "SPARC.Tests.\(UUID().uuidString)"
        return (UserDefaults(suiteName: name)!, name)
    }

    /// Example data is test-only: a store runs the demo car only under the interface-test argument.
    @MainActor
    private func demoStore(_ defaults: UserDefaults, client: any ControllerClient = MockControllerClient(),
                           now: @escaping () -> Date = Date.init) -> AppStore {
        let store = AppStore(defaults: defaults, client: client, now: now, arguments: ["--uitesting"])
        XCTAssertTrue(store.isDemo)
        return store
    }

    /// An interface-test launch always starts from the default demo car and routine. Switching demo
    /// mode off and on again is what restores the choices saved while it ran.
    @MainActor
    private func reopenDemo(_ store: AppStore) {
        store.setDemoMode(false)
        XCTAssertFalse(store.isDemo)
        store.setDemoMode(true)
        XCTAssertTrue(store.isDemo)
    }

    private func appTestSnapshot() -> ChargeSnapshot {
        var snapshot = ChargeSnapshot.disconnected()
        snapshot.state = .waiting
        snapshot.vehicleName = "No vehicle connected"
        snapshot.controlOnly = true
        return snapshot
    }

    private func teslaSettings(active: ControllerConnectionMode = .virtual, ready: Bool = true) -> ControllerSettings {
        let connections = ControllerConnections(revision: 1, mode: .tesla, activeMode: active,
            virtual: .init(model: .cybertruck, batteryPercent: 20, pluggedIn: true),
            smartPlug: .init(address: nil, vehicleSource: .virtual, status: "not_configured"),
            tesla: .init(configured: true, authorized: true, status: "connected", vehicleName: "My Tesla", error: nil,
                vehicleId: "1", vehicles: [], keyPairingURL: nil, commandsReady: ready, chargeState: nil))
        return ControllerSettings(weekly: .default, today: nil, connections: connections)
    }

    @MainActor
    func testTeslaSetupDoesNotSwitchProvidersOrSendCommandsAndActivationRequiresConfirmation() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: teslaSettings())
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertEqual(store.activeConnectionMode, .virtual)
        XCTAssertFalse(store.canSendTeslaCommands)
        await store.sendTeslaCommand(.start)
        let initialCommands = await client.teslaCommands
        XCTAssertTrue(initialCommands.isEmpty)
        await client.setConfirmsActivation(false)
        await store.activateConnection(.tesla)
        XCTAssertEqual(store.activeConnectionMode, .virtual)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertTrue(store.connectionsIsStale)
        await store.refresh()
        await client.setConfirmsActivation(true)
        await store.activateConnection(.tesla)
        XCTAssertEqual(store.activeConnectionMode, .tesla)
        XCTAssertTrue(store.canSendTeslaCommands)
        XCTAssertNil(store.effectiveGoal, "Virtual schedule cannot be presented as a Tesla charging plan")
        let activatedCommands = await client.teslaCommands
        XCTAssertTrue(activatedCommands.isEmpty, "Activation itself cannot start or stop the real car")
    }

    @MainActor
    func testRemovingTeslaWhileItChargesMovesToTheVirtualCarFirst() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: teslaSettings(active: .tesla))
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertEqual(store.activeConnectionMode, .tesla)
        XCTAssertEqual(store.chargingCarName, "My Tesla")
        await store.disconnectTesla()
        XCTAssertNil(store.errorMessage)
        let calls = await client.teslaCalls
        XCTAssertEqual(calls, ["activate:virtual", "disconnect:virtual"], "Home must never point at a removed Tesla")
        XCTAssertEqual(store.activeConnectionMode, .virtual)
        XCTAssertEqual(store.chargingCarName, "Virtual Cybertruck")
        XCTAssertEqual(store.connections?.tesla.authorized, false)
    }

    @MainActor
    func testTeslaStaysConnectedWhenTheVirtualCarCannotTakeOver() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: teslaSettings(active: .tesla))
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await client.setConfirmsActivation(false)
        await store.disconnectTesla()
        let calls = await client.teslaCalls
        XCTAssertEqual(calls, ["activate:virtual"], "Tesla is not removed while the Pi still charges with it")
        XCTAssertNotNil(store.errorMessage)
    }

    @MainActor
    func testSwitchingTeslaAccountMovesChargingToTheVirtualCarBeforeSignIn() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: teslaSettings(active: .tesla))
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await store.switchTeslaAccount()
        let calls = await client.teslaCalls
        XCTAssertEqual(calls, ["activate:virtual"])
        XCTAssertEqual(store.activeConnectionMode, .virtual)
        XCTAssertNotNil(store.errorMessage, "This mock has no Tesla sign-in, so starting it reports an error")
    }

    @MainActor
    func testUsingTeslaReadsTheCarAgainFirstAndStaysOnVirtualWhenTeslaDoesNotAnswer() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: teslaSettings())
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await client.setFailedOperations(["tesla_refresh"])
        await store.useTeslaForCharging()
        var calls = await client.teslaCalls
        XCTAssertEqual(calls, ["refresh"])
        XCTAssertEqual(store.activeConnectionMode, .virtual)
        XCTAssertNotNil(store.errorMessage)
        await client.setFailedOperations([])
        await store.useTeslaForCharging()
        calls = await client.teslaCalls
        XCTAssertEqual(calls, ["refresh", "refresh", "activate:tesla"])
        XCTAssertEqual(store.activeConnectionMode, .tesla)
        let commands = await client.teslaCommands
        XCTAssertTrue(commands.isEmpty, "Switching cars never starts or stops charging")
    }

    private func severalTeslaAccounts(active: ControllerConnectionMode = .tesla) -> ControllerSettings {
        var settings = teslaSettings(active: active)
        settings.connections?.tesla.vehicles = [
            .init(id: "1", vin: "5YJ3E1EA7KF123456", displayName: "My Tesla", accountId: "a"),
            .init(id: "2", vin: "7SAYGDEE5PF654321", displayName: "Family Tesla", accountId: "b"),
            .init(id: "3", vin: "5YJYGDEE1LF000003", displayName: "Work Tesla", accountId: "c")]
        settings.connections?.tesla.accounts = [
            .init(id: "a", label: "Tesla account 1", authorized: true, vehicleCount: 1, error: nil),
            .init(id: "b", label: "Tesla account 2", authorized: true, vehicleCount: 1, error: nil),
            .init(id: "c", label: "Tesla account 3", authorized: false, vehicleCount: 1, error: "Sign in to this Tesla account again.")]
        settings.connections?.tesla.accountId = "a"
        return settings
    }

    @MainActor
    func testCalendarSyncAcceptsConfirmedVirtualCarWithoutRealVehicleTelemetry() async throws {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        var snapshot=ChargeSnapshot.demo();snapshot.virtualCar=true
        let client=MockControllerClient(snapshot:snapshot,settings:teslaSettings(active:.virtual))
        let store=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{"pi-a"})
        await store.refresh()
        XCTAssertFalse(store.hasLiveTelemetry);XCTAssertTrue(store.isVirtualCarActive)
        let now=Date()
        try await store.sendSleepWindows(SleepWindowUpdate(enabled:true,windows:[SleepWindow(id:"sleep",start:now,departure:now.addingTimeInterval(9*3600))],targetPercent:80))
        let updates=await client.sleepWindowUpdates
        XCTAssertEqual(updates.count,1)
    }

    @MainActor
    func testCalendarGoalTakesPrecedenceOverThePausedCampaign() async throws {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        let now=Date();let start=now.addingTimeInterval(3600);let wake=now.addingTimeInterval(10*3600)
        var settings=teslaSettings(active:.virtual)
        settings.sleepCalendar=SleepCalendarStatus(enabled:true,updatedAt:now,windowCount:14,nextStart:start,nextWake:wake,source:"Apple Calendar",automaticTesla:false)
        let client=MockControllerClient(settings:settings)
        let store=AppStore(defaults:defaults,client:client,now:{now},arguments:[])
        await store.refresh()
        XCTAssertEqual(store.effectiveGoal?.departure,wake)
        XCTAssertEqual(store.effectiveGoal?.readyBy,wake.addingTimeInterval(-1800))
        XCTAssertEqual(store.campaignWindow?.start,start)
    }

    @MainActor
    func testManualScheduleSelectionDisablesCalendarAndReadsTheConfirmedPiState() async throws {
        let (defaults, name) = isolatedDefaults(); defer { defaults.removePersistentDomain(forName: name) }
        var settings = teslaSettings(active: .virtual)
        settings.sleepCalendar = SleepCalendarStatus(enabled: true, updatedAt: Date(), windowCount: 0, nextStart: nil, nextWake: nil, source: "Apple Calendar", automaticTesla: false)
        var snapshot = ChargeSnapshot.demo(); snapshot.virtualCar = true
        let client = MockControllerClient(snapshot: snapshot, settings: settings)
        let store = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        await store.refresh()
        XCTAssertEqual(store.sleepCalendarStatus?.enabled, true)
        settings.sleepCalendar?.enabled = false
        await client.setSettings(settings)
        let switched = await store.sleepCalendar.disconnect(store: store)
        XCTAssertTrue(switched)
        XCTAssertEqual(store.sleepCalendarStatus?.enabled, false)
        XCTAssertEqual(store.snapshot.batteryPercent, snapshot.batteryPercent)
        let updates = await client.sleepWindowUpdates
        XCTAssertEqual(updates.count, 1)
        XCTAssertFalse(try XCTUnwrap(updates.first).enabled)
        XCTAssertTrue(try XCTUnwrap(updates.first).windows.isEmpty)
    }

    @MainActor
    func testRejectedManualScheduleSelectionKeepsCalendarActive() async {
        let (defaults, name) = isolatedDefaults(); defer { defaults.removePersistentDomain(forName: name) }
        var settings = teslaSettings(active: .virtual)
        settings.sleepCalendar = SleepCalendarStatus(enabled: true, updatedAt: Date(), windowCount: 0, nextStart: nil, nextWake: nil, source: "Apple Calendar", automaticTesla: false)
        var snapshot = ChargeSnapshot.demo(); snapshot.virtualCar = true
        let client = MockControllerClient(snapshot: snapshot, settings: settings, failedOperations: ["sleepWindows"])
        let store = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        await store.refresh()
        let switched = await store.sleepCalendar.disconnect(store: store)
        XCTAssertFalse(switched)
        XCTAssertEqual(store.sleepCalendarStatus?.enabled, true)
        XCTAssertFalse(store.sleepCalendar.working)
        let updates = await client.sleepWindowUpdates
        XCTAssertTrue(updates.isEmpty)
    }

    @MainActor
    func testAcknowledgedCalendarDisconnectWithFailedReadBackCannotSilentlyEnableItAgain() async {
        let (defaults, name) = isolatedDefaults(); defer { defaults.removePersistentDomain(forName: name) }
        var settings = teslaSettings(active: .virtual)
        settings.sleepCalendar = SleepCalendarStatus(enabled: true, updatedAt: Date(), windowCount: 0, nextStart: nil, nextWake: nil, source: "Apple Calendar", automaticTesla: false)
        var snapshot = ChargeSnapshot.demo(); snapshot.virtualCar = true
        let client = MockControllerClient(snapshot: snapshot, settings: settings)
        let store = AppStore(defaults: defaults, client: client, arguments: [], controllerID: { "pi-a" })
        await store.refresh()
        await client.setFailedOperations(["settings"])
        let switched = await store.sleepCalendar.disconnect(store: store)
        XCTAssertFalse(switched)
        XCTAssertTrue(store.sleepCalendar.message.contains("Calendar is off on the Pi"))
        XCTAssertEqual(store.sleepCalendarStatus?.enabled, true, "Do not fabricate canonical state when read-back failed")
        await store.sleepCalendar.refresh(store: store, force: true)
        let updates = await client.sleepWindowUpdates
        XCTAssertEqual(updates.count, 1)
        XCTAssertFalse(updates[0].enabled)
    }

    @MainActor
    func testProfileIdentityFollowsControllerAndSelectedTeslaAndDoesNotSendChargingCommands() async {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        var owner="pi-a"
        let client=MockControllerClient(settings:severalTeslaAccounts(active:.virtual))
        let store=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{owner})
        await store.refresh();XCTAssertEqual(store.activeCarProfileID,"pi-a:virtual")
        await store.useTeslaCar("2");XCTAssertEqual(store.activeCarProfileID,"pi-a:tesla:2")
        await store.useTeslaCar("1");XCTAssertEqual(store.activeCarProfileID,"pi-a:tesla:1")
        owner="pi-b";XCTAssertEqual(store.activeCarProfileID,"pi-b:tesla:1")
        let commands=await client.teslaCommands;XCTAssertTrue(commands.isEmpty)
    }

    @MainActor
    func testQuickProfileChoiceIsImmediateAndDoesNotActivateOrCommandAnyCar() async {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        let client=MockControllerClient(settings:severalTeslaAccounts(active:.virtual))
        let store=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{"pi"})
        await store.refresh()
        store.chooseCarProfile("tesla:2")
        XCTAssertEqual(store.activeCarProfileID,"pi:tesla:2")
        XCTAssertEqual(store.chargingCarName,"Family Tesla")
        XCTAssertEqual(store.activeConnectionMode,.virtual)
        XCTAssertNil(store.profileTeslaReading);XCTAssertNil(store.selectedTeslaHistory)
        XCTAssertFalse(store.canSendTeslaCommands)
        let calls=await client.teslaCalls;XCTAssertTrue(calls.isEmpty)
    }

    @MainActor
    func testReadOnlyTeslaProfileWorksWithoutKeyApprovalAndKeepsCommandsDisabled() async {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        let client=MockControllerClient(settings:severalTeslaAccounts(active:.virtual))
        await client.setUnapprovedCars(["2"])
        let store=AppStore(defaults:defaults,client:client,arguments:[])
        await store.refresh();store.chooseCarProfile("tesla:2");await store.prepareCarProfile()
        XCTAssertEqual(store.profileConnectionMode,.tesla)
        XCTAssertEqual(store.connections?.tesla.vehicleId,"2")
        XCTAssertEqual(store.activeConnectionMode,.virtual)
        XCTAssertFalse(store.canSendTeslaCommands);XCTAssertNil(store.errorMessage)
        let commands=await client.teslaCommands;XCTAssertTrue(commands.isEmpty)
        let activations=await client.activationRequests;XCTAssertTrue(activations.isEmpty)
    }

    @MainActor
    func testRapidProfileChoicesPrepareOnlyTheLastCarAndPersistOnRelaunch() async {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        let client=MockControllerClient(settings:severalTeslaAccounts(active:.virtual))
        let store=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{"pi"})
        await store.refresh()
        store.chooseCarProfile("tesla:2");store.chooseCarProfile("virtual");store.chooseCarProfile("tesla:1")
        XCTAssertEqual(store.profileTeslaID,"1")
        await store.prepareCarProfile()
        let calls=await client.teslaCalls;XCTAssertEqual(calls,["refresh"])
        let relaunched=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{"pi"})
        await relaunched.refresh();XCTAssertEqual(relaunched.profileTeslaID,"1")
    }

    @MainActor
    func testRemovedCarAndAnotherPhonesVehicleChangeNeverReuseTheWrongData() async {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        var settings=severalTeslaAccounts(active:.virtual)
        let client=MockControllerClient(settings:settings)
        let store=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{"pi"})
        await store.refresh();store.chooseCarProfile("tesla:2")
        XCTAssertNil(store.profileTeslaReading);XCTAssertFalse(store.showsTelemetry)
        settings.connections?.tesla.vehicles?.removeAll { $0.id == "2" }
        await client.setSettings(settings);await store.refresh()
        XCTAssertEqual(store.profileConnectionMode,.virtual)
        XCTAssertNil(store.profileTeslaID);XCTAssertNil(store.selectedTeslaHistory)
        store.chooseCarProfile("tesla:does-not-exist")
        XCTAssertEqual(store.profileConnectionMode,.virtual)
    }

    @MainActor
    func testOfflineReadOnlyProfileKeepsIdentityAndReportsUnavailableWithoutSendingCommands() async {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        let client=MockControllerClient(settings:severalTeslaAccounts(active:.virtual))
        let store=AppStore(defaults:defaults,client:client,arguments:[],controllerID:{"pi"})
        await store.refresh();store.chooseCarProfile("tesla:2")
        await client.setFailedOperations(["tesla_refresh"]);await store.prepareCarProfile()
        XCTAssertEqual(store.profileTeslaID,"2")
        XCTAssertNotNil(store.errorMessage);XCTAssertFalse(store.canSendTeslaCommands)
        let commands=await client.teslaCommands;XCTAssertTrue(commands.isEmpty)
    }

    @MainActor
    func testResultsFollowTheViewedTeslaWhileThePiChargingProviderRemainsVirtual() async {
        let (defaults,name)=isolatedDefaults();defer{defaults.removePersistentDomain(forName:name)}
        let client=MockControllerClient(settings:severalTeslaAccounts(active:.virtual))
        let store=AppStore(defaults:defaults,client:client,arguments:[])
        await store.refresh();store.chooseCarProfile("tesla:2")
        XCTAssertEqual(store.activeConnectionMode,.virtual)
        XCTAssertEqual(store.defaultHistoryCar,.tesla)
        XCTAssertNil(store.selectedTeslaHistory)
        store.chooseCarProfile("virtual")
        XCTAssertEqual(store.defaultHistoryCar,.virtual)
    }

    @MainActor
    func testAddingATeslaAccountKeepsChargingWithTheCarInUse() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: severalTeslaAccounts())
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertTrue(store.teslaHoldsSeveralAccounts)
        XCTAssertEqual(store.teslaAccounts.map(\.id), ["a", "b", "c"])
        await store.addTeslaAccount()
        let calls = await client.teslaCalls
        XCTAssertEqual(calls, [], "Adding an account must not move charging off the Tesla")
        XCTAssertEqual(store.activeConnectionMode, .tesla)
        await store.switchTeslaAccount()
        let afterSwitch = await client.teslaCalls
        XCTAssertEqual(afterSwitch, [], "On a Pi with several accounts, switching account adds one")
    }

    @MainActor
    func testRemovingAnotherTeslaAccountKeepsTheCarInUseAndRemovingItsOwnAccountMovesToVirtualFirst() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: severalTeslaAccounts())
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await store.removeTeslaAccount("b")
        var calls = await client.teslaCalls
        XCTAssertEqual(calls, ["disconnect:b:tesla"])
        XCTAssertEqual(store.activeConnectionMode, .tesla)
        XCTAssertEqual(store.connections?.tesla.vehicles?.map(\.id), ["1", "3"])
        await store.removeTeslaAccount("missing")
        calls = await client.teslaCalls
        XCTAssertEqual(calls, ["disconnect:b:tesla"], "An account the Pi doesn't list is never sent")
        await store.removeTeslaAccount("a")
        calls = await client.teslaCalls
        XCTAssertEqual(calls, ["disconnect:b:tesla", "activate:virtual", "disconnect:a:virtual"])
        XCTAssertEqual(store.activeConnectionMode, .virtual)
        XCTAssertNil(store.connections?.tesla.vehicleId)
        XCTAssertEqual(store.teslaAccounts.map(\.id), ["c"])
    }

    @MainActor
    func testChoosingAnotherTeslaGoesThroughTheVirtualCarAndNeverSendsAChargeCommand() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: severalTeslaAccounts())
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await store.useTeslaCar("1")
        var calls = await client.teslaCalls
        XCTAssertEqual(calls, [], "The car already in use is left alone")
        await store.useTeslaCar("2")
        calls = await client.teslaCalls
        XCTAssertEqual(calls, ["activate:virtual", "select:2", "refresh", "activate:tesla"])
        XCTAssertEqual(store.activeConnectionMode, .tesla)
        XCTAssertEqual(store.connections?.tesla.vehicleId, "2")
        XCTAssertEqual(store.connections?.tesla.accountId, "b")
        XCTAssertEqual(store.chargingCarName, "Family Tesla")
        await store.useTeslaCar("not-listed")
        let after = await client.teslaCalls
        XCTAssertEqual(after, calls, "Only a car the Pi lists can be chosen")
        let commands = await client.teslaCommands
        XCTAssertTrue(commands.isEmpty)
    }

    @MainActor
    func testATeslaThatHasNotApprovedSPARCLeavesTheVirtualCarCharging() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: severalTeslaAccounts())
        await client.setUnapprovedCars(["2"])
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await store.useTeslaCar("2")
        let calls = await client.teslaCalls
        XCTAssertEqual(calls, ["activate:virtual", "select:2", "refresh"])
        XCTAssertEqual(store.activeConnectionMode, .virtual)
        XCTAssertEqual(store.connections?.tesla.vehicleId, "2")
        XCTAssertEqual(store.errorMessage.map(TeslaMessage.friendly), "Approve SPARC in the Tesla app, then try again.")
    }

    @MainActor
    func testAPiWithOneTeslaAccountKeepsTheOldRemoveAndSwitchBehavior() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: teslaSettings(active: .tesla))
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertFalse(store.teslaHoldsSeveralAccounts)
        await store.removeTeslaAccount("anything")
        let calls = await client.teslaCalls
        XCTAssertEqual(calls, ["activate:virtual", "disconnect:virtual"], "An older Pi gets the empty body it accepts")
        XCTAssertEqual(TeslaAccountBody.disconnect(nil), [:])
        XCTAssertEqual(TeslaAccountBody.disconnect("a"), ["accountId": .string("a")])
    }

    func testTeslaStateFromPisWithAndWithoutAccountsDecodes() throws {
        let older = #"{"configured":true,"authorized":true,"status":"connected","vehicleId":"1","vehicles":[{"id":"1","vin":"5YJ3E1EA7KF123456","displayName":"My Tesla"}]}"#
        let newer = #"{"configured":true,"authorized":true,"status":"connected","vehicleId":"2","accountId":"b","vehicles":[{"id":"1","vin":"5YJ3E1EA7KF123456","displayName":"My Tesla","accountId":"a"},{"id":"2","vin":"7SAYGDEE5PF654321","displayName":"Family Tesla","accountId":"b"}],"accounts":[{"id":"a","label":"Tesla account 1","authorized":false,"vehicleCount":1,"error":"Sign in to this Tesla account again."},{"id":"b","label":"Tesla account 2","authorized":true,"vehicleCount":1}]}"#
        let old = try JSONDecoder().decode(ControllerConnections.Tesla.self, from: Data(older.utf8))
        XCTAssertNil(old.accounts)
        XCTAssertNil(old.vehicles?.first?.accountId)
        let new = try JSONDecoder().decode(ControllerConnections.Tesla.self, from: Data(newer.utf8))
        XCTAssertEqual(new.accounts?.map(\.authorized), [false, true])
        XCTAssertEqual(new.accounts?.first?.error, "Sign in to this Tesla account again.")
        XCTAssertEqual(new.vehicles?.map(\.accountId), ["a", "b"])
        XCTAssertEqual(new.accountId, "b")
    }

    func testTeslaErrorsAreWordedForOwners() {
        XCTAssertEqual(TeslaMessage.friendly("Tesla data is stale. Wake the car in the Tesla app, then refresh before activating it."),
                       "Your Tesla didn't answer. If it's asleep, open the Tesla app to wake it, then try again.")
        XCTAssertEqual(TeslaMessage.friendly("Tesla did not accept the request; it was not retried."),
                       "Your Tesla didn't answer. If it's asleep, open the Tesla app to wake it, then try again.")
        XCTAssertEqual(TeslaMessage.friendly("Approve SPARC in the Tesla app before charging with your Tesla."),
                       "Approve SPARC in the Tesla app, then try again.")
        XCTAssertEqual(TeslaMessage.friendly("Something unrelated."), "Something unrelated.")
    }

    @MainActor
    func testTeslaCommandsDoNotInventChargingTelemetryAndVirtualEditsAreBlocked() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient(settings: teslaSettings(active: .tesla))
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        let observed = store.snapshot
        await store.chargeNow()
        XCTAssertEqual(store.snapshot, observed, "A command receipt is not a charging measurement")
        XCTAssertNotNil(store.teslaCommandMessage)
        await store.pauseCharging()
        await store.setVirtualCarState(batteryPercent: 20, pluggedIn: true)
        let commands = await client.teslaCommands
        let virtualEdits = await client.virtualStateEdits
        XCTAssertEqual(commands, ["charge_start", "charge_stop"])
        XCTAssertEqual(virtualEdits, 0)
        await client.setFailedOperations(["tesla_command"])
        await store.sendTeslaCommand(.start)
        XCTAssertNil(store.teslaCommandMessage)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertEqual(store.snapshot, observed)
    }

    @MainActor
    func testControllerTestCommandsUseConfirmedStatesWithoutInventingTelemetry() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults, client: MockControllerClient(snapshot: appTestSnapshot()), arguments: [])
        await store.refresh()
        XCTAssertTrue(store.isControllerTest)
        XCTAssertFalse(store.isDemo)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertFalse(store.showsTelemetry)
        XCTAssertTrue(store.canControlCharging)
        await store.chargeNow()
        XCTAssertEqual(store.snapshot.state, .charging)
        XCTAssertEqual(store.snapshot.powerKW, 0)
        await store.pauseCharging()
        XCTAssertEqual(store.snapshot.state, .paused)
        await store.resumeCharging()
        XCTAssertEqual(store.snapshot.state, .charging)
        await store.endCharging()
        XCTAssertEqual(store.snapshot.state, .waiting)
        XCTAssertTrue(store.records.isEmpty)
    }

    @MainActor
    func testControllerTestDisconnectClearsActiveStateAndRecoversWithoutDemoData() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let client = MockControllerClient(snapshot: appTestSnapshot())
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        await store.chargeNow()
        await client.setFailedOperations(["snapshot"])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertFalse(store.isControllerTest)
        XCTAssertFalse(store.canControlCharging)
        XCTAssertFalse(store.showsTelemetry)
        XCTAssertTrue(store.records.isEmpty)
        await client.setFailedOperations([])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .charging)
        XCTAssertTrue(store.isControllerTest)
        XCTAssertFalse(store.showsTelemetry)
    }

    @MainActor
    func testControllerTestScheduleSaveUsesControllerAndUpdatesItsGoal() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let client = MockControllerClient(snapshot: appTestSnapshot())
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        var weekly = WeeklySchedule.default
        weekly.mode = .fixed
        weekly.targetPercent = 90
        await store.saveWeekly(weekly)
        let saved = try? await client.fetchSettings()
        XCTAssertEqual(saved?.weekly, weekly)
        XCTAssertEqual(store.weekly, weekly)
        XCTAssertTrue(store.isControllerTest)
        XCTAssertFalse(store.showsTelemetry)
        let confirmed = store.snapshot
        store.setDemoMode(true)
        XCTAssertFalse(store.isDemo, "Normal live mode cannot enable example data")
        XCTAssertTrue(store.isControllerTest)
        XCTAssertFalse(store.isVirtualCarActive)
        XCTAssertEqual(store.snapshot, confirmed)
    }

    @MainActor
    func testVirtualCarDefaultsOnAtFortyAndOptOutReturnsWithDemoMode() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let store = demoStore(defaults)
        XCTAssertTrue(store.virtualCarEnabled)
        XCTAssertTrue(store.isVirtualCarActive)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertEqual(store.snapshot.batteryPercent, 40)
        XCTAssertEqual(store.snapshot.state, .waiting)
        await store.setVirtualCarEnabled(false)
        XCTAssertFalse(store.isVirtualCarActive)
        XCTAssertFalse(store.canControlCharging)
        XCTAssertEqual(store.snapshot.state, .unplugged)
        let relaunched = demoStore(defaults)
        XCTAssertTrue(relaunched.virtualCarEnabled, "Every interface-test launch starts from the default demo car")
        reopenDemo(relaunched)
        XCTAssertFalse(relaunched.virtualCarEnabled)
        XCTAssertFalse(relaunched.showsTelemetry)
        await relaunched.setVirtualCarEnabled(true)
        XCTAssertEqual(relaunched.snapshot.batteryPercent, 40)
        XCTAssertEqual(relaunched.snapshot.energyKWh, 0)
    }

    @MainActor
    func testVirtualChargeAccruesElapsedEnergyAndPauseResumeEnd() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        var clock = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        let store = demoStore(defaults, now: { clock })
        var weekly = WeeklySchedule.default
        weekly.enabled = false
        await store.saveWeekly(weekly)
        await store.chargeNow()
        clock.addTimeInterval(3600)
        await store.refresh()
        XCTAssertEqual(store.snapshot.batteryPercent, 50)
        XCTAssertEqual(store.snapshot.energyKWh, 11.5, accuracy: 0.000001)
        XCTAssertEqual(store.snapshot.estimatedCost, 2.415, accuracy: 0.000001)
        await store.pauseCharging()
        clock.addTimeInterval(3600)
        await store.refresh()
        XCTAssertEqual(store.snapshot.energyKWh, 11.5, accuracy: 0.000001)
        XCTAssertEqual(store.snapshot.state, .paused)
        await store.resumeCharging()
        clock.addTimeInterval(3600)
        await store.refresh()
        XCTAssertEqual(store.snapshot.batteryPercent, 60)
        await store.endCharging()
        clock.addTimeInterval(3600)
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .waiting)
        XCTAssertEqual(store.snapshot.energyKWh, 23, accuracy: 0.000001)
        await store.resumeCharging()
        clock.addTimeInterval(24 * 3600)
        await store.refresh()
        XCTAssertEqual(store.snapshot.batteryPercent, 80)
        XCTAssertEqual(store.snapshot.state, .complete)
        XCTAssertEqual(store.snapshot.energyKWh, 40 / 0.9, accuracy: 0.000001)
        let energy = store.snapshot.energyKWh
        await store.setVirtualCarEnabled(true)
        XCTAssertEqual(store.snapshot.energyKWh, energy, "An unchanged toggle must not reset the car")
    }

    @MainActor
    func testFixedVirtualWindowStartsAtChosenTimeAndStopsAtReadyDeadline() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        var clock = ISO8601DateFormatter().date(from: "2026-10-27T10:59:00Z")! // 05:59 Chicago.
        let store = demoStore(defaults, now: { clock })
        var weekly = WeeklySchedule.default
        weekly.mode = .fixed; weekly.fixedStartMinutes = 360
        await store.saveWeekly(weekly)
        XCTAssertEqual(store.snapshot.windowStart, ISO8601DateFormatter().date(from: "2026-10-27T11:00:00Z"))
        XCTAssertEqual(store.snapshot.windowEnd, ISO8601DateFormatter().date(from: "2026-10-27T11:30:00Z"))
        clock.addTimeInterval(31 * 60)
        await store.refresh()
        XCTAssertEqual(store.snapshot.batteryPercent, 45)
        XCTAssertEqual(store.snapshot.energyKWh, 5.75, accuracy: 0.000001)
        XCTAssertEqual(store.snapshot.state, .waiting)
        clock.addTimeInterval(20 * 60)
        await store.chargeNow() // Manual charging remains possible in the final buffer.
        clock.addTimeInterval(20 * 60)
        await store.refresh()
        XCTAssertEqual(store.snapshot.energyKWh, 5.75 + 11.5 / 6, accuracy: 0.000001)
        XCTAssertEqual(store.snapshot.state, .waiting)
    }

    @MainActor
    func testControllerVirtualSourceAndFailedToggleNeverClaimRealTelemetry() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let client = MockControllerClient(snapshot: .demo(),
            settings: ControllerSettings(weekly: .default, today: nil, virtualCarEnabled: true))
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertTrue(store.isVirtualCarActive)
        XCTAssertTrue(store.isControllerTest)
        XCTAssertFalse(store.hasLiveTelemetry)
        await client.setFailedOperations(["virtual"])
        await store.setVirtualCarEnabled(false)
        XCTAssertTrue(store.virtualCarEnabled)
        XCTAssertTrue(store.isVirtualCarActive)
        XCTAssertNotNil(store.errorMessage)
        await client.setFailedOperations(["snapshot"])
        await store.setVirtualCarEnabled(false)
        XCTAssertTrue(store.virtualCarEnabled, "An unconfirmed acknowledgement must not change the switch")
        XCTAssertFalse(store.isVirtualCarActive)
        XCTAssertEqual(store.snapshot.state, .offline)
        await client.setFailedOperations([])
        await store.refresh()
        XCTAssertFalse(store.virtualCarEnabled)
        XCTAssertFalse(store.showsTelemetry)
        store.setDemoMode(true)
        XCTAssertFalse(store.isDemo, "Normal live mode cannot enable example data")
        XCTAssertFalse(store.virtualCarEnabled, "A local mode request cannot override the confirmed Pi setting")
    }

    @MainActor
    func testRepeatedUnchangedDemoRefreshDoesNotPublishTelemetryUpdates() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let now = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        let store = demoStore(defaults, now: { now })
        let initialSnapshot = store.snapshot
        var publicationCount = 0
        let observation = store.objectWillChange.sink { publicationCount += 1 }
        defer { observation.cancel() }
        for _ in 0..<3 { await store.refresh() }
        XCTAssertEqual(publicationCount, 0, "An unchanged polling refresh must not invalidate the interface")
        XCTAssertEqual(store.snapshot, initialSnapshot)
    }

    @MainActor
    func testFixedLateEveningStartBelongsToNightBeforeMorningDeparture() async throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let now = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        let store = demoStore(defaults, now: { now })
        var weekly = WeeklySchedule.default
        weekly.mode = .fixed
        weekly.fixedStartMinutes = 23 * 60
        await store.saveWeekly(weekly)
        let goal = try XCTUnwrap(store.effectiveGoal)
        XCTAssertEqual(goal.departure, ISO8601DateFormatter().date(from: "2026-10-27T12:00:00Z"))
        XCTAssertEqual(store.snapshot.windowStart, ISO8601DateFormatter().date(from: "2026-10-27T04:00:00Z"))
        XCTAssertLessThanOrEqual(store.snapshot.windowEnd, goal.readyBy)
    }

    @MainActor
    func testEarlyOneTimeDepartureKeepsDemoWindowBeforeReadyBuffer() async throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let now = ISO8601DateFormatter().date(from: "2026-10-27T00:00:00Z")!
        let store = demoStore(defaults, now: { now })
        let departure = ISO8601DateFormatter().date(from: "2026-10-27T06:00:00Z")!
        await store.saveToday(target: 80, departure: departure)
        let goal = try XCTUnwrap(store.effectiveGoal)
        XCTAssertGreaterThanOrEqual(store.snapshot.windowStart, now)
        XCTAssertGreaterThan(store.snapshot.windowEnd, store.snapshot.windowStart)
        XCTAssertLessThanOrEqual(store.snapshot.windowEnd, goal.readyBy)
    }

    @MainActor
    func testDisabledDemoRoutineRemovesScheduledDeadlineAndWindow() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let store = demoStore(defaults)
        var weekly = WeeklySchedule.default
        weekly.enabled = false
        await store.saveWeekly(weekly)
        XCTAssertNil(store.effectiveGoal)
        XCTAssertEqual(store.displayedReadyTime, "Not scheduled")
        XCTAssertEqual(store.displayedWindow, "No charging window scheduled")
        await store.chargeNow()
        XCTAssertEqual(store.snapshot.state, .charging)
    }

    @MainActor
    func testUnconfirmedAcceptedCommandClearsLiveTelemetryAndCanRecover() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let client = MockControllerClient()
        let store = AppStore(defaults: defaults, client: client, arguments: [])
        await store.refresh()
        XCTAssertTrue(store.hasLiveTelemetry)
        await client.setFailedOperations(["snapshot"])
        await store.chargeNow()
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertEqual(store.snapshot.powerKW, 0)
        XCTAssertNotNil(store.connectionMessage)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.busy)
        await client.setFailedOperations([])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .charging)
        XCTAssertTrue(store.hasLiveTelemetry)
        XCTAssertNil(store.connectionMessage)
    }

    @MainActor
    func testFreshDemoLaunchAlwaysWaitsAndKeepsSavedRoutine() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let client = MockControllerClient()
        let first = demoStore(defaults, client: client)
        var changed = WeeklySchedule.default
        changed.targetPercent = 90
        await first.saveWeekly(changed)
        await first.chargeNow()
        XCTAssertEqual(first.snapshot.state, .charging)
        let relaunched = demoStore(defaults, client: client)
        XCTAssertEqual(relaunched.snapshot.state, .waiting)
        XCTAssertEqual(relaunched.snapshot.powerKW, 0)
        XCTAssertEqual(relaunched.weekly, .default, "Every interface-test launch starts from the default routine")
        reopenDemo(relaunched)
        XCTAssertEqual(relaunched.snapshot.state, .waiting)
        XCTAssertEqual(relaunched.snapshot.powerKW, 0)
        XCTAssertEqual(relaunched.weekly.targetPercent, 90)
    }

    @MainActor
    func testTodayOverrideExpiresWithoutRewritingSavedWeeklySchedule() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        var clock = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        let store = demoStore(defaults, now: { clock })
        var weekly = WeeklySchedule.default
        weekly.targetPercent = 85
        await store.saveWeekly(weekly)
        let departure = clock.addingTimeInterval(3600)
        await store.saveToday(target: 95, departure: departure)
        XCTAssertEqual(store.snapshot.targetPercent, 95)
        XCTAssertEqual(store.weekly, weekly)
        clock = departure
        await store.refresh()
        XCTAssertNil(store.today)
        XCTAssertEqual(store.snapshot.targetPercent, 85)
        XCTAssertEqual(store.weekly, weekly)
        let relaunched = demoStore(defaults, now: { clock })
        reopenDemo(relaunched)
        XCTAssertNil(relaunched.today)
        XCTAssertEqual(relaunched.weekly, weekly)
    }

    @MainActor
    func testLiveModeStartsWithNoDemoMetrics() {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        XCTAssertFalse(store.isDemo)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertEqual(store.snapshot.batteryPercent, 0)
        XCTAssertEqual(store.snapshot.monthlySavings, 0)
        XCTAssertEqual(store.snapshot.currentRateCents, 0)
        XCTAssertTrue(store.records.isEmpty)
    }

    @MainActor
    func testFailedLiveCommandDoesNotOptimisticallyChangeChargingState() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults,
            client: MockControllerClient(failedOperations: ["command"]), arguments: [])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .waiting)
        await store.chargeNow()
        XCTAssertEqual(store.snapshot.state, .waiting)
        XCTAssertEqual(store.snapshot.powerKW, 0)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.busy)
    }

    @MainActor
    func testFailedLiveSettingsSaveLeavesOldRoutine() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults,
            client: MockControllerClient(failedOperations: ["save"]), arguments: [])
        var changed = WeeklySchedule.default
        changed.targetPercent = 95
        await store.saveWeekly(changed)
        XCTAssertEqual(store.weekly, .default)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.busy)
    }

    @MainActor
    func testFailedLiveRefreshDoesNotShowSampleDataAsCurrent() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults,
            client: MockControllerClient(failedOperations: ["settings"]), arguments: [])
        await store.refresh()
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertTrue(store.records.isEmpty)
        XCTAssertEqual(store.weekly, .default)
        XCTAssertEqual(store.snapshot.monthlySavings, 0)
        XCTAssertNotNil(store.connectionMessage)
        XCTAssertNil(store.errorMessage)
    }

    @MainActor
    func testControllerSettingsLoadEvenWhenVehicleTelemetryIsUnavailable() async {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let now = ISO8601DateFormatter().date(from: "2026-10-26T23:00:00Z")!
        var weekly = WeeklySchedule.default
        weekly.targetPercent = 85
        let today = TodayOverride(targetPercent: 95, departure: now.addingTimeInterval(3600))
        let client = MockControllerClient(settings: ControllerSettings(weekly: weekly, today: today),
                                          failedOperations: ["snapshot"])
        let store = AppStore(defaults: defaults, client: client, now: { now }, arguments: [])
        await store.refresh()
        XCTAssertEqual(store.weekly, weekly)
        XCTAssertEqual(store.today, today)
        XCTAssertEqual(store.displayedTarget, "95%")
        XCTAssertEqual(store.snapshot.state, .offline)
        XCTAssertFalse(store.hasLiveTelemetry)
        XCTAssertTrue(store.records.isEmpty)
        XCTAssertNotNil(store.connectionMessage)
    }

    @MainActor
    func testMalformedStoredSettingsFallBackToDefaults() throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        var bad = WeeklySchedule.default
        bad.targetPercent = 150
        defaults.set(try JSONEncoder().encode(ControllerSettings(weekly: bad, today: nil)), forKey: "sparc.demo.settings.v1")
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        XCTAssertEqual(store.weekly, .default)
        XCTAssertEqual(store.snapshot.targetPercent, 80)
    }

    @MainActor
    func testMalformedStoredOverrideIsDiscarded() throws {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let invalid = TodayOverride(targetPercent: 101, departure: .now.addingTimeInterval(3600))
        defaults.set(try JSONEncoder().encode(ControllerSettings(weekly: .default, today: invalid)), forKey: "sparc.demo.settings.v1")
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        XCTAssertNil(store.today)
        XCTAssertEqual(store.snapshot.targetPercent, 80)
    }

    @MainActor
    func testSampleRecordsGiveASixNightStreakAndFourOfSixMilestones() {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        let store = demoStore(defaults)
        XCTAssertEqual(store.smartNightStreak, 6)
        let nights = store.recentNights(7)
        XCTAssertEqual(nights.map(\.charged), [true, true, true, true, true, true, false])
        XCTAssertEqual(nights.last?.isTonight, true)
        let milestones = store.milestones
        XCTAssertEqual(milestones.filter(\.unlocked).map(\.id), ["first", "run5", "save10", "kwh100"])
        XCTAssertEqual(milestones.first { $0.id == "save25" }?.detail, "$8.92 to go")
        XCTAssertEqual(milestones.first { $0.id == "run10" }?.detail, "4 to go")
        XCTAssertEqual(store.displayVehicleName, "Cybertruck")
    }

    @MainActor
    func testNoRecordsMeansNoStreakAndNothingUnlocked() {
        let (defaults, name) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(false, forKey: "sparc.demoMode")
        let store = AppStore(defaults: defaults, client: MockControllerClient(), arguments: [])
        XCTAssertEqual(store.smartNightStreak, 0)
        XCTAssertTrue(store.milestones.allSatisfy { !$0.unlocked })
        XCTAssertEqual(store.milestones.first { $0.id == "save10" }?.detail, "$10.00 to go")
    }

    func testAgoNeverTicksBySeconds() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        XCTAssertEqual(SPARCFormat.ago(now.addingTimeInterval(-45), now: now), "Just now")
        XCTAssertEqual(SPARCFormat.ago(now.addingTimeInterval(-125), now: now), "2 min ago")
        XCTAssertEqual(SPARCFormat.ago(now.addingTimeInterval(-7300), now: now), "2 h ago")
    }
}
