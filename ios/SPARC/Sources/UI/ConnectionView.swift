import SwiftUI

/// Tesla for an owner: connect one or more Tesla accounts, see every car, approve SPARC once in the Tesla app,
/// and remove an account. Choosing which car SPARC charges is the "Charging with" switcher, which always asks first.
struct TeslaConnectionSetupView: View {
    private enum CheckState { case idle, checking, done, failed }

    @EnvironmentObject private var store: AppStore
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @State private var confirmRemove = false
    @State private var confirmSwitchAccount = false
    @State private var removingAccount: ControllerConnections.Tesla.Account?
    @State private var switchingTo: ControllerConnections.Tesla.Vehicle?
    @State private var checkState = CheckState.idle
    @State private var awaitingApproval = false

    private var tesla: ControllerConnections.Tesla? { store.connections?.tesla }
    private var connected: Bool { tesla?.authorized == true }
    private var cars: [ControllerConnections.Tesla.Vehicle] { tesla?.vehicles ?? [] }
    private var selectedCar: ControllerConnections.Tesla.Vehicle? { cars.first { $0.id == tesla?.vehicleId } }
    private var teslaActive: Bool { store.activeConnectionMode == .tesla }
    private var unavailable: Bool { store.busy || store.connectionsIsStale || store.isDemo || checkState == .checking }

