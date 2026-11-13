import SwiftUI
import UIKit

struct SPARCRootView: View {
    @EnvironmentObject private var store: AppStore
    @State private var selectedTab: SPARCTab = .home
    @State private var sheet: HomeSheet?
    @State private var tabBarTop: CGFloat?

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                profileSelector
                // Tabs switch in place: no slide, no cross-fade.
                Group {
                    switch selectedTab {
                    case .home:
                        if store.profileConnectionMode == .tesla {
                            TeslaManualChargingView(onSettings: { selectedTab = .settings })
                        } else {
                            HomeView(onSettings: { selectedTab = .settings },
                                 onSchedule: { sheet = .schedule },
                                 onConnection: { sheet = .connection },
                                 onCharge: { selectedTab = .charge },
                                 onInsights: { selectedTab = .insights },
                                 onPlan: { selectedTab = .plan })
                        }
                    case .charge: ChargingView(onPlan: { selectedTab = .plan }, onConnection: { sheet = .connection })
                    case .plan:
                        if store.profileConnectionMode == .tesla {
                            ScrollView {
                                VStack(alignment: .leading, spacing: 16) {
                                    Text("Tesla charging is manual").sparcFont(30, weight: .bold)
                                    Button("Open Tesla setup") { sheet = .connection }
                                }.padding(16)
                            }
                        } else { PlanView() }
                    case .insights: InsightsView()
                    case .settings:
                        NavigationStack {
                            SettingsView()
                                .toolbarBackground(.black, for: .navigationBar)
                        }
                    }
                }
                // Only the tab pages sit under the floating bar; sheets keep the default (nil).
                .id(store.activeCarProfileID)
                .environment(\.sparcTabBarTop, tabBarTop)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .padding(.top, topSafeAreaDeficit(geometry))
            .frame(width: geometry.size.width, height: geometry.size.height, alignment: .top)
        }
        .background(Color.black.ignoresSafeArea())
        .foregroundStyle(.white)
        .task(id: store.activeCarProfileID + (store.connectionsIsStale ? ":stale" : ":ready")) { await store.prepareCarProfile() }
        .onChange(of: store.activeCarProfileID) { _, _ in sheet = nil }
        // The bar floats over the pages. As a bottom inset it also gives every page's scroll view the right content inset.
        .safeAreaInset(edge: .bottom, spacing: 0) {
            tabBarArea
                .onGeometryChange(for: CGFloat.self) { $0.frame(in: .global).minY } action: { tabBarTop = $0 }
        }
        .ignoresSafeArea(.container, edges: .bottom)
        .sparcToastHost()
        .sheet(item: $sheet) { value in
            NavigationStack {
                Group {
                    switch value {
                    case .cars: CarProfilesView()
                    case .schedule: CurrentScheduleView()
                    case .prices: PricesView()
                    case .connection: ConnectionView()
                    }
                }
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) { Button("Done") { sheet = nil }.foregroundStyle(.white).accessibilityIdentifier("sheet.done") }
                }
                .toolbarBackground(.black, for: .navigationBar)
            }
            .sparcToastHost()
            .preferredColorScheme(.dark)
            .tint(.white)
            .onAppear { traceUITest("Sheet appeared: \(value.rawValue)") }
        }
        .alert("Couldn't complete that", isPresented: Binding(get: { store.errorMessage != nil && sheet == nil && (selectedTab == .home || selectedTab == .charge) }, set: { if !$0 { store.errorMessage = nil } })) {
            Button("OK") { store.errorMessage = nil }
        } message: { Text(store.errorMessage ?? "Please try again.") }
        .preferredColorScheme(.dark)
        .tint(.white)
    }

    // Some device windows report a smaller top inset than their scene's status bar.
    // Add only that measured deficit; a correct native inset adds no padding.
    private func topSafeAreaDeficit(_ geometry: GeometryProxy) -> CGFloat {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first
        let statusBarHeight = scene?.statusBarManager?.statusBarFrame.height ?? 0
        return max(0, statusBarHeight - geometry.safeAreaInsets.top)
    }

    private var profileSelector: some View {
        Menu {
            Button { store.chooseCarProfile("virtual") } label: {
                Label("Virtual " + (store.connections?.virtual.model.title ?? "car"), systemImage: store.profileConnectionMode == .virtual ? "checkmark.circle.fill" : "car.side")
            }
            ForEach(store.connections?.tesla.vehicles ?? []) { car in
                Button { store.chooseCarProfile("tesla:" + car.id) } label: {
                    Label(car.name(among: store.connections?.tesla.vehicles ?? []), systemImage: store.profileTeslaID == car.id ? "checkmark.circle.fill" : "car.side")
                }
            }
            Divider()
            Button("Manage cars and accounts", systemImage: "gearshape") { sheet = .connection }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "car.side.fill").foregroundStyle(SPARCTheme.spark)
                Text(store.chargingCarName).sparcFont(15, weight: .semibold).lineLimit(1)
                Image(systemName: "chevron.down").font(.system(size: 11, weight: .semibold))
                Spacer()
                if store.busy { ProgressView().tint(SPARCTheme.spark) }
            }.padding(.horizontal, 20).frame(minHeight: 44).background(.black)
        }.accessibilityLabel("Car profile: " + store.chargingCarName)
            .accessibilityIdentifier("profile.currentCar")
    }

    // MARK: Floating tab bar

    private var tabBarArea: some View {
        tabBar
            .padding(.horizontal, 12)
            .padding(.top, 8)
            .padding(.bottom, 20)
            .background(alignment: .bottom) {
                LinearGradient(stops: [.init(color: .black.opacity(0), location: 0),
                                       .init(color: .black.opacity(0.92), location: 0.62),
                                       .init(color: .black.opacity(0.92), location: 1)],
                               startPoint: .top, endPoint: .bottom)
                    .frame(height: 120)
                    .allowsHitTesting(false)
            }
    }

    private var tabBar: some View {
        HStack(spacing: 2) {
            ForEach(SPARCTab.allCases) { tab in tabButton(tab) }
        }
        .padding(.horizontal, 6)
        .frame(height: 68)
        .background {
            ZStack {
                Capsule().fill(.ultraThinMaterial)
                Capsule().fill(Color(white: 20 / 255).opacity(0.74))
            }
        }
        .overlay(Capsule().strokeBorder(.white.opacity(0.09), lineWidth: 1))
        .overlay {
            // The faint light edge along the top of the glass.
            Capsule().strokeBorder(LinearGradient(colors: [.white.opacity(0.07), .clear], startPoint: .top, endPoint: .center), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.6), radius: 20, y: 16)
        // Keep the floating bar readable at the top of the normal text-size range.
        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
    }

    private func tabButton(_ tab: SPARCTab) -> some View {
        let selected = selectedTab == tab
        return Button {
            selectedTab = tab
        } label: {
            VStack(spacing: 3) {
                Image(systemName: tab.symbol)
                    .font(.system(size: 21, weight: .semibold))
                    .frame(height: 24)
                Text(tab.rawValue)
                    .sparcFont(10.5, weight: selected ? .bold : .semibold)
                    .lineLimit(1).minimumScaleFactor(0.8)
            }
            .frame(maxWidth: .infinity, minHeight: 56, maxHeight: 56)
            .foregroundStyle(selected ? SPARCTheme.spark : Color(white: 154 / 255))
            .background(selected ? SPARCTheme.spark.opacity(0.13) : .clear, in: Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel(tab.rawValue)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("tab.\(tab == .insights ? "insights" : tab.rawValue.lowercased())")
    }
}

private enum SPARCTab: String, CaseIterable, Identifiable {
    case home = "Home", charge = "Charge", plan = "Plan", insights = "Savings", settings = "Settings"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .home: return "house"
        case .charge: return "bolt"
        case .plan: return "calendar"
        case .insights: return "chart.line.uptrend.xyaxis"
        case .settings: return "gearshape"
        }
    }
}