    var body: some View {
        ScrollView { pageContent }
        .background(SPARCTheme.background).scrollIndicators(.hidden)
        .navigationTitle("Tesla").navigationBarTitleDisplayMode(.inline)
        .task {
            await store.refresh()
            if connected { await check() }
        }
        .onChange(of: scenePhase) { _, phase in
            // Back from approving SPARC in the Tesla app: look again without another tap.
            guard phase == .active, awaitingApproval else { return }
            awaitingApproval = false
            Task { await check() }
        }
        .confirmationDialog("Switch Tesla account?", isPresented: $confirmSwitchAccount, titleVisibility: .visible) {
            Button("Sign in to Tesla") { Task { await switchAccount() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(teslaActive ? "Sign in with the Tesla account that has your car. SPARC charges the virtual car until you pick a car from that account."
                 : "Sign in with the Tesla account that has your car.")
        }
        .confirmationDialog("Remove Tesla?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove Tesla", role: .destructive) { Task { await store.disconnectTesla(); checkState = .idle } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(teslaActive ? "SPARC switches to the virtual car and forgets your Tesla sign-in. Nothing changes on your car."
                 : "SPARC forgets your Tesla sign-in. Nothing changes on your car.")
        }
        .confirmationDialog(removingAccount.map { "Remove \($0.label)?" } ?? "Remove Tesla account?",
                            isPresented: Binding(get: { removingAccount != nil }, set: { if !$0 { removingAccount = nil } }),
                            titleVisibility: .visible, presenting: removingAccount) { account in
            Button("Remove account", role: .destructive) {
                Task { await store.removeTeslaAccount(account.id); checkState = .idle; if connected { await check() } }
            }
            Button("Cancel", role: .cancel) {}
        } message: { account in
            Text(teslaActive && (tesla?.accountId == account.id || store.teslaAccounts.count == 1)
                 ? "SPARC switches to the virtual car and forgets this sign-in."
                 : "SPARC forgets this sign-in and its cars.")
        }
        .confirmationDialog(switchingTo.map { "Charge with \(name($0))?" } ?? "Charge with this Tesla?",
                            isPresented: Binding(get: { switchingTo != nil }, set: { if !$0 { switchingTo = nil } }),
                            titleVisibility: .visible, presenting: switchingTo) { car in
            Button("Use \(name(car))") { Task { await store.useTeslaCar(car.id); checkState = store.errorMessage == nil ? .done : .failed } }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("Nothing starts or stops charging.")
        }
    }

    private var pageContent: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 0) {
                Text("Tesla").sparcFont(30, weight: .bold).accessibilityAddTraits(.isHeader)
                SPARCInfoButton("Tesla approval") {
                    Text("Tesla asks you to approve SPARC once in the Tesla app. Approve it there, then come back and tap “I’ve approved it”.")
                    Text("Until a car approves SPARC, the virtual car charges. Nothing starts or stops when you switch cars.")
                }
                Spacer(minLength: 0)
            }
            if store.connectionsIsStale { offlineNotice }
            if let tesla {
                if !tesla.configured {
                    notice("Tesla isn't available on this SPARC controller yet.")
                } else if !tesla.authorized && store.teslaAccounts.isEmpty {
                    connectCard
                } else {
                    if store.teslaHoldsSeveralAccounts { accountsCard } else { accountCard }
                    if tesla.authorized { carCard }
                    if selectedCar != nil {
                        approvalCard
                    }
                }
            } else if !store.connectionsIsStale {
                notice("Connect to your SPARC controller to set up Tesla.")
            }
            feedback
        }
        .padding(16).padding(.bottom, 24)
        .frame(maxWidth: 640).frame(maxWidth: .infinity)
    }

    private var offlineNotice: some View {
        card {
            Label("Can't reach your SPARC controller", systemImage: "wifi.slash")
                .sparcFont(15, weight: .semibold).foregroundStyle(SPARCTheme.warning)
            Button("Try again") { Task { await store.refresh(); if connected { await check() } } }
                .disabled(store.busy).foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
        }
    }

    private var connectCard: some View {
        card {
            Image(systemName: "bolt.car").font(.system(size: 28, weight: .semibold)).foregroundStyle(SPARCTheme.spark)
                .accessibilityHidden(true)
            Text("Connect your Tesla").sparcFont(20, weight: .semibold).accessibilityAddTraits(.isHeader)
            SPARCActionButton(title: store.teslaAuthorizationInProgress ? "Finish signing in to Tesla" : "Connect Tesla",
                              symbol: "arrow.up.right", kind: .primary, height: 52,
                              busy: store.busy || store.teslaAuthorizationInProgress) {
                Task { await connect() }
            }
            .disabled(unavailable || store.teslaAuthorizationInProgress)
            .accessibilityIdentifier("tesla.connect")
        }
    }

    private var accountCard: some View {
        card {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill").font(.system(size: 22)).foregroundStyle(SPARCTheme.spark)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Tesla account connected").sparcFont(17, weight: .semibold)
                    Text(selectedCar.map { "Car: \(name($0))" } ?? "Pick your car below")
                        .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                }
                Spacer(minLength: 0)
            }
            .accessibilityElement(children: .combine).accessibilityIdentifier("tesla.account")
            HStack(spacing: 10) {
                SPARCActionButton(title: "Switch account", symbol: "arrow.left.arrow.right", kind: .tinted, height: 46) {
                    confirmSwitchAccount = true
                }
                .disabled(unavailable || store.teslaAuthorizationInProgress)
                .accessibilityIdentifier("tesla.switchAccount")
                SPARCActionButton(title: "Remove", symbol: "xmark", kind: .danger, height: 46) { confirmRemove = true }
                    .disabled(unavailable)
                    .accessibilityIdentifier("tesla.remove")
            }
        }
    }

    /// A Pi that holds several Tesla accounts: each with its cars, a way back in when its sign-in lapsed, and Remove.
    private var accountsCard: some View {
        card {
            Text(store.teslaAccounts.count > 1 ? "Tesla accounts" : "Tesla account")
                .sparcFont(17, weight: .semibold).accessibilityAddTraits(.isHeader)
            ForEach(store.teslaAccounts) { account in accountRow(account) }
            SPARCActionButton(title: store.teslaAuthorizationInProgress ? "Finish signing in to Tesla" : "Add another Tesla account",
                              symbol: "plus", kind: .tinted, height: 46,
                              busy: store.teslaAuthorizationInProgress) {
                Task { await addAccount() }
            }
            .disabled(unavailable || store.teslaAuthorizationInProgress)
            .accessibilityIdentifier("tesla.addAccount")
        }
    }

    private func accountRow(_ account: ControllerConnections.Tesla.Account) -> some View {
        HStack(spacing: 12) {
            Image(systemName: account.authorized ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .font(.system(size: 20)).foregroundStyle(account.authorized ? SPARCTheme.spark : SPARCTheme.warning)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(account.label).sparcFont(16, weight: .semibold)
                Text(accountSubtitle(account)).sparcFont(13)
                    .foregroundStyle(account.authorized ? SPARCTheme.muted : SPARCTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("tesla.account.\(account.id)")
            Spacer(minLength: 8)
            if !account.authorized {
                Button("Sign in") { Task { await addAccount() } }
                    .disabled(unavailable || store.teslaAuthorizationInProgress)
                    .foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
                    .accessibilityLabel("Sign in to \(account.label) again")
                    .accessibilityIdentifier("tesla.signInAgain.\(account.id)")
            }
            Button { removingAccount = account } label: {
                Image(systemName: "xmark.circle").font(.system(size: 20)).foregroundStyle(SPARCTheme.muted)
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .buttonStyle(SPARCPressStyle())
            .disabled(unavailable)
            .accessibilityLabel("Remove \(account.label)")
            .accessibilityIdentifier("tesla.removeAccount.\(account.id)")
        }
    }

    private func accountSubtitle(_ account: ControllerConnections.Tesla.Account) -> String {
        if !account.authorized { return "Sign in again to see this account's cars" }
        if account.error != nil { return "Couldn't reach Tesla for this account" }
        let names = cars.filter { $0.accountId == account.id }.map { name($0) }
        let inUse = teslaActive && tesla?.accountId == account.id ? " · Charging" : ""
        switch names.count {
        case 0: return account.vehicleCount == 0 ? "No cars on this account" : "\(account.vehicleCount) car\(account.vehicleCount == 1 ? "" : "s")"
        case 1, 2: return names.joined(separator: ", ") + inUse
        default: return "\(names[0]) and \(names.count - 1) more" + inUse
        }
    }

    private struct CarGroup: Identifiable {
        let id: String
        let title: String?
        let cars: [ControllerConnections.Tesla.Vehicle]
    }

    /// Cars grouped by account when there are several accounts, so an owner can tell where each one comes from.
    private var carGroups: [CarGroup] {
        let accounts = store.teslaAccounts
        guard accounts.count > 1 else { return [CarGroup(id: "all", title: nil, cars: cars)] }
        var groups = accounts.compactMap { account -> CarGroup? in
            let owned = cars.filter { $0.accountId == account.id }
            return owned.isEmpty ? nil : CarGroup(id: account.id, title: account.label, cars: owned)
        }
        let known = Set(accounts.map(\.id))
        let other = cars.filter { $0.accountId.map { !known.contains($0) } ?? true }
        if !other.isEmpty { groups.append(CarGroup(id: "other", title: "Other", cars: other)) }
        return groups
    }

    private var carCard: some View {
        card {
            Text(cars.count > 1 ? "Your cars" : "Your car").sparcFont(17, weight: .semibold).accessibilityAddTraits(.isHeader)
            if !cars.isEmpty {
                ForEach(carGroups) { group in
                    if let title = group.title {
                        Text(title.uppercased()).sparcFont(11, weight: .bold).tracking(0.6).foregroundStyle(SPARCTheme.tertiary)
                            .padding(.top, 4).accessibilityAddTraits(.isHeader)
                    }
                    ForEach(group.cars) { car in carRow(car) }
                }
            } else if store.connectionsIsStale {
                Text("Reconnect to see your car.").sparcFont(14).foregroundStyle(SPARCTheme.muted)
            } else if checkState == .done {
                Text(store.teslaAccounts.count > 1 ? "No cars on these Tesla accounts" : "No cars on this Tesla account")
                    .sparcFont(16, weight: .semibold)
                    .accessibilityIdentifier("tesla.carsMessage")
                SPARCActionButton(title: store.teslaHoldsSeveralAccounts ? "Add another account" : "Switch account",
                                  symbol: store.teslaHoldsSeveralAccounts ? "plus" : "arrow.left.arrow.right", kind: .primary, height: 50) {
                    if store.teslaHoldsSeveralAccounts { Task { await addAccount() } } else { confirmSwitchAccount = true }
                }
                .disabled(unavailable)
                .accessibilityIdentifier("tesla.noCarsSwitchAccount")
                checkAgainButton
            } else if checkState == .failed {
                Text("Couldn't load your cars from Tesla.").sparcFont(14).foregroundStyle(SPARCTheme.warning)
                    .accessibilityIdentifier("tesla.carsMessage")
                checkAgainButton
            } else {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Finding your cars…").sparcFont(14).foregroundStyle(SPARCTheme.muted)
                }
                .accessibilityElement(children: .combine).accessibilityIdentifier("tesla.carsMessage")
            }
        }
    }

    private var checkAgainButton: some View {
        Button("Check again") { Task { await check() } }
            .disabled(unavailable).foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
            .accessibilityIdentifier("tesla.loadCars")
    }

    private func carRow(_ car: ControllerConnections.Tesla.Vehicle) -> some View {
        let isSelected = car.id == tesla?.vehicleId
        return Button { Task { await choose(car) } } label: {
            HStack(spacing: 12) {
                Image(systemName: "car.side.fill").foregroundStyle(isSelected ? SPARCTheme.spark : SPARCTheme.muted)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(name(car)).sparcFont(16, weight: .semibold).foregroundStyle(.white)
                    Text(isSelected ? carStatus : "Tap to use this car").sparcFont(13).foregroundStyle(SPARCTheme.muted)
                }
                Spacer(minLength: 8)
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? SPARCTheme.spark : SPARCTheme.tertiary).accessibilityHidden(true)
            }
            .padding(.vertical, 6).contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .disabled(unavailable)
        .accessibilityIdentifier("tesla.car.\(car.id)")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var carStatus: String {
        guard let reading = tesla?.chargeState else { return "Selected" }
        return "\(Int(reading.batteryPercent.rounded()))% · \(reading.pluggedIn ? "Plugged in" : "Not plugged in")"
    }

    @ViewBuilder private var approvalCard: some View {
        if tesla?.commandsReady == true {
            Label("SPARC can start and stop charging", systemImage: "checkmark.shield.fill")
                .sparcFont(14, weight: .semibold).foregroundStyle(SPARCTheme.spark)
                .frame(maxWidth: .infinity, alignment: .leading).padding(16).sparcSurface(radius: 20)
                .accessibilityIdentifier("tesla.ready")
        } else if checkState == .done, tesla?.error == nil {
            card {
                Text("Allow charging control").sparcFont(17, weight: .semibold).accessibilityAddTraits(.isHeader)
                if let url = tesla?.keyPairingURL {
                    SPARCActionButton(title: "Approve in Tesla app", symbol: "arrow.up.right", kind: .primary, height: 50) {
                        awaitingApproval = true
                        openURL(url)
                    }
                    .disabled(unavailable)
                    .accessibilityIdentifier("tesla.approveKey")
                }
                Button("I've approved it") { Task { await check() } }
                    .disabled(unavailable).foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
                    .accessibilityIdentifier("tesla.checkApproval")
            }
        }
    }

    @ViewBuilder private var feedback: some View {
        if let error = store.errorMessage ?? tesla?.error {
            Text(TeslaMessage.friendly(error)).sparcFont(13).foregroundStyle(SPARCTheme.dangerText)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("tesla.error")
        }
    }

    private func connect() async {
        await store.authorizeTesla()
        if connected { await check() }
    }

    private func switchAccount() async {
        await store.switchTeslaAccount()
        if connected { await check() }
    }

    private func addAccount() async {
        await store.addTeslaAccount()
        if connected { await check() }
    }

    /// Asks Tesla for the account's cars and whether SPARC is approved. One car is chosen for the owner.
    private func check() async {
        guard !store.connectionsIsStale, !store.isDemo, connected else { return }
        checkState = .checking
        await store.refreshTesla()
        if store.errorMessage == nil, tesla?.vehicleId == nil, cars.count == 1 {
            await store.selectTeslaVehicle(cars[0].id)
        }
        checkState = store.errorMessage == nil ? .done : .failed
    }

    private func choose(_ car: ControllerConnections.Tesla.Vehicle) async {
        guard car.id != tesla?.vehicleId else { return }
        // The Pi changes car only while the virtual car charges, so a switch between Teslas asks first.
        if teslaActive { switchingTo = car; return }
        await store.selectTeslaVehicle(car.id)
        checkState = store.errorMessage == nil ? .done : .failed
    }

    private func name(_ car: ControllerConnections.Tesla.Vehicle) -> String { car.name(among: cars) }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) { content() }
            .frame(maxWidth: .infinity, alignment: .leading).padding(16).sparcSurface(radius: 20)
    }

    private func notice(_ text: String) -> some View {
        Text(text).sparcFont(14).foregroundStyle(SPARCTheme.muted).fixedSize(horizontal: false, vertical: true)
    }
}

/// "Charging with": one place to move Home and charging controls between the virtual car and each of the
/// owner's Teslas. Switching never starts or stops charging, and each car keeps its own history.
struct ChargingCarSwitcher: View {
    private enum Choice: Equatable {
        case virtual
        case tesla(ControllerConnections.Tesla.Vehicle)
    }

    @EnvironmentObject private var store: AppStore
    var offersTeslaSetup = true
    /// False shows only the Tesla SPARC has chosen, for screens that list the cars themselves.
    var listsEveryTesla = true
    @State private var pending: Choice?
    @State private var failure: String?

    private var tesla: ControllerConnections.Tesla? { store.connections?.tesla }
    private var allTeslas: [ControllerConnections.Tesla.Vehicle] { tesla?.authorized == true ? tesla?.vehicles ?? [] : [] }
    private var teslaCars: [ControllerConnections.Tesla.Vehicle] {
        listsEveryTesla ? allTeslas : allTeslas.filter { $0.id == tesla?.vehicleId }
    }
    private var virtualName: String { "Virtual \(store.connections?.virtual.model.title ?? "car")" }
    private var unavailable: Bool { store.busy || store.connectionsIsStale || store.isDemo || store.connections == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("CAR PROFILES").sparcFont(11, weight: .bold).tracking(0.6).foregroundStyle(SPARCTheme.muted)
                .padding(.bottom, 4).accessibilityAddTraits(.isHeader)
            option(.virtual)
            if !teslaCars.isEmpty {
                ForEach(teslaCars) { car in
                    Rectangle().fill(SPARCTheme.line).frame(height: 1)
                    option(.tesla(car))
                }
            } else if offersTeslaSetup {
                Rectangle().fill(SPARCTheme.line).frame(height: 1)
                NavigationLink { TeslaConnectionSetupView() } label: {
                    row(title: "Tesla", subtitle: tesla?.authorized == true ? "Pick your car" : "Connect your Tesla",
                        symbol: "bolt.car", trailing: "chevron.right", active: false)
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityIdentifier("carSwitch.setupTesla")
            }
            if let failure {
                Text(failure).sparcFont(13).foregroundStyle(SPARCTheme.dangerText)
                    .fixedSize(horizontal: false, vertical: true).padding(.top, 10)
                    .accessibilityIdentifier("carSwitch.error")
            }
        }
        .padding(16).sparcSurface(radius: 20)
        .confirmationDialog(pending.map { "Charge with \(title($0))?" } ?? "Change car?",
                            isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                            titleVisibility: .visible, presenting: pending) { choice in
            Button("Use \(title(choice))") {
                pending = nil
                Task { await switchTo(choice) }
            }
            Button("Cancel", role: .cancel) { pending = nil }
        } message: { choice in
            Text(message(for: choice))
        }
    }