private enum HomeSheet: String, Identifiable {
    case schedule, prices, connection, cars
    var id: String { rawValue }
}

// MARK: - Home

private enum HomeTone {
    static let heroLine = Color(white: 38 / 255)
    static let pillFill = Color(white: 14 / 255)
    static let pillText = Color(white: 201 / 255)
    static let iconTile = Color(white: 22 / 255)
    static let pausedIcon = Color(white: 208 / 255)
    static let ctaFill = Color(red: 14 / 255, green: 26 / 255, blue: 20 / 255)
    static let readyDetail = Color(red: 201 / 255, green: 242 / 255, blue: 220 / 255)
    static let footnote = Color(white: 122 / 255)
    static let barOff = Color(white: 44 / 255)
    static let sheetFill = Color(white: 18 / 255)
}

private struct HomeView: View {
    @EnvironmentObject private var store: AppStore
    @AppStorage(SavingsComparison.storageKey) private var savingsComparison: SavingsComparison = .fixedPlan
    @EnvironmentObject private var provisioning: ProvisioningManager
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.sparcToast) private var toast
    @State private var showChargeSheet = false

    var onSettings: () -> Void
    var onSchedule: () -> Void
    var onConnection: () -> Void
    var onCharge: () -> Void
    var onInsights: () -> Void
    var onPlan: () -> Void

    private var snapshot: ChargeSnapshot { store.snapshot }
    private var state: ChargeState { store.snapshot.state }
    private var isCharging: Bool { state == .charging }

    var body: some View {
        ZStack {
            ambientGlow
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    header
                    pillRow.padding(.top, 10)
                    hero.padding(.top, 10)
                    if store.smartNightStreak > 0 { streakCard.padding(.top, 12) }
                    tiles.padding(.top, 10)
                    planCard.padding(.top, 10)
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 16)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .scrollIndicators(.hidden)
            .refreshable { await store.refresh() }
        }
        .sheet(isPresented: $showChargeSheet) {
            ChargeNowSheet(details: chargeDetails, busy: store.busy, onCharge: startCharging, onWait: { showChargeSheet = false })
                .preferredColorScheme(.dark)
        }
    }

    /// A very soft brand-green wash in the top-left corner of the page.
    private var ambientGlow: some View {
        Color.clear.ignoresSafeArea()
            .overlay(alignment: .topLeading) {
                Circle()
                    .fill(RadialGradient(colors: [SPARCTheme.green.opacity(0.16), SPARCTheme.green.opacity(0)],
                                         center: .center, startRadius: 0, endRadius: 193))
                    .frame(width: 420, height: 420)
                    .offset(x: -120, y: -170)
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            // Refreshes each minute so the clock and greeting stay right. Nothing animates.
            TimelineView(.everyMinute) { context in
                VStack(alignment: .leading, spacing: 4) {
                    Text(Self.dateLine(context.date))
                        .sparcFont(12, weight: .semibold).tracking(1)
                        .foregroundStyle(SPARCTheme.tertiary)
                    Text(Self.greeting(context.date))
                        .sparcFont(30, weight: .bold).tracking(-0.9)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                }
            }
            Spacer(minLength: 0)
            Button(action: onSettings) {
                Text("HP")
                    .sparcFont(15, weight: .bold).tracking(0.3)
                    .dynamicTypeSize(...DynamicTypeSize.xxLarge)
                    .frame(width: 46, height: 46)
                    .background(LinearGradient(colors: [Color(white: 28 / 255), Color(white: 11 / 255)], startPoint: .topLeading, endPoint: .bottomTrailing), in: Circle())
                    .overlay(Circle().stroke(Color(white: 42 / 255)))
                    .frame(minWidth: 46, minHeight: 46)
                    .contentShape(Circle())
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityLabel("Settings")
            .accessibilityIdentifier("home.settings")
        }
    }

    private static let weekdayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.timeZone = SPARCFormat.zone
        formatter.dateFormat = "EEEE"
        return formatter
    }()

    private static func dateLine(_ date: Date) -> String {
        "\(weekdayFormatter.string(from: date)) · \(SPARCFormat.time(date))".uppercased()
    }

    private static func greeting(_ date: Date) -> String {
        let hour = ScheduleEngine.chicagoCalendar.component(.hour, from: date)
        if hour < 12 { return "Good morning." }
        if hour < 18 { return "Good afternoon." }
        return "Good evening."
    }

    // MARK: Pills

    private var notConnected: Bool { state == .offline && !store.isDemo }

    private var pillRow: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { connectionPill; statePill; Spacer(minLength: 0) }
            VStack(alignment: .leading, spacing: 4) { connectionPill; statePill }
        }
    }

    private var connectionPill: some View {
        let connected = !notConnected
        let overBluetooth = connected && !store.isDemo && provisioning.savedController?.bluetoothPeripheralID != nil
        return Button(action: onConnection) {
            HStack(spacing: 8) {
                SPARCStatusDot(color: connected ? SPARCTheme.spark : Color(white: 0.6), size: 8)
                Text(connected ? "Home controller" : "Not connected")
                if overBluetooth {
                    BluetoothRune()
                        .stroke(SPARCTheme.spark, style: StrokeStyle(lineWidth: 2.1, lineCap: .round, lineJoin: .round))
                        .frame(width: 15, height: 15)
                        .accessibilityHidden(true)
                }
            }
            .sparcFont(13, weight: .semibold)
            .foregroundStyle(connected ? SPARCTheme.sparkText : HomeTone.pillText)
            .padding(.leading, 11).padding(.trailing, 12)
            .frame(minHeight: 36)
            .background(connected ? SPARCTheme.spark.opacity(0.08) : HomeTone.pillFill, in: Capsule())
            .overlay(Capsule().stroke(connected ? SPARCTheme.spark.opacity(0.30) : HomeTone.heroLine))
            .padding(.vertical, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel(connected ? "Home controller, connected" : "Home controller, not connected")
        .accessibilityHint("Opens connection details")
        .accessibilityIdentifier("home.connection")
    }

    @ViewBuilder private var statePill: some View {
        if state != .offline {
            HStack(spacing: 7) {
                Image(systemName: "powerplug").font(.system(size: 14, weight: .semibold)).accessibilityHidden(true)
                Text(state == .unplugged ? "Unplugged" : "Plugged in")
            }
            .sparcFont(13, weight: .semibold)
            .foregroundStyle(HomeTone.pillText)
            .padding(.horizontal, 12)
            .frame(minHeight: 36)
            .background(HomeTone.pillFill, in: Capsule())
            .overlay(Capsule().stroke(HomeTone.heroLine))
            .padding(.vertical, 4)
            .accessibilityElement(children: .combine)
        }
    }

    // MARK: Hero

    private var hero: some View {
        VStack(alignment: .leading, spacing: 0) {
            heroHeader
            truckStage.padding(.horizontal, -18).padding(.top, 2)
            if store.showsTelemetry {
                SPARCBatteryBar(percent: snapshot.batteryPercent, target: snapshot.targetPercent,
                                showsTargetLabel: true, leadingCaption: rangeCaption)
            }
            statusRow.padding(.top, 16)
            controlRow
            chargeButton.padding(.top, 16)
            if state == .complete { readyCard.padding(.top, 12) }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(alignment: .topTrailing) {
            Circle()
                .fill(RadialGradient(colors: [heroGlowColor, heroGlowColor.opacity(0)], center: .center, startRadius: 0, endRadius: 147))
                .frame(width: 320, height: 320)
                .offset(x: 90, y: -110)
                .accessibilityHidden(true)
        }
        .background(SPARCTheme.heroGradient)
        .clipShape(RoundedRectangle(cornerRadius: 30, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 30, style: .continuous).stroke(HomeTone.heroLine, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Your \(store.displayVehicleName)")
    }

    private var heroGlowColor: Color {
        switch state {
        case .charging: return SPARCTheme.spark.opacity(0.32)
        case .complete: return SPARCTheme.spark.opacity(0.26)
        case .paused: return SPARCTheme.green.opacity(0.12)
        default: return SPARCTheme.green.opacity(0.18)
        }
    }

    @ViewBuilder private var heroHeader: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 10) { heroTitle; stateChip }
        } else {
            HStack(alignment: .top, spacing: 12) {
                heroTitle
                Spacer(minLength: 0)
                stateChip
            }
        }
    }

    private var heroTitle: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(store.displayVehicleName)
                .sparcFont(12, weight: .semibold).tracking(1).textCase(.uppercase)
                .foregroundStyle(SPARCTheme.tertiary)
            batteryNumber.padding(.top, 2)
            Text(targetLine)
                .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 5)
        }
    }

    @ViewBuilder private var batteryNumber: some View {
        if store.showsTelemetry {
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                Text("\(snapshot.batteryPercent)").sparcFont(68, weight: .bold).tracking(-3.4)
                Text("%").sparcFont(28, weight: .bold).tracking(-1.4).foregroundStyle(SPARCTheme.muted)
            }
            .monospacedDigit()
            .lineLimit(1).minimumScaleFactor(0.6)
            .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
            // The battery bar below reads the same value to VoiceOver.
            .accessibilityHidden(true)
        } else {
            Text("—").sparcFont(68, weight: .bold).foregroundStyle(SPARCTheme.muted)
                .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                .accessibilityLabel("Battery unavailable")
        }
    }

    private var targetLine: String {
        let target = store.displayedTarget
        let ready = store.displayedReadyTime
        guard target != "—" else { return "Waiting for your controller" }
        if ready == "—" || ready == "Not scheduled" { return "Target \(target)" }
        return "Target \(target) · ready by \(ready)"
    }

    private var stateChip: some View {
        switch state {
        case .waiting: return SPARCChip(title: "Waiting")
        case .charging: return SPARCChip(title: "Charging", live: true, dot: true)
        case .paused: return SPARCChip(title: "Paused")
        case .complete: return SPARCChip(title: "Ready", live: true)
        case .unplugged: return SPARCChip(title: "Unplugged")
        case .offline: return SPARCChip(title: "Offline")
        }
    }

    private var truckStage: some View {
        ControllerVehicleView(charging: isCharging)
            .frame(maxWidth: .infinity)
            .background {
                if isCharging {
                    // A still, soft glow under the truck while it charges.
                    GeometryReader { proxy in
                        let width = max(1, proxy.size.width * 0.78)
                        Circle()
                            .fill(RadialGradient(colors: [SPARCTheme.spark.opacity(0.55), SPARCTheme.spark.opacity(0)],
                                                 center: .center, startRadius: 0, endRadius: width / 2))
                            .frame(width: width, height: width)
                            .scaleEffect(x: 1, y: 36 / width)
                            .blur(radius: 6)
                            .position(x: proxy.size.width * 0.12 + width / 2, y: proxy.size.height - 34)
                    }
                    .accessibilityHidden(true)
                }
            }
    }

    private var rangeCaption: String? {
        if snapshot.energyKWh > 0.05 { return "+\(SPARCFormat.decimal(snapshot.energyKWh)) kWh this session" }
        if state == .unplugged || state == .offline { return nil }
        return "Plugged in at \(snapshot.batteryPercent)%"
    }

    // MARK: Status, controls, charge button

    private var statusRow: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: statusSymbol)
                .font(.system(size: 20, weight: state == .complete ? .heavy : .semibold))
                .foregroundStyle(statusIconColor)
                .frame(width: 44, height: 44)
                .background(statusTileColor, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: state)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(state.displayTitle)
                    .sparcFont(15.5, weight: .semibold).tracking(-0.15)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("home.chargeStatus")
                Text(statusSubtitle)
                    .sparcFont(13).monospacedDigit().foregroundStyle(SPARCTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
    }

    private var statusSymbol: String {
        switch state {
        case .waiting: return "moon"
        case .charging: return "bolt.fill"
        case .paused: return "pause.fill"
        case .complete: return "checkmark"
        case .unplugged: return "powerplug"
        case .offline: return "wifi.slash"
        }
    }
    private var statusIconColor: Color {
        switch state {
        case .charging: return SPARCTheme.spark
        case .complete: return SPARCTheme.onSpark
        case .paused: return HomeTone.pausedIcon
        default: return SPARCTheme.muted
        }
    }
    private var statusTileColor: Color {
        switch state {
        case .charging: return SPARCTheme.spark.opacity(0.14)
        case .complete: return SPARCTheme.spark
        default: return HomeTone.iconTile
        }
    }

    private var statusSubtitle: String {
        switch state {
        case .charging:
            return "\(SPARCFormat.decimal(snapshot.powerKW)) kW · \(SPARCFormat.duration(snapshot.remainingMinutes)) to \(snapshot.targetPercent)%"
        case .paused:
            return "0 kW · resume whenever you like"
        case .complete:
            if let goal = store.effectiveGoal { return "\(snapshot.batteryPercent)% · ready for your \(SPARCFormat.time(goal.departure)) drive" }
            return "Ready for your next departure"
        case .unplugged:
            return "Plug in at home to start charging"
        case .offline:
            return store.hasLiveTelemetry ? "Last update · \(SPARCFormat.time(snapshot.updatedAt))" : "Reconnect your controller"
        case .waiting:
            if store.effectiveGoal == nil { return "Your routine is paused. Charge now whenever you're ready." }
            if planStartsLater {
                var text = "Starts at \(SPARCFormat.time(snapshot.windowStart))"
                if (store.isDemo && store.isVirtualCarActive) {
                    let planned = VirtualCarState.exampleRate(at: snapshot.windowStart)
                    if planned < snapshot.currentRateCents { text += ", in the \(Self.cents(planned))¢ hours" }
                }
                return text
            }
            return "Your plan starts charging automatically"
        }
    }

    @ViewBuilder private var controlRow: some View {
        if state == .charging || state == .paused {
            HStack(spacing: 10) {
                if state == .charging {
                    SPARCActionButton(title: "Pause", symbol: "pause.fill", kind: .secondary, height: 50, busy: store.busy, action: pause)
                        .accessibilityIdentifier("home.pause")
                } else {
                    SPARCActionButton(title: store.isDemo ? "Resume" : "Resume plan", symbol: "play.fill", kind: .tinted, height: 50, busy: store.busy, action: resume)
                        .accessibilityIdentifier("home.resume")
                }
                Button(action: onCharge) {
                    HStack(spacing: 6) {
                        Text("Manage").sparcFont(16, weight: .semibold)
                        Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold)).accessibilityHidden(true)
                    }
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .foregroundStyle(.white)
                    .background(SPARCTheme.control, in: Capsule())
                    .overlay(Capsule().stroke(SPARCTheme.controlLine))
                    .contentShape(Capsule())
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityLabel("Manage charging")
                .accessibilityIdentifier("home.manage")
            }
            .padding(.top, 14)
        }
    }

    @ViewBuilder private var chargeButton: some View {
        switch state {
        case .waiting, .paused:
            Button { showChargeSheet = true } label: {
                HStack(spacing: 10) {
                    if store.busy { ProgressView().tint(SPARCTheme.onSpark) }
                    else { Image(systemName: "bolt.fill").font(.system(size: 18, weight: .semibold)).accessibilityHidden(true) }
                    Text("Charge now").sparcFont(17, weight: .bold).tracking(-0.17)
                }
                .frame(maxWidth: .infinity, minHeight: 58)
                .foregroundStyle(SPARCTheme.onSpark)
                .background(SPARCTheme.spark, in: Capsule())
                .overlay(Capsule().strokeBorder(LinearGradient(colors: [.white.opacity(0.45), .clear], startPoint: .top, endPoint: .center), lineWidth: 1))
                .shadow(color: SPARCTheme.spark.opacity(0.30), radius: 15, y: 10)
                .contentShape(Capsule())
            }
            .buttonStyle(SPARCPressStyle())
            .disabled(store.busy)
            .accessibilityLabel("Charge now")
            .accessibilityIdentifier("home.chargeNow")
        case .charging:
            chargeLink(chip: "Live · \(SPARCFormat.decimal(snapshot.powerKW)) kW", live: true, fill: snapshot.batteryPercent)
        case .complete:
            chargeLink(chip: "Done", live: true, fill: snapshot.batteryPercent)
        case .unplugged:
            chargeLink(chip: "Unplugged", live: false, fill: nil)
        case .offline:
            chargeLink(chip: "Offline", live: false, fill: nil)
        }
    }

    /// While a charge is live or finished, the big button leads to the Charge tab and shows how far along the battery is.
    private func chargeLink(chip: String, live: Bool, fill: Int?) -> some View {
        Button(action: onCharge) {
            HStack(spacing: 10) {
                Image(systemName: "bolt.fill").font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(live ? SPARCTheme.spark : SPARCTheme.muted)
                    .accessibilityHidden(true)
                Text("Charge now").sparcFont(17, weight: .bold).tracking(-0.17)
                Text(chip).sparcFont(12, weight: .bold).monospacedDigit()
                    .foregroundStyle(live ? SPARCTheme.spark : Color(white: 0.82))
                    .padding(.horizontal, 9).padding(.vertical, 4)
                    .background(live ? SPARCTheme.spark.opacity(0.18) : Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
                    .accessibilityHidden(true)
            }
            .frame(maxWidth: .infinity, minHeight: 58)
            .foregroundStyle(.white)
            .background {
                if let fill {
                    GeometryReader { proxy in
                        Rectangle().fill(SPARCTheme.spark.opacity(0.16))
                            .frame(width: proxy.size.width * CGFloat(min(100, max(0, fill))) / 100)
                            .animation(SPARCMotion.calm(SPARCMotion.glide, reduceMotion), value: fill)
                    }
                }
            }
            .background(HomeTone.ctaFill)
            .clipShape(Capsule())
            .overlay(Capsule().stroke(live ? SPARCTheme.spark.opacity(0.35) : SPARCTheme.controlLine))
            .contentShape(Capsule())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel("Charge now")
        .accessibilityValue(chip)
        .accessibilityHint("Opens the Charge tab")
        .accessibilityIdentifier("home.chargeNow")
    }

    private var readyCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark").font(.system(size: 16, weight: .heavy))
                .foregroundStyle(SPARCTheme.onSpark)
                .frame(width: 34, height: 34).background(SPARCTheme.spark, in: Circle())
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("Ready to go").sparcFont(15, weight: .semibold)
                Text(readyDetail).sparcFont(13).monospacedDigit().foregroundStyle(HomeTone.readyDetail)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SPARCTheme.spark.opacity(0.10), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(SPARCTheme.spark.opacity(0.32)))
        .accessibilityElement(children: .combine)
    }

    private var readyDetail: String {
        if snapshot.energyKWh > 0.05 { return "\(snapshot.batteryPercent)% · \(SPARCFormat.decimal(snapshot.energyKWh)) kWh added" }
        return "\(snapshot.batteryPercent)%"
    }

    // MARK: Streak

    private var streakCard: some View {
        let count = store.smartNightStreak
        let nights = store.recentNights(7)
        let tonightCharged = nights.last?.charged == true
        return HStack(alignment: .center, spacing: 14) {
            Image(systemName: "flame").font(.system(size: 19, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark)
                .frame(width: 44, height: 44)
                .background(SPARCTheme.spark.opacity(0.12), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(count) smart \(count == 1 ? "night" : "nights") in a row").sparcFont(15, weight: .semibold)
                Text(tonightCharged ? "Tonight's charge counts too" : "Let tonight's plan run to make it \(count + 1)")
                    .sparcFont(12.5).foregroundStyle(SPARCTheme.muted)
            }
            .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if !typeSize.isAccessibilitySize { nightDots(nights) }
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 24)
        .overlay(alignment: .bottomTrailing) {
            if typeSize.isAccessibilitySize { nightDots(nights).padding(16) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(count) smart \(count == 1 ? "night" : "nights") in a row")
        .accessibilityValue("\(nights.filter(\.charged).count) of the last 7 nights charged")
    }

    private func nightDots(_ nights: [SPARCNight]) -> some View {
        HStack(spacing: 5) {
            ForEach(nights) { night in
                VStack(spacing: 4) {
                    Circle()
                        .fill(night.charged ? SPARCTheme.spark : (night.isTonight ? .clear : Color(white: 0.17)))
                        .overlay(Circle().stroke(SPARCTheme.spark, lineWidth: night.isTonight && !night.charged ? 2 : 0))
                        .shadow(color: night.charged ? SPARCTheme.spark.opacity(0.55) : .clear, radius: 4)
                        .frame(width: 10, height: 10)
                    Text(night.letter).sparcFont(9.5, weight: .semibold).foregroundStyle(SPARCTheme.tertiary)
                }
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
        .accessibilityHidden(true)
    }

    // MARK: Savings and rate tiles

    private var tiles: some View {
        HStack(alignment: .top, spacing: 10) {
            savingsTile
                .overlay(alignment: .topTrailing) {
                    SPARCInfoButton("How savings are calculated") {
                        Text("Compared with charging right away at a fixed price.")
                    }
                    .padding(.top, 2).padding(.trailing, 2)
                }
            rateTile
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private var recentRecords: [ChargingRecord] { Array(store.records.sorted { $0.date < $1.date }.suffix(6)) }

    /// The controller's monthly figure when it reports one; otherwise the savings recorded this month.
    private var monthSavings: Double? {
        if snapshot.monthlySavings > 0 { return snapshot.monthlySavings }
        let calendar = ScheduleEngine.chicagoCalendar
        let thisMonth = store.records.filter { calendar.isDate($0.date, equalTo: .now, toGranularity: .month) }
        if !thisMonth.isEmpty { return thisMonth.reduce(0) { $0 + $1.savings } }
        return store.showsTelemetry ? 0 : nil
    }

    private var savingsTileValue: String {
        if let report = store.combinedSavings { return report.totals(savingsComparison)?.savedPercent.map(SPARCFormat.percent) ?? "—" }
        if store.isDemo { return monthSavings.map(SPARCFormat.money) ?? "—" }
        return store.historyWeek?.savingsUSD.map(SPARCFormat.money) ?? "—"
    }

    private var savingsTileIsNegative: Bool {
        if let report = store.combinedSavings { return (report.totals(savingsComparison)?.savedUSD ?? 0) < 0 }
        if store.isDemo { return false }
        return (store.historyWeek?.savingsUSD ?? 0) < 0
    }

    private var savingsTile: some View {
        let records = recentRecords
        return Button(action: onInsights) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 6) {
                    Image(systemName: "leaf").font(.system(size: 12, weight: .semibold)).accessibilityHidden(true)
                    Text("Saved")
                }
                .sparcFont(12, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                Text(savingsTileValue)
                    .sparcFont(31, weight: .bold).tracking(-1.24).monospacedDigit()
                    .foregroundStyle(savingsTileIsNegative ? SPARCTheme.warning : SPARCTheme.spark)
                    .lineLimit(1).minimumScaleFactor(0.6)
                    .padding(.top, 7)
                if store.isDemo, !records.isEmpty { savingsBars(records).padding(.top, 8) }
            }
            .padding(15)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .sparcSurface(radius: 24)
            .contentShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens Savings")
        .accessibilityIdentifier("home.savings")
    }

    private func savingsBars(_ records: [ChargingRecord]) -> some View {
        let values = records.map(\.savings)
        let low = values.min() ?? 0, high = values.max() ?? 0
        return HStack(alignment: .bottom, spacing: 4) {
            ForEach(Array(values.enumerated()), id: \.offset) { index, value in
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(index == values.count - 1 ? SPARCTheme.spark : SPARCTheme.spark.opacity(0.4))
                    .frame(height: high > low ? 8 + (value - low) / (high - low) * 20 : 18)
            }
        }
        .frame(height: 28, alignment: .bottom)
        .accessibilityHidden(true)
    }

    private var rateTile: some View {
        return RatePageButton {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Text("Rate now").sparcFont(12, weight: .semibold)
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                }.foregroundStyle(SPARCTheme.muted)
                rateValue.padding(.top, 7)
            }
            .padding(15)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .sparcSurface(radius: 24)
            .contentShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens rate breakdown and hourly history")
        .accessibilityIdentifier("home.rate")
    }

    @ViewBuilder private var rateValue: some View {
        if let rate = store.currentCombinedRate {
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                Text(Self.cents(rate)).sparcFont(31, weight: .bold).tracking(-1.24)
                Text("¢").sparcFont(17, weight: .bold).foregroundStyle(SPARCTheme.muted)
            }
            .monospacedDigit()
            .lineLimit(1).minimumScaleFactor(0.6)
        } else {
            Text("—").sparcFont(31, weight: .bold).foregroundStyle(SPARCTheme.muted)
        }
    }

    /// Hourly prices by Chicago hour. The app only knows them when the controller runs its built-in price model
    /// (the same one the Prices page charts); a real controller reports just the current rate.
    private var hourlyRates: [Double]? {
        guard (store.isDemo && store.isVirtualCarActive) else { return nil }
        let calendar = ScheduleEngine.chicagoCalendar
        let midnight = calendar.startOfDay(for: .now)
        return (0..<24).map { hour in
            VirtualCarState.exampleRate(at: calendar.date(bySettingHour: hour, minute: 0, second: 0, of: midnight) ?? midnight)
        }
    }

    private func rateBars(_ rates: [Double]) -> some View {
        let low = rates.min() ?? 0, high = rates.max() ?? 0
        let current = ScheduleEngine.chicagoCalendar.component(.hour, from: .now)
        return HStack(alignment: .bottom, spacing: 2) {
            ForEach(0..<24, id: \.self) { hour in
                let cheap = high > low && rates[hour] <= low
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(hour == current ? Color.white : (cheap ? SPARCTheme.spark : HomeTone.barOff))
                    .frame(height: high > low ? 9 + (rates[hour] - low) / (high - low) * 15 : 16)
                    .shadow(color: cheap ? SPARCTheme.spark.opacity(0.5) : .clear, radius: 3)
            }
        }
        .frame(height: 28, alignment: .bottom)
        .accessibilityHidden(true)
    }

    private static func cheapRunText(_ rates: [Double]) -> String? {
        guard let low = rates.min(), let high = rates.max(), high > low,
              let first = rates.firstIndex(where: { $0 <= low }) else { return nil }
        var last = first
        while last + 1 < rates.count, rates[last + 1] <= low { last += 1 }
        return "\(cents(low))¢ from \(hourLabel(first)) to \(hourLabel(last + 1))"
    }

    private static func hourLabel(_ hour: Int) -> String {
        let h = hour % 24
        return "\(h % 12 == 0 ? 12 : h % 12) \(h < 12 ? "AM" : "PM")"
    }

    /// Whole cents without a decimal, otherwise one decimal place.
    private static func cents(_ value: Double) -> String {
        SPARCFormat.cents(value)
    }

    // MARK: Tonight's plan

    private var hasPlanWindow: Bool {
        guard store.effectiveGoal != nil, snapshot.windowEnd > snapshot.windowStart else { return false }
        return store.showsTelemetry || (store.isControllerTest && store.weekly.mode == .fixed)
    }
    private var planStartsLater: Bool { hasPlanWindow && snapshot.windowStart > Date.now.addingTimeInterval(60) }

    /// What the plan will cost: the controller's estimate, or for the built-in price model the spent amount plus
    /// the cost of the energy still to come at the planned start.
    private var planCost: Double? {
        guard store.showsTelemetry else { return nil }
        guard snapshot.costAvailable != false else { return nil }
        guard (store.isDemo && store.isVirtualCarActive) else { return snapshot.estimatedCost }
        let start = state == .charging ? Date.now : max(Date.now, hasPlanWindow ? snapshot.windowStart : .now)
        return snapshot.estimatedCost + Self.modelCost(energyKWh: energyToTarget, from: start)
    }

    private var energyToTarget: Double {
        max(0, Double(snapshot.targetPercent - snapshot.batteryPercent)) / 100 * VirtualCarState.capacityKWh / VirtualCarState.efficiency
    }

    /// The price of delivering `energyKWh` at the car's charge power from `start`, hour by hour at the model's prices.
    private static func modelCost(energyKWh: Double, from start: Date) -> Double {
        let calendar = ScheduleEngine.chicagoCalendar
        var remaining = energyKWh
        var cursor = start
        var total = 0.0
        var steps = 0
        while remaining > 0.0001, steps < 120 {
            steps += 1
            let hourEnd = calendar.dateInterval(of: .hour, for: cursor)?.end ?? cursor.addingTimeInterval(3600)
            let supplied = min(remaining, VirtualCarState.powerKW * hourEnd.timeIntervalSince(cursor) / 3600)
            total += supplied * VirtualCarState.exampleRate(at: cursor) / 100
            remaining -= supplied
            cursor = hourEnd
        }
        return total
    }

    private var planCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "moon").font(.system(size: 16, weight: .semibold)).foregroundStyle(SPARCTheme.spark)
                        .accessibilityHidden(true)
                    Text("Tonight's plan").sparcFont(15.5, weight: .semibold)
                        .accessibilityAddTraits(.isHeader)
                }
                Spacer(minLength: 0)
                Button("View plan", action: onPlan)
                    .sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.spark)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("home.viewPlan")
            }
            planBody
        }
        .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 24)
    }

    @ViewBuilder private var planBody: some View {
        if !store.isDemo, let campaign = store.campaign {
            if let window = store.campaignWindow {
                Button(action: onSchedule) {
                    HStack {
                        Text(SPARCFormat.window(window.start, window.end)).sparcFont(22, weight: .bold)
                            .foregroundStyle(SPARCTheme.spark).minimumScaleFactor(0.6).lineLimit(1)
                        Spacer(minLength: 8)
                        Image(systemName: "chevron.right").foregroundStyle(SPARCTheme.muted)
                    }.frame(minHeight: 44)
                }.buttonStyle(SPARCPressStyle()).accessibilityIdentifier("home.campaignSchedule")
                Text("\(campaign.initialBatteryPercent)% → \(store.effectiveGoal?.targetPercent ?? campaign.targetBatteryPercent)% · \(campaign.nights) nights")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                if store.campaignIsStale {
                    Text("Saved schedule · reconnect to confirm")
                        .sparcFont(12).foregroundStyle(SPARCTheme.tertiary).padding(.top, 6)
                }
            } else {
                Text(campaign.enabled ? "Nightly run finished" : "Nightly schedule paused")
                    .sparcFont(22, weight: .bold)
                Button("Review nightly schedule", action: onSchedule).sparcFont(13).padding(.top, 8)
            }
        } else if store.effectiveGoal == nil {
            Text("Your routine is paused").sparcFont(22, weight: .bold).tracking(-0.44)
        } else if state == .complete {
            Text("Already at \(snapshot.targetPercent)%").sparcFont(22, weight: .bold).tracking(-0.44)
            planFooter(note: "Nothing left to charge")
        } else {
            scheduleButton
            if hasPlanWindow, let end = store.effectiveGoal?.departure, end > Date.now.addingTimeInterval(300) {
                HomePlanTimeline(now: .now,
                                 end: max(end, snapshot.windowEnd),
                                 window: snapshot.windowStart...snapshot.windowEnd,
                                 readyBy: store.effectiveGoal?.readyBy,
                                 cheap: cheapIntervals(from: .now, to: max(end, snapshot.windowEnd)))
                    .padding(.top, 12)
            }
            planFooter(note: nil)
        }
    }

    private var scheduleButton: some View {
        let text = hasPlanWindow ? SPARCFormat.window(snapshot.windowStart, snapshot.windowEnd) : "Awaiting vehicle data"
        return Button {
            traceUITest("Schedule button action")
            onSchedule()
        } label: {
            HStack(spacing: 8) {
                Text(text).sparcFont(22, weight: .bold).tracking(-0.44).monospacedDigit()
                    .foregroundStyle(hasPlanWindow ? SPARCTheme.spark : .white)
                    .lineLimit(1).minimumScaleFactor(0.7)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold)).foregroundStyle(SPARCTheme.tertiary)
                    .accessibilityHidden(true)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .disabled(store.busy)
        .accessibilityLabel("Current schedule")
        .accessibilityValue(text)
        .accessibilityHint("Opens your charging schedule")
        .accessibilityIdentifier("home.schedule")
    }

    private func planFooter(note: String?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(store.isDemo ? "Estimated cost" : "Supply cost so far").foregroundStyle(SPARCTheme.muted)
            Spacer(minLength: 8)
            Text(note ?? planCostText).fontWeight(.semibold).monospacedDigit().multilineTextAlignment(.trailing)
        }
        .sparcFont(13)
        .padding(.top, 12)
        .overlay(alignment: .top) { Rectangle().fill(Color(white: 30 / 255)).frame(height: 1) }
        .padding(.top, 12)
        .accessibilityElement(children: .combine)
    }

    private var planCostText: String {
        guard let cost = planCost else { return "—" }
        let money = SPARCFormat.money(cost)
        if !store.isDemo { return money }
        guard store.effectiveGoal != nil, store.displayedReadyTime != "—", store.displayedReadyTime != "Not scheduled" else { return money }
        return "\(money) · ready by \(store.displayedReadyTime)"
    }

    /// Stretches of the coming hours at the lowest price of the day, when the app knows hourly prices.
    private func cheapIntervals(from start: Date, to end: Date) -> [ClosedRange<Date>] {
        guard let rates = hourlyRates, let low = rates.min(), let high = rates.max(), high > low else { return [] }
        let calendar = ScheduleEngine.chicagoCalendar
        var result: [ClosedRange<Date>] = []
        var cursor = start
        var steps = 0
        while cursor < end, steps < 120 {
            steps += 1
            let hourEnd = calendar.dateInterval(of: .hour, for: cursor)?.end ?? cursor.addingTimeInterval(3600)
            let segmentEnd = min(hourEnd, end)
            if VirtualCarState.exampleRate(at: cursor) <= low {
                if let last = result.last, last.upperBound == cursor { result[result.count - 1] = last.lowerBound...segmentEnd }
                else { result.append(cursor...segmentEnd) }
            }
            cursor = hourEnd
        }
        return result
    }

    // MARK: Charge now

    private var chargeDetails: ChargeNowDetails {
        guard store.canControlCharging else {
            return ChargeNowDetails(unavailable: "Charging controls aren't available right now. Check your home controller connection.")
        }
        let rate = snapshot.currentRateCents
        let target = snapshot.targetPercent
        var sentence: String? = nil
        var rows: [ChargeNowDetails.Row] = []
        var waitTitle = "Not now"
        let later = planStartsLater
        if later { waitTitle = "Wait for \(SPARCFormat.time(snapshot.windowStart))" }

        if (store.isDemo && store.isVirtualCarActive) {
            // The built-in price model: charge-now and planned costs from the same hourly prices the car uses.
            let energy = energyToTarget
            let now = Self.modelCost(energyKWh: energy, from: .now)
            sentence = "Power is \(Self.cents(rate))¢ per kWh right now."
            if later {
                let planned = VirtualCarState.exampleRate(at: snapshot.windowStart)
                sentence! += planned < rate ? " Your plan waits for the \(Self.cents(planned))¢ hours at \(SPARCFormat.time(snapshot.windowStart))."
                                            : " Your plan starts at \(SPARCFormat.time(snapshot.windowStart))."
            }
            if energy > 0 {
                rows.append(.init(title: "Charge now to \(target)%", value: SPARCFormat.money(now), saves: nil, highlighted: false))
                if later {
                    let plan = Self.modelCost(energyKWh: energy, from: snapshot.windowStart)
                    rows.append(.init(title: waitTitle, value: SPARCFormat.money(plan),
                                      saves: now - plan > 0.005 ? "saves \(SPARCFormat.money(now - plan))" : nil, highlighted: true))
                }
            }
        } else {
            if store.showsTelemetry, snapshot.rateAvailable != false { sentence = "ComEd supply is \(Self.cents(rate))¢ per kWh right now." }
            if store.isDemo, later, snapshot.estimatedCost > 0 {
                rows.append(.init(title: waitTitle, value: SPARCFormat.money(snapshot.estimatedCost), saves: nil, highlighted: true))
            }
        }
        return ChargeNowDetails(sentence: sentence, rows: rows, waitTitle: waitTitle)
    }

    private func startCharging() {
        showChargeSheet = false
        Task {
            await store.chargeNow()
            if store.errorMessage == nil { toast("Charging started") }
        }
    }

    private func pause() {
        Task {
            await store.pauseCharging()
            if store.errorMessage == nil { toast("Paused · 0 kW") }
        }
    }

    private func resume() {
        Task {
            await store.resumeCharging()
            if store.errorMessage == nil { toast(store.snapshot.state == .charging ? "Charging resumed" : "Smart plan resumed") }
        }
    }
}

// MARK: - Pieces

/// The Bluetooth rune, drawn on the 24-unit grid the design uses.
private struct BluetoothRune: Shape {
    func path(in rect: CGRect) -> Path {
        let scale = min(rect.width, rect.height) / 24
        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + x * scale, y: rect.minY + y * scale) }
        var path = Path()
        path.move(to: point(7, 7))
        path.addLine(to: point(17, 17))
        path.addLine(to: point(12, 21))
        path.addLine(to: point(12, 3))
        path.addLine(to: point(17, 7))
        path.addLine(to: point(7, 17))
        return path
    }
}

/// A bar from now until departure: the charging window, the cheapest hours behind it, and when the car is ready.
private struct HomePlanTimeline: View {
    let now: Date
    let end: Date
    let window: ClosedRange<Date>
    let readyBy: Date?
    let cheap: [ClosedRange<Date>]
    @Environment(\.dynamicTypeSize) private var typeSize

    private func fraction(_ date: Date) -> CGFloat {
        let span = end.timeIntervalSince(now)
        guard span > 0 else { return 0 }
        return CGFloat(min(1, max(0, date.timeIntervalSince(now) / span)))
    }

    private struct AxisLabel: Identifiable {
        let id: String
        let text: String
        let fraction: CGFloat
    }