    private func title(_ choice: Choice) -> String {
        switch choice {
        case .virtual: return virtualName
        case .tesla(let car): return car.name(among: allTeslas)
        }
    }

    private func message(for choice: Choice) -> String {
        switch choice {
        case .virtual: return "Every screen will show the virtual car again. Nothing is sent to your Tesla."
        case .tesla(let car) where store.activeConnectionMode == .tesla && car.id != tesla?.vehicleId:
            return "Every screen will show \(car.name(among: allTeslas)). If it hasn't approved SPARC yet, the virtual car charges until you approve it in the Tesla app."
        case .tesla:
            return "Every screen will show your Tesla. The virtual car's schedule pauses and its history stays with it."
        }
    }

    private func isActive(_ choice: Choice) -> Bool {
        switch choice {
        case .virtual: return store.activeConnectionMode == .virtual
        case .tesla(let car): return store.activeConnectionMode == .tesla && tesla?.vehicleId == car.id
        }
    }

    private func subtitle(_ choice: Choice) -> String {
        switch choice {
        case .virtual:
            guard let car = store.connections?.virtual else { return "Virtual car" }
            return "\(Int(car.batteryPercent.rounded()))% · \(car.pluggedIn ? "Plugged in" : "Not plugged in")"
        case .tesla(let car):
            if car.id == tesla?.vehicleId, let reading = tesla?.chargeState {
                return "\(Int(reading.batteryPercent.rounded()))% · \(reading.pluggedIn ? "Plugged in" : "Not plugged in")"
            }
            if store.teslaAccounts.count > 1, let label = store.teslaAccounts.first(where: { $0.id == car.accountId })?.label {
                return "Tesla · \(label)"
            }
            return "Your real car"
        }
    }

    private func option(_ choice: Choice) -> some View {
        let active = isActive(choice)
        let identifier: String
        switch choice {
        case .virtual: identifier = "carSwitch.virtual"
        case .tesla(let car): identifier = "carSwitch.tesla.\(car.id)"
        }
        return Button {
            guard !active else { return }
            failure = nil
            pending = choice
        } label: {
            row(title: title(choice), subtitle: subtitle(choice), symbol: choice == .virtual ? "car.side" : "bolt.car",
                trailing: active ? "checkmark.circle.fill" : "circle", active: active)
        }
        .buttonStyle(SPARCPressStyle())
        .disabled(unavailable)
        .accessibilityIdentifier(identifier)
        .accessibilityAddTraits(active ? .isSelected : [])
        .accessibilityHint(active ? "SPARC is charging with this car" : "Switch charging to this car")
    }

    private func row(title: String, subtitle: String, symbol: String, trailing: String, active: Bool) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).font(.system(size: 18, weight: .semibold))
                .foregroundStyle(active ? SPARCTheme.spark : SPARCTheme.muted).frame(width: 30).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).sparcFont(16, weight: .semibold).foregroundStyle(.white)
                Text(subtitle).sparcFont(13).foregroundStyle(SPARCTheme.muted)
            }
            Spacer(minLength: 8)
            Image(systemName: trailing).foregroundStyle(active ? SPARCTheme.spark : SPARCTheme.tertiary).accessibilityHidden(true)
        }
        .padding(.vertical, 12).contentShape(Rectangle())
    }

    private func switchTo(_ choice: Choice) async {
        switch choice {
        case .tesla(let car): await store.useTeslaCar(car.id)
        case .virtual: await store.activateConnection(.virtual)
        }
        failure = store.errorMessage.map(TeslaMessage.friendly)
    }
}

extension ControllerConnections.Tesla.Vehicle {
    /// The owner's name for the car; two cars with the same name are told apart by the end of their VIN.
    func name(among cars: [Self]) -> String {
        let base = displayName.isEmpty ? "Tesla" : displayName
        guard cars.contains(where: { $0.id != id && ($0.displayName.isEmpty ? "Tesla" : $0.displayName) == base }) else { return base }
        return "\(base) (…\(vin.suffix(4)))"
    }
}

/// Controller and Tesla errors, worded as what an owner can do next.
enum TeslaMessage {
    static func friendly(_ message: String) -> String {
        let text = message.lowercased()
        if text.contains("asleep") || text.contains("stale") || text.contains("did not accept the request") {
            return "Your Tesla didn't answer. If it's asleep, open the Tesla app to wake it, then try again."
        }
        if text.contains("proxy") {
            return "SPARC can see your Tesla, but charging control isn't ready on your controller yet. Restart the controller, then try again."
        }
        if text.contains("virtual-key") || text.contains("approve sparc") || text.contains("verification") {
            return "Approve SPARC in the Tesla app, then try again."
        }
        if text.contains("switch to virtual before selecting") {
            return "Switch to the virtual car before choosing a different Tesla."
        }
        if text.contains("authorization state expired") || text.contains("complete tesla authorization again")
            || text.contains("authorization must be completed again") || text.contains("permission must be reviewed") {
            return "Your Tesla sign-in needs to be done again. Connect Tesla to continue."
        }
        if text.contains("could not be confirmed") || text.contains("could not be reached") {
            return "Couldn't reach Tesla. Check your controller's internet connection, then try again."
        }
        return message
    }
}

private struct TeslaVehicleReading: View {
    let reading: ControllerConnections.Tesla.ChargeState
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(Int(reading.batteryPercent.rounded()))% · \(reading.pluggedIn ? "Plugged in" : "Unplugged") · \(SPARCFormat.decimal(reading.powerKW)) kW")
                .sparcFont(18, weight: .semibold).monospacedDigit()
            Text(reading.state.capitalized).foregroundStyle(SPARCTheme.muted)
            Text("Updated \(reading.updatedAt.formatted(date: .abbreviated, time: .shortened))")
                .sparcFont(12).foregroundStyle(SPARCTheme.muted)
            if reading.stale == true { Text("May be out of date.").foregroundStyle(SPARCTheme.warning) }
        }.accessibilityElement(children: .combine)
    }
}