    private var labels: [AxisLabel] {
        var items = [AxisLabel(id: "now", text: "Now", fraction: 0)]
        if fraction(window.lowerBound) > 0.02 { items.append(AxisLabel(id: "start", text: SPARCFormat.time(window.lowerBound), fraction: fraction(window.lowerBound))) }
        if fraction(window.upperBound) < 0.98 { items.append(AxisLabel(id: "stop", text: SPARCFormat.time(window.upperBound), fraction: fraction(window.upperBound))) }
        items.append(AxisLabel(id: "leave", text: SPARCFormat.time(end), fraction: 1))
        // Drop any label that would sit on top of its neighbour.
        let spacing: CGFloat = typeSize.isAccessibilitySize ? 0.4 : 0.2
        var kept: [AxisLabel] = []
        for item in items.sorted(by: { $0.fraction < $1.fraction }) {
            if let last = kept.last, item.fraction - last.fraction < spacing { continue }
            kept.append(item)
        }
        return kept
    }

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    Capsule().fill(Color(white: 27 / 255)).frame(width: width, height: 8).offset(y: 11)
                    ForEach(Array(cheap.enumerated()), id: \.offset) { _, interval in
                        Rectangle().fill(SPARCTheme.spark.opacity(0.12))
                            .frame(width: max(0, (fraction(interval.upperBound) - fraction(interval.lowerBound)) * width), height: 8)
                            .offset(x: fraction(interval.lowerBound) * width, y: 11)
                    }
                    Capsule().fill(SPARCTheme.spark)
                        .frame(width: max(12, (fraction(window.upperBound) - fraction(window.lowerBound)) * width), height: 12)
                        .shadow(color: SPARCTheme.spark.opacity(0.5), radius: 7)
                        .offset(x: min(fraction(window.lowerBound) * width, width - 12), y: 9)
                    RoundedRectangle(cornerRadius: 1).fill(.white).frame(width: 2, height: 22).offset(y: 4)
                    if let readyBy {
                        RoundedRectangle(cornerRadius: 1).fill(SPARCTheme.tertiary).frame(width: 2, height: 22)
                            .offset(x: max(0, min(width - 2, fraction(readyBy) * width - 1)), y: 4)
                    }
                }
            }
            .frame(height: 30)
            GeometryReader { proxy in
                ZStack(alignment: .topLeading) {
                    ForEach(labels) { item in
                        Text(item.text).sparcFont(11).foregroundStyle(SPARCTheme.tertiary)
                            .fixedSize()
                            .alignmentGuide(.leading) { $0.width * item.fraction }
                            .offset(x: item.fraction * proxy.size.width)
                    }
                }
            }
            .frame(height: 15)
        }
        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
        .accessibilityHidden(true)
    }
}

private struct ChargeNowDetails {
    struct Row {
        let title: String
        let value: String
        let saves: String?
        let highlighted: Bool
    }
    var unavailable: String? = nil
    var sentence: String? = nil
    var rows: [Row] = []
    var waitTitle = "Not now"
}

/// The "Charge now?" bottom sheet: what it costs to charge right away against waiting for the plan.
private struct ChargeNowSheet: View {
    let details: ChargeNowDetails
    let busy: Bool
    let onCharge: () -> Void
    let onWait: () -> Void
    @State private var contentHeight: CGFloat = 470

    var body: some View {
        ScrollView {
            content
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollIndicators(.hidden)
        .presentationDetents([.height(contentHeight + 34)])
        .presentationDragIndicator(.visible)
        .presentationBackground(HomeTone.sheetFill)
        .presentationCornerRadius(38)
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            Image(systemName: "bolt.fill").font(.system(size: 26, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark)
                .frame(width: 56, height: 56)
                .background(SPARCTheme.spark.opacity(0.14), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .accessibilityHidden(true)
            Text("Charge now?").sparcFont(25, weight: .bold).tracking(-0.5)
                .accessibilityAddTraits(.isHeader)
                .padding(.top, 14)
            if let reason = details.unavailable {
                Text(reason).sparcFont(15).lineSpacing(3).foregroundStyle(Color(white: 181 / 255))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 8)
            } else {
                if let sentence = details.sentence {
                    Text(sentence).sparcFont(15).lineSpacing(3).foregroundStyle(Color(white: 181 / 255))
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 8)
                }
                if !details.rows.isEmpty { comparison.padding(.top, 16) }
            }
            SPARCActionButton(title: "Charge now", kind: .primary, height: 56, busy: busy, action: onCharge)
                .opacity(details.unavailable == nil ? 1 : 0.4)
                .disabled(details.unavailable != nil)
                .accessibilityIdentifier("home.chargeNow.confirm")
                .padding(.top, 16)
            SPARCActionButton(title: details.waitTitle, kind: .plain, height: 50, action: onWait)
                .accessibilityIdentifier("home.chargeNow.wait")
                .padding(.top, 8)
        }
        .padding(.horizontal, 20).padding(.top, 30).padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var comparison: some View {
        VStack(spacing: 0) {
            ForEach(Array(details.rows.enumerated()), id: \.offset) { index, row in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(row.title).sparcFont(14.5).foregroundStyle(Color(white: 201 / 255))
                    Spacer(minLength: 8)
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(row.value).sparcFont(17, weight: .bold).monospacedDigit()
                            .foregroundStyle(row.highlighted ? SPARCTheme.spark : .white)
                        if let saves = row.saves {
                            Text(saves).sparcFont(12.5, weight: .semibold).foregroundStyle(SPARCTheme.mint)
                        }
                    }
                }
                .padding(.horizontal, 16).padding(.vertical, 13)
                .accessibilityElement(children: .combine)
                if index < details.rows.count - 1 {
                    Rectangle().fill(Color(white: 30 / 255)).frame(height: 1)
                }
            }
        }
        .background(Color(white: 10 / 255), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Color(white: 35 / 255)))
    }
}

private func traceUITest(_ event: String) {
    #if DEBUG
    if ProcessInfo.processInfo.arguments.contains("--uitesting") {
        print("SPARC UI TRACE: \(event)")
    }
    #endif
}

extension ChargeState {
    var displayTitle: String {
        switch self {
        case .waiting: return "Waiting for a better charging time"
        case .charging: return "Charging with good energy"
        case .paused: return "Charging paused"
        case .complete: return "Your charge is complete"
        case .unplugged: return "Cable unplugged"
        case .offline: return "Live charging unavailable"
        }
    }
    var symbol: String {
        switch self {
        case .waiting: return "moon"
        case .charging: return "bolt"
        case .paused: return "pause.circle"
        case .complete: return "checkmark.circle"
        case .unplugged: return "cable.connector"
        case .offline: return "wifi.slash"
        }
    }
}