/// The real car has manual controls; virtual session totals and schedules do not apply.
struct TeslaManualChargingView: View {
    @EnvironmentObject private var store: AppStore
    @State private var pendingCommand: TeslaChargeCommand?
    @State private var chargeLimit = 80
    @State private var showSetup = false
    var onSettings: (() -> Void)? = nil

    private var unavailable: Bool { store.busy || !store.canSendTeslaCommands }

    var body: some View {
        ScrollView { pageContent }
        .scrollIndicators(.hidden).background(SPARCTheme.background)
        .sheet(isPresented: $showSetup) { setupSheet }
        .confirmationDialog(commandDialogTitle, isPresented: commandDialogPresented, titleVisibility: .visible) {
            commandDialogActions
        } message: { commandDialogMessage }
    }

    private var pageContent: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            selectedProvider
            vehicleReading
            refreshControl
            if store.activeConnectionMode != .tesla || store.profileTeslaID != store.connections?.tesla.vehicleId {
                SPARCActionButton(title: "Enable charging for this Tesla", symbol: "bolt.car", kind: .tinted, height: 50) {
                    Task { await store.useTeslaCar(store.profileTeslaID ?? "") }
                }.disabled(store.busy || store.connections?.tesla.commandsReady != true || store.profileTeslaID != store.connections?.tesla.vehicleId)
            }
            commandControls
            limitControl
            feedback
            Button("Tesla account and cars") { showSetup = true }
        }
        .padding(16).padding(.bottom, 24)
        .frame(maxWidth: 640).frame(maxWidth: .infinity)
    }

    private var header: some View {
        HStack {
            Text("Tesla charging").sparcFont(30, weight: .bold).accessibilityAddTraits(.isHeader)
            Spacer()
            if let onSettings {
                Button(action: onSettings) { Image(systemName: "gearshape").frame(width: 44, height: 44) }
                    .accessibilityLabel("Settings").accessibilityIdentifier("home.settings")
            }
        }
    }

    private var selectedProvider: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(store.activeConnectionMode == .tesla && store.profileTeslaID == store.connections?.tesla.vehicleId ? "Charging with your Tesla" : "Tesla profile").sparcFont(14, weight: .semibold).foregroundStyle(SPARCTheme.spark)
                .accessibilityIdentifier("tesla.controlsProvider")
            Text(store.chargingCarName).sparcFont(22, weight: .semibold)
        }
    }

    @ViewBuilder private var vehicleReading: some View {
        if let reading = store.profileTeslaReading {
            TeslaVehicleReading(reading: reading).padding(16).frame(maxWidth: .infinity, alignment: .leading).sparcSurface(radius: 20)
        } else {
            Text("No battery reading yet.")
                .foregroundStyle(SPARCTheme.muted)
        }
    }

    private var refreshControl: some View {
        VStack(alignment: .leading, spacing: 16) {
            SPARCActionButton(title: "Refresh Tesla", symbol: "arrow.clockwise", kind: .tinted, height: 52, busy: store.busy) {
                Task { await refreshTesla() }
            }.disabled(store.busy || store.isDemo)
                .accessibilityIdentifier("tesla.refreshReading")
            if store.connections?.tesla.commandsReady != true || store.connectionsIsStale {
                Text(store.connections?.tesla.commandsReady != true ? "You can view this car and its history. Approve SPARC's key in the Tesla app to enable charging controls." : "Tap Refresh, or open the Tesla app to wake the car.")
                    .sparcFont(13).foregroundStyle(SPARCTheme.warning)
            }
        }
    }

    private var commandControls: some View {
        VStack(spacing: 16) {
            SPARCActionButton(title: "Start charging", symbol: "bolt.fill", kind: .primary, height: 54) { pendingCommand = .start }
                .disabled(unavailable).opacity(unavailable ? 0.45 : 1).accessibilityIdentifier("tesla.startCharging")
            SPARCActionButton(title: "Stop charging", symbol: "pause.fill", kind: .tinted, height: 54) { pendingCommand = .stop }
                .disabled(unavailable).opacity(unavailable ? 0.45 : 1).accessibilityIdentifier("tesla.stopCharging")
        }
    }

    private var limitControl: some View {
        VStack(alignment: .leading, spacing: 12) {
            Stepper("Charge limit · \(chargeLimit)%", value: $chargeLimit, in: 50...100, step: 5)
                .disabled(unavailable).accessibilityIdentifier("tesla.chargeLimit")
            Button("Send \(chargeLimit)% limit to Tesla") { pendingCommand = .setLimit }.disabled(unavailable)
        }.padding(16).sparcSurface(radius: 20)
    }

    @ViewBuilder private var feedback: some View {
        if let message = store.teslaCommandMessage { Text(message).sparcFont(13).foregroundStyle(SPARCTheme.spark) }
        if let error = store.errorMessage { Text(TeslaMessage.friendly(error)).sparcFont(13).foregroundStyle(SPARCTheme.dangerText) }
    }

    private var setupSheet: some View {
        NavigationStack {
            TeslaConnectionSetupView()
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { showSetup = false } } }
        }.preferredColorScheme(.dark)
    }

    private var commandDialogTitle: String { pendingCommand?.title ?? "Tesla command" }

    private var commandDialogPresented: Binding<Bool> {
        Binding(get: { pendingCommand != nil }, set: { if !$0 { pendingCommand = nil } })
    }

    @ViewBuilder private var commandDialogActions: some View {
        if let command = pendingCommand {
            Button(command == .setLimit ? "Set Tesla limit to \(chargeLimit)%" : command.title) {
                Task { await store.sendTeslaCommand(command, value: command == .setLimit ? chargeLimit : nil) }
            }
        }
        Button("Cancel", role: .cancel) { pendingCommand = nil }
    }

    private var commandDialogMessage: some View {
        Text("Sends a real charging command to \(store.connections?.tesla.vehicleName ?? "your selected Tesla").")
    }

    private func refreshTesla() async {
        if store.connectionsIsStale { await store.refresh() }
        if !store.connectionsIsStale { await store.refreshProfileTesla() }
    }
}

// MARK: - Shared drawing (also used by the pairing screen)

/// The Bluetooth rune, drawn as a stroke so it scales with Dynamic Type.
struct ConnectionBluetoothGlyph: Shape {
    func path(in rect: CGRect) -> Path {
        let s = min(rect.width, rect.height) / 24
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + x * s, y: rect.minY + y * s) }
        var path = Path()
        path.move(to: p(7, 7))
        path.addLine(to: p(17, 17))
        path.addLine(to: p(12, 21))
        path.addLine(to: p(12, 3))
        path.addLine(to: p(17, 7))
        path.addLine(to: p(7, 17))
        return path
    }
}

struct ConnectionBluetoothMark: View {
    var size: CGFloat = 18
    var color: Color = SPARCTheme.spark
    var weight: CGFloat = 2.3
    var body: some View {
        ConnectionBluetoothGlyph()
            .stroke(color, style: StrokeStyle(lineWidth: size / 24 * weight, lineCap: .round, lineJoin: .round))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

/// A Raspberry Pi board drawn from shapes. `mini` is the small icon used on device cards.
struct ConnectionPiBoard: View {
    var pcbStroke: Color = SPARCTheme.green
    var pins: Color = SPARCTheme.spark
    var led: Color = SPARCTheme.spark
    var mini = false

    var body: some View {
        Canvas { context, size in
            let scale = min(size.width / 132, size.height / 96)
            func rect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat) -> Path {
                Path(roundedRect: CGRect(x: x * scale, y: y * scale, width: w * scale, height: h * scale),
                     cornerRadius: r * scale, style: .continuous)
            }
            func dot(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat) -> Path {
                Path(ellipseIn: CGRect(x: (x - r) * scale, y: (y - r) * scale, width: r * 2 * scale, height: r * 2 * scale))
            }
            let pcb = Color(red: 15 / 255, green: 26 / 255, blue: 20 / 255)
            let chip = Color(white: 0.102)
            let edge = Color(white: 0.227)
            if mini {
                context.fill(rect(2.5, 2.5, 127, 91, 12), with: .color(pcb))
                context.stroke(rect(2.5, 2.5, 127, 91, 12), with: .color(pcbStroke), lineWidth: 5 * scale)
                context.fill(rect(44, 34, 38, 38, 6), with: .color(chip))
                context.stroke(rect(44, 34, 38, 38, 6), with: .color(pins), lineWidth: 3 * scale)
                context.fill(dot(18, 80, 6), with: .color(led))
            } else {
                context.fill(rect(2, 2, 128, 92, 12), with: .color(pcb))
                context.stroke(rect(2, 2, 128, 92, 12), with: .color(pcbStroke), lineWidth: 2 * scale)
                for row in [CGFloat(11), 17] {
                    for column in 0..<14 { context.fill(dot(16 + CGFloat(column) * 6, row, 1.7), with: .color(pins)) }
                }
                context.fill(rect(44, 36, 36, 36, 6), with: .color(chip))
                context.stroke(rect(44, 36, 36, 36, 6), with: .color(edge), lineWidth: 1.5 * scale)
                context.fill(rect(52, 44, 20, 20, 3), with: .color(Color(white: 0.137)))
                context.fill(rect(88, 42, 18, 14, 3), with: .color(chip))
                context.stroke(rect(88, 42, 18, 14, 3), with: .color(edge), lineWidth: 1.2 * scale)
                for y in [CGFloat(24), 46, 68] { context.fill(rect(114, y, 18, 16, 2), with: .color(Color(white: 0.165))) }
                context.fill(dot(16, 82, 3.4), with: .color(led))
            }
        }
        .aspectRatio(132.0 / 96.0, contentMode: .fit)
        .accessibilityHidden(true)
    }
}

/// Soft static glow behind a screen. It never moves.
struct ConnectionAmbientGlow: View {
    var color: Color
    var top: CGFloat = 30
    var body: some View {
        ZStack(alignment: .top) {
            SPARCTheme.background
            RadialGradient(colors: [color, .clear], center: .center, startRadius: 0, endRadius: 184)
                .frame(width: 368, height: 368)
                .offset(y: top - 60)
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

// MARK: - Connection screen

private enum ConnectionLook: Equatable { case connected, reconnecting, offline, none }

/// Home controller connection: status, sync, reconnect and forget.
struct ConnectionView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var manager: ProvisioningManager
    @Environment(\.sparcToast) private var toast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    /// A sync or reconnect started from this screen is running.
    @State private var working = false
    @State private var showSetup = false
    @State private var confirmForget = false
    @State private var actionError: String?

    private var controller: PairedController? { manager.savedController }

    private var look: ConnectionLook {
        if store.isDemo { return .connected }
        guard controller != nil else { return .none }
        if store.snapshot.state != .offline { return .connected }
        return working ? .reconnecting : .offline
    }

    private var overBluetooth: Bool { store.isDemo || controller?.bluetoothPeripheralID != nil }
    private var transport: String { overBluetooth ? "Bluetooth" : "Home network" }
    /// Pi status and Wi-Fi come over the saved Bluetooth pairing only.
    private var hasBluetoothController: Bool { controller?.bluetoothPeripheralID != nil }

    private var title: String { look == .none ? "No controller yet" : "Home controller" }
    private var subtitle: String {
        if look == .none { return "Connect your Raspberry Pi to charge on your schedule." }
        guard !store.isDemo, let controller else { return "Bluetooth" }
        // The Bluetooth name tells two Pis apart (every app-test Pi reports the same deviceName),
        // and the model is no longer assumed: this works for a Pi 4 or a Pi 5.
        return "\(controller.displayName) · \(transport)"
    }
    private var statusText: String {
        switch look {
        case .connected: "Connected"
        case .reconnecting: "Reconnecting…"
        case .offline, .none: "Not connected"
        }
    }
    private var statusColor: Color {
        switch look {
        case .connected: SPARCTheme.spark
        case .reconnecting: SPARCTheme.mint
        case .offline, .none: Color(white: 0.79)
        }
    }
    private var ambient: Color {
        switch look {
        case .connected: SPARCTheme.spark.opacity(0.16)
        case .reconnecting: SPARCTheme.spark.opacity(0.08)
        case .offline, .none: Color(white: 0.47).opacity(0.06)
        }
    }

    /// Facts the Pi itself reported over Bluetooth, e.g. "Wi-Fi: Home · Running for 3h 12m · 42°C".
    private var piFacts: [String] {
        guard !store.isDemo, hasBluetoothController, let status = manager.controllerStatus else { return [] }
        var items: [String] = []
        if let wifi = status.wifi {
            items.append(wifi.connected ? "Wi-Fi: \(wifi.ssid ?? "connected")" : "Wi-Fi: not connected")
        }
        items.append("Running for \(SPARCFormat.duration(status.uptimeSeconds / 60))")
        if let celsius = status.temperatureC { items.append("\(Int(celsius.rounded()))°C") }
        return items
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                hero
                switch look {
                case .connected:
                    facts.padding(.top, 20)
                    if !piFacts.isEmpty { piFactsRow.padding(.top, 10) }
                    syncButton.padding(.top, 12)
                case .reconnecting:
                    reconnectingCard.padding(.top, 20)
                case .offline:
                    offlineActions.padding(.top, 20)
                case .none:
                    SPARCActionButton(title: "Connect a controller", symbol: "plus", kind: .primary, height: 58) { showSetup = true }
                        .accessibilityIdentifier("connection.connect")
                        .padding(.top, 24)
                }
                if look != .none {
                    chargingProvider.padding(.top, 14)
                    nightNote.padding(.top, 14)
                    options.padding(.top, 12)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 32)
            .frame(maxWidth: 640)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .background { ConnectionAmbientGlow(color: ambient) }
        .navigationTitle("Connection")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(SPARCTheme.background, for: .navigationBar)
        .navigationDestination(isPresented: $showSetup) { ControllerSetupView() }
        .confirmationDialog("Forget this controller?", isPresented: $confirmForget, titleVisibility: .visible) {
            Button("Forget controller", role: .destructive) { forget() }
            Button("Keep it", role: .cancel) {}
        } message: {
            Text("The Pi keeps its schedule. You can reconnect any time.")
        }
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: look)
        .task {
            // Fresh Pi facts each time the screen opens, but only while the controller is reachable.
            if !store.isDemo, hasBluetoothController, look == .connected { await manager.refreshStatus() }
        }
    }

    // MARK: Hero

    private var hero: some View {
        VStack(spacing: 0) {
            ConnectionHero(look: look)
            HStack(spacing: 8) {
                if look == .connected { SPARCStatusDot() }
                Text(statusText).sparcFont(14, weight: .bold).tracking(0.14)
                    .foregroundStyle(statusColor)
            }
            .padding(.top, 4)
            .accessibilityIdentifier("connection.status")
            Text(title).sparcFont(28, weight: .bold).tracking(-0.84)
                .multilineTextAlignment(.center)
                .padding(.top, 8)
                .accessibilityAddTraits(.isHeader)
            Text(subtitle).sparcFont(14).foregroundStyle(SPARCTheme.muted)
                .multilineTextAlignment(.center)
                .padding(.top, 4)
                .padding(.horizontal, 12)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
    }

    // MARK: Connected

    private var facts: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(spacing: 8))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 8))
        return layout {
            ConnectionFactTile(title: "Connection") {
                Group {
                    if overBluetooth { ConnectionBluetoothMark(size: 20) }
                    else { Image(systemName: "wifi").font(.system(size: 18, weight: .semibold)).foregroundStyle(SPARCTheme.spark).accessibilityHidden(true) }
                }
                .frame(height: 22, alignment: .leading)
                Text(transport).sparcFont(14, weight: .semibold)
                    .lineLimit(2).minimumScaleFactor(0.85)
            }
            ConnectionFactTile(title: "Last sync") {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    Text(working ? "Syncing" : SPARCFormat.ago(store.snapshot.updatedAt, now: context.date))
                        .sparcFont(20, weight: .bold).tracking(-0.4).monospacedDigit()
                        .foregroundStyle(working ? SPARCTheme.spark : .white)
                        .lineLimit(1).minimumScaleFactor(0.7)
                }
            }
            ConnectionFactTile(title: "Security") {
                Image(systemName: "lock").font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(SPARCTheme.spark)
                    .frame(height: 22, alignment: .leading)
                    .accessibilityHidden(true)
                Text("Encrypted").sparcFont(14, weight: .semibold)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    /// One quiet line of real facts from the Pi.
    private var piFactsRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: "cpu").font(.system(size: 12, weight: .semibold))
                .foregroundStyle(SPARCTheme.tertiary)
                .accessibilityHidden(true)
            Text(piFacts.joined(separator: " · ")).sparcFont(12.5).monospacedDigit()
                .foregroundStyle(SPARCTheme.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 6)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("connection.piFacts")
    }

    private var syncButton: some View {
        SPARCActionButton(title: working ? "Syncing…" : "Sync now", symbol: "arrow.clockwise", kind: .tinted, height: 54, busy: working) {
            Task { await sync(announce: "Synced · schedule and charge are up to date") }
        }
        .accessibilityIdentifier("connection.syncNow")
    }

    // MARK: Reconnecting and offline

    private var reconnectingCard: some View {
        HStack(spacing: 12) {
            ProgressView().controlSize(.small).tint(SPARCTheme.spark)
                .frame(width: 30, height: 30)
            Text("Reconnecting to your controller").sparcFont(15, weight: .semibold)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .sparcSurface(radius: 24)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("connection.reconnecting")
    }

    private var offlineActions: some View {
        VStack(spacing: 0) {
            SPARCActionButton(title: "Reconnect", symbol: "arrow.clockwise", kind: .primary, height: 58) {
                Task { await sync(announce: "Connected to Home controller") }
            }
            .shadow(color: SPARCTheme.spark.opacity(0.3), radius: 15, y: 10)
            .accessibilityIdentifier("connection.reconnect")
            if let message = store.connectionMessage {
                Text(message).sparcFont(12.5).foregroundStyle(SPARCTheme.tertiary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 6)
                    .accessibilityIdentifier("connection.message")
            }
            tips.padding(.top, 14)
        }
    }

    private var tips: some View {
        let items = overBluetooth
            ? ["Move within 10 meters of the Pi.",
               "Check the Pi has power.",
               "Turn Bluetooth on."]
            : ["Join your Pi's Wi-Fi.",
               "Check the Pi has power.",
               "Allow Local Network for SPARC."]
        return VStack(alignment: .leading, spacing: 0) {
            Text("If it doesn't connect").sparcFont(13, weight: .semibold)
                .foregroundStyle(Color(white: 0.79))
                .padding(.top, 12).padding(.bottom, 4)
                .accessibilityAddTraits(.isHeader)
            ForEach(Array(items.enumerated()), id: \.offset) { index, text in
                if index > 0 { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(index + 1)").sparcFont(13.5, weight: .bold).foregroundStyle(SPARCTheme.spark)
                    Text(text).sparcFont(13.5).foregroundStyle(SPARCTheme.muted)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 9).padding(.bottom, index == items.count - 1 ? 13 : 9)
                .accessibilityElement(children: .combine)
            }
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 22)
    }

    // MARK: Night note and options

    private var chargingProvider: some View {
        NavigationLink { TeslaConnectionSetupView() } label: {
            HStack(spacing: 12) {
                Image(systemName: "car.side").foregroundStyle(SPARCTheme.spark)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Charging with").sparcFont(12).foregroundStyle(SPARCTheme.muted)
                    Text(store.chargingCarName).sparcFont(16, weight: .semibold)
                    if store.connectionsIsStale { Text("Last confirmed").sparcFont(12).foregroundStyle(SPARCTheme.warning) }
                }
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(SPARCTheme.tertiary)
            }
            .padding(16).sparcSurface(radius: 20)
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityIdentifier("connection.chargingProvider")
    }

    private var nightNote: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "moon").font(.system(size: 15, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark)
                .padding(.top, 1)
                .accessibilityHidden(true)
            Text(store.activeConnectionMode == .tesla
                 ? "Tesla charging uses manual commands. Refresh vehicle readings when you need them."
                 : "Your Pi keeps tonight's plan running even when your phone is away.")
                .sparcFont(13.5).foregroundStyle(Color(white: 0.79))
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 20)
        .accessibilityElement(children: .combine)
    }

    private var options: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let actionError {
                Text(actionError).sparcFont(13).foregroundStyle(SPARCTheme.dangerText)
                    .padding(.horizontal, 4)
                    .accessibilityIdentifier("connection.error")
            }
            VStack(spacing: 0) {
                if hasBluetoothController {
                    NavigationLink { ControllerStatusView() } label: {
                        optionRow("Status & Wi-Fi", chevron: true)
                    }
                    .buttonStyle(SPARCPressStyle())
                    .accessibilityIdentifier("connection.statusWiFi")
                    optionDivider
                }
                Button { showSetup = true } label: {
                    optionRow(controller == nil ? "Connect a controller" : "Connect another controller", chevron: true)
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityIdentifier("connection.connectAnother")
                if controller != nil {
                    optionDivider
                    Button { actionError = nil; confirmForget = true } label: {
                        optionRow("Forget this controller", color: SPARCTheme.dangerText)
                    }
                    .buttonStyle(SPARCPressStyle())
                    .disabled(store.busy)
                    .accessibilityIdentifier("connection.forget")
                }
            }
            .sparcSurface(radius: 22)
        }
    }

    private var optionDivider: some View { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }

    private func optionRow(_ title: String, color: Color = .white, chevron: Bool = false) -> some View {
        HStack(spacing: 12) {
            Text(title).sparcFont(15, weight: .semibold).foregroundStyle(color)
                .multilineTextAlignment(.leading)
            Spacer(minLength: 8)
            if chevron {
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color(white: 0.43))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        .contentShape(Rectangle())
    }

    // MARK: Actions

    /// Refresh from the controller. A poll that is already running makes `refresh()` return at once,
    /// so wait for the radio to settle and keep the spinner up long enough to read.
    private func sync(announce message: String) async {
        guard !working else { return }
        working = true
        let started = ContinuousClock.now
        await store.refresh()
        var waited = 0
        while (manager.isWorking || manager.stage == .connecting) && !store.isDemo && waited < 60 {
            try? await Task.sleep(for: .milliseconds(400))
            waited += 1
        }
        // Pi facts ride the same Bluetooth link, so ask only once the controller has answered.
        if !store.isDemo, store.snapshot.state != .offline { await manager.refreshStatus() }
        let minimum = Duration.milliseconds(700)
        let elapsed = started.duration(to: .now)
        if elapsed < minimum { try? await Task.sleep(for: minimum - elapsed) }
        working = false
        if store.isDemo || store.snapshot.state != .offline { toast(message) }
    }

    private func forget() {
        store.forgetController()
        manager.forget()
        if manager.savedController == nil {
            actionError = nil
            DispatchQueue.main.async { showSetup = true }
        } else {
            actionError = manager.errorMessage ?? store.errorMessage ?? "Couldn't remove the controller. Try again."
        }
    }
}

// MARK: - Pieces

private struct ConnectionHero: View {
    let look: ConnectionLook

    var body: some View {
        let connected = look == .connected
        let dimmed = look == .offline || look == .none
        let shape = RoundedRectangle(cornerRadius: 30, style: .continuous)
        ZStack {
            if look == .connected {
                Circle().strokeBorder(SPARCTheme.spark.opacity(0.22), lineWidth: 1).frame(width: 200, height: 200)
            }
            if look == .reconnecting {
                Circle().strokeBorder(Color(white: 0.137), lineWidth: 1).frame(width: 200, height: 200)
            }
            shape
                .fill(connected ? Color(red: 13 / 255, green: 23 / 255, blue: 18 / 255) : Color(white: 0.055))
                .overlay(shape.strokeBorder(connected ? SPARCTheme.spark.opacity(0.45) : Color(white: 0.165), lineWidth: 1))
                .shadow(color: connected ? SPARCTheme.spark.opacity(0.25) : .clear, radius: 20)
                .frame(width: 150, height: 118)
                .overlay {
                    ConnectionPiBoard(pcbStroke: connected ? SPARCTheme.green : Color(white: 0.227),
                                      pins: connected ? SPARCTheme.spark : Color(white: 0.29),
                                      led: dimmed ? Color(white: 0.29) : SPARCTheme.spark)
                        .frame(width: 118, height: 86)
                        .opacity(dimmed ? 0.45 : 1)
                }
                .overlay(alignment: .bottomTrailing) { badge.offset(x: 12, y: 12) }
        }
        .frame(width: 240, height: 220)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Raspberry Pi controller")
    }

    private var badge: some View {
        let fill: Color = switch look {
        case .connected: SPARCTheme.spark
        case .reconnecting: Color(red: 30 / 255, green: 58 / 255, blue: 44 / 255)
        case .offline, .none: Color(white: 0.165)
        }
        let glyph: Color = switch look {
        case .connected: SPARCTheme.onSpark
        case .reconnecting: SPARCTheme.spark
        case .offline, .none: SPARCTheme.tertiary
        }
        return ZStack {
            Circle().fill(.black).frame(width: 46, height: 46)
            Circle().fill(fill).frame(width: 40, height: 40)
            ConnectionBluetoothMark(size: 18, color: glyph)
        }
    }
}

private struct ConnectionFactTile<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).sparcFont(11.5, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            content
        }
        .padding(.horizontal, 12).padding(.vertical, 13)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .sparcSurface(radius: 20)
        .accessibilityElement(children: .combine)
    }
}
