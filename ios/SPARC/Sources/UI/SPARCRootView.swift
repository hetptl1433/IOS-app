import SwiftUI

struct SPARCRootView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var selectedTab: SPARCTab = .home
    @State private var sheet: HomeSheet?
    @State private var confirmCharge = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            Group {
                switch selectedTab {
                case .home:
                    HomeView(onSettings: { sheet = .settings }, onSchedule: {
                        traceUITest("Root received schedule request")
                        sheet = .schedule
                    }, onCharge: openCharge, onSavings: { selectedTab = .insights }, onPrices: { sheet = .prices }, onPlan: { selectedTab = .plan })
                case .charge: ChargingView()
                case .plan: PlanView()
                case .insights: InsightsView()
                case .records: RecordsView()
                }
            }
            .transition(.opacity)
        }
        .foregroundStyle(.white)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: 0) {
                if store.isDemo {
                    Text(store.isVirtualCarActive ? "DEMO · VIRTUAL CAR" : "DEMO MODE")
                        .sparcFont(8, weight: .medium).tracking(1.2)
                        .foregroundStyle(SPARCTheme.muted).padding(.top, 5)
                        .accessibilityLabel(store.isVirtualCarActive ? "Demo mode. Virtual car and example prices." : "Demo mode. Virtual car is off.")
                } else if store.isVirtualCarActive {
                    Text("PI CONNECTED · VIRTUAL CAR")
                        .sparcFont(8, weight: .medium).tracking(1.2)
                        .foregroundStyle(SPARCTheme.muted).padding(.top, 5)
                        .accessibilityIdentifier("controller.virtualCar")
                } else if store.isControllerTest {
                    Text("APP TEST · NO VEHICLE CONNECTED")
                        .sparcFont(8, weight: .medium).tracking(1.2)
                        .foregroundStyle(SPARCTheme.muted).padding(.top, 5)
                        .accessibilityIdentifier("controller.testMode")
                }
                tabBar
            }.background(.black)
        }
        .sheet(item: $sheet) { value in
            NavigationStack {
                Group {
                    switch value {
                    case .settings: SettingsView()
                    case .schedule: CurrentScheduleView()
                    case .prices: PricesView()
                    }
                }
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) { Button("Done") { sheet = nil }.foregroundStyle(.white).accessibilityIdentifier("sheet.done") }
                }
                .toolbarBackground(.black, for: .navigationBar)
            }
            .preferredColorScheme(.dark)
            .tint(.white)
            .onAppear { traceUITest("Sheet appeared: \(value.rawValue)") }
        }
        .confirmationDialog("Start charging now?", isPresented: $confirmCharge, titleVisibility: .visible) {
            Button("Charge now") { Task { await store.chargeNow() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(store.isVirtualCarActive ? "Start charging your virtual car. Your weekly routine stays saved." : store.isDemo ? "Enable Virtual car in Settings to try charging." : store.isControllerTest ? "Start the charging test on your controller. No vehicle is connected, and your weekly routine stays saved." : "The controller will request an immediate charge. Your weekly routine stays saved.")
        }
        .alert("Couldn't complete that", isPresented: Binding(get: { store.errorMessage != nil && sheet == nil && (selectedTab == .home || selectedTab == .charge) }, set: { if !$0 { store.errorMessage = nil } })) {
            Button("OK") { store.errorMessage = nil }
        } message: { Text(store.errorMessage ?? "Please try again.") }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.2), value: selectedTab)
        .preferredColorScheme(.dark)
        .tint(.white)
    }

    private var tabBar: some View {
        HStack(spacing: 3) {
            ForEach(SPARCTab.allCases) { tab in
                Button {
                    selectedTab = tab
                } label: {
                    VStack(spacing: 5) {
                        Image(systemName: tab.symbol).font(.system(size: 19, weight: .regular))
                            .foregroundStyle(selectedTab == tab ? SPARCTheme.green : Color(white: 0.64))
                        Text(tab.rawValue).font(.system(size: 10, weight: selectedTab == tab ? .semibold : .regular))
                            .foregroundStyle(selectedTab == tab ? .white : SPARCTheme.muted)
                    }
                    .frame(maxWidth: .infinity, minHeight: 51)
                    .background(selectedTab == tab ? Color(white: 0.085) : .clear, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .contentShape(Rectangle())
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityLabel(tab.rawValue)
                .accessibilityAddTraits(selectedTab == tab ? .isSelected : [])
                .accessibilityIdentifier("tab.\(tab.rawValue.lowercased())")
            }
        }
        .padding(6)
        .background(Color(white: 0.015), in: RoundedRectangle(cornerRadius: 23, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 23, style: .continuous).stroke(SPARCTheme.line))
        .padding(.horizontal, 10)
        .padding(.top, 6)
        .background(.black)
    }

    private func openCharge() {
        if [.charging, .complete, .unplugged, .offline].contains(store.snapshot.state) { selectedTab = .charge }
        else { confirmCharge = true }
    }
}

private enum SPARCTab: String, CaseIterable, Identifiable {
    case home = "Home", charge = "Charge", plan = "Plan", insights = "Insights", records = "Records"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .home: return "house"
        case .charge: return "bolt"
        case .plan: return "calendar"
        case .insights: return "chart.xyaxis.line"
        case .records: return "clock"
        }
    }
}

private enum HomeSheet: String, Identifiable {
    case settings, schedule, prices
    var id: String { rawValue }
}

private struct HomeView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dynamicTypeSize) private var typeSize
    var onSettings: () -> Void
    var onSchedule: () -> Void
    var onCharge: () -> Void
    var onSavings: () -> Void
    var onPrices: () -> Void
    var onPlan: () -> Void

    var body: some View {
        SPARCPage {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(greeting).sparcFont(12).foregroundStyle(SPARCTheme.muted)
                    Text("A little smarter.\nA lot more saved.")
                        .sparcFont(28, weight: .semibold).tracking(-1)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                }
                Spacer(minLength: 0)
                Button(action: onSettings) {
                    Image(systemName: "person.crop.circle")
                        .sparcFont(22, weight: .light).foregroundStyle(SPARCTheme.green)
                        .frame(width: 46, height: 46)
                        .background(Color(white: 0.035), in: Circle())
                        .overlay(Circle().stroke(SPARCTheme.line))
                        .contentShape(Circle())
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityLabel("Settings")
                .accessibilityIdentifier("home.settings")
            }
            .padding(.bottom, 4)

            if store.isDemo {
                HStack(spacing: 6) {
                    Image(systemName: "sparkles").foregroundStyle(SPARCTheme.green)
                    Text(store.isVirtualCarActive ? "Demo mode · virtual car" : "Demo mode · virtual car off").foregroundStyle(SPARCTheme.muted).accessibilityIdentifier("demo.banner")
                    Spacer()
                    Button("Connect Pi", action: onSettings).foregroundStyle(.white)
                }
                .sparcFont(11)
            } else if store.isVirtualCarActive {
                SPARCNotice(title: "Virtual car · plugged in. Controls and schedules run on your Pi over Bluetooth.", symbol: "car.side")
            } else if store.isControllerTest {
                SPARCNotice(title: "App test mode · no vehicle connected.", symbol: "bolt.badge.clock")
            }

            if store.snapshot.state == .offline && !store.isDemo {
                SPARCNotice(title: store.connectionMessage ?? (store.controllerName == nil ? "Connect your Raspberry Pi from Settings to set up your home controller." : "Live vehicle data is unavailable. Check your home network and the vehicle connection on your controller."), symbol: "wifi.slash")
            }
            if let today = store.today {
                SPARCNotice(title: "Just this departure · \(today.targetPercent)% by \(SPARCFormat.time(today.departure)). Your weekly routine stays saved.", symbol: "calendar.badge.clock")
            }

            SPARCCard(glossy: true) {
                VStack(spacing: 16) {
                    Group {
                        if typeSize.isAccessibilitySize {
                            VStack(alignment: .leading, spacing: 12) { vehicleTitle; vehicleTools }
                                .frame(maxWidth: .infinity, alignment: .leading)
                        } else {
                            HStack(spacing: 10) { vehicleTitle; Spacer(minLength: 0); vehicleTools }
                        }
                    }
                    Group {
                        if typeSize.isAccessibilitySize {
                            VStack(spacing: 4) { battery; truck }
                        } else {
                            HStack(spacing: 12) { battery; truck }
                        }
                    }
                    .padding(.vertical, 2)
                    HStack(spacing: 12) {
                        homeMetric("Your target", store.displayedTarget)
                        Rectangle().fill(SPARCTheme.line).frame(width: 1, height: 34)
                        homeMetric("Ready by", store.displayedReadyTime)
                    }
                    Rectangle().fill(Color(white: 0.22)).frame(height: 1)
                    HStack(alignment: .center, spacing: 12) {
                        VStack(alignment: .leading, spacing: 7) {
                            Text(store.snapshot.state.displayTitle)
                                .sparcFont(13, weight: .semibold)
                                .fixedSize(horizontal: false, vertical: true)
                                .accessibilityIdentifier("home.chargeStatus")
                            Text(statusSubtitle)
                                .sparcFont(12).foregroundStyle(SPARCTheme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                        Image(systemName: store.snapshot.state.symbol).foregroundStyle(SPARCTheme.green)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    SPARCButton(title: "Charge now", symbol: "bolt", busy: store.busy, action: onCharge)
                        .accessibilityIdentifier("home.chargeNow")
                }
            }

            HStack(alignment: .top, spacing: 12) {
                summaryCard(label: store.isVirtualCarActive ? "Virtual savings" : "Saved this month", value: store.showsTelemetry ? SPARCFormat.money(store.isVirtualCarActive ? store.snapshot.sessionSavings : store.snapshot.monthlySavings) : "—", detail: "View savings", symbol: "leaf", action: onSavings)
                summaryCard(label: store.isVirtualCarActive ? "Example rate" : "Current total rate", value: store.showsTelemetry ? "\(SPARCFormat.decimal(store.snapshot.currentRateCents, digits: 0))¢" : "—", detail: "See prices", symbol: nil, action: onPrices)
            }
            HStack {
                Text("Your overnight plan").sparcFont(16, weight: .semibold)
                Spacer()
                Button("View plan", action: onPlan).sparcFont(12).foregroundStyle(.white)
            }
            .padding(.top, 2)
            SPARCCard {
                VStack(alignment: .leading, spacing: 11) {
                    HStack {
                        Image(systemName: "moon.stars").foregroundStyle(SPARCTheme.green)
                        Text(store.effectiveGoal != nil ? "A quieter night. A smarter charge." : "Your routine is paused.")
                            .sparcFont(14, weight: .medium)
                    }
                    Text(store.effectiveGoal != nil ? store.displayedWindow : "Charge now remains available.")
                        .sparcFont(21, weight: .semibold).foregroundStyle(SPARCTheme.green)
                    SPARCDetail(title: "Estimated charging cost", value: store.showsTelemetry ? SPARCFormat.money(store.snapshot.estimatedCost) : "—")
                }
            }
            Text(store.isVirtualCarActive ? "Virtual battery and energy · example prices. Charging increases the battery over time; pause stops it. Times shown in Chicago time." : store.isDemo ? "Turn on Virtual car in Settings to explore charging." : store.isControllerTest ? "Virtual car is off. Turn it on in Settings to try a plugged-in car at 40%." : "Times shown in Chicago time. Charging preferences save on your Raspberry Pi.")
                .sparcFont(11).foregroundStyle(SPARCTheme.muted)
        }
        .refreshable { await store.refresh() }
    }

    private var vehicleTitle: some View {
        Text(store.snapshot.vehicleName).sparcFont(16, weight: .semibold)
            .lineLimit(1).minimumScaleFactor(0.8)
    }
    private var vehicleTools: some View {
        HStack(spacing: 10) {
            Button {
                traceUITest("Calendar button action")
                onSchedule()
            } label: {
                Image(systemName: "calendar.badge.clock")
                    .font(.system(size: 15)).foregroundStyle(SPARCTheme.green)
                    .frame(width: 44, height: 44)
                    .background(Color(white: 0.045), in: RoundedRectangle(cornerRadius: 13))
                    .overlay(RoundedRectangle(cornerRadius: 13).stroke(Color(white: 0.19)))
                    .contentShape(Rectangle())
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityLabel("Current schedule")
            .accessibilityIdentifier("home.schedule")
            .disabled(store.busy)
            SPARCPill(title: store.isVirtualCarActive ? "Plugged in" : "Home", symbol: "cable.connector")
        }
    }
    @ViewBuilder private var battery: some View {
        if store.showsTelemetry { BatteryRing(percent: store.snapshot.batteryPercent) }
        else { UnavailableBatteryRing() }
    }
    private var truck: some View {
        CybertruckView(charging: store.snapshot.state == .charging)
            .frame(maxWidth: .infinity)
            .frame(maxHeight: 132)
    }
    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: .now)
        if hour < 12 { return "Good morning." }
        if hour < 18 { return "Good afternoon." }
        return "Good evening."
    }
    private var statusSubtitle: String {
        if store.isControllerTest && !store.isVirtualCarActive {
            switch store.snapshot.state {
            case .charging: return "Your Pi confirmed the charging test"
            case .paused: return "Test paused · your weekly routine stays saved"
            case .complete: return "The scheduled test window has finished"
            case .waiting: return store.weekly.mode == .fixed ? "Test window · \(store.displayedWindow)" : "Charge now to test, or choose a fixed start time"
            case .unplugged: return "No vehicle is connected"
            case .offline: return "Reconnect your controller"
            }
        }
        switch store.snapshot.state {
        case .charging: return "\(SPARCFormat.decimal(store.snapshot.powerKW)) kW · \(SPARCFormat.duration(store.snapshot.remainingMinutes)) remaining"
        case .paused: return "Your weekly routine stays saved"
        case .complete: return "Ready for your next departure"
        case .unplugged: return "Plug in at home to start charging"
        case .offline: return store.hasLiveTelemetry ? "Last update · \(SPARCFormat.time(store.snapshot.updatedAt))" : "Reconnect your controller"
        case .waiting: return store.effectiveGoal == nil ? "Your routine is paused. Charge now whenever you're ready." : "Next window · \(store.displayedWindow)"
        }
    }
    private func homeMetric(_ title: String, _ value: String) -> some View {
        VStack(spacing: 5) {
            Text(title).sparcFont(11).foregroundStyle(SPARCTheme.muted)
            Text(value).sparcFont(20, weight: .semibold).tracking(-0.5).foregroundStyle(SPARCTheme.green)
                .lineLimit(1).minimumScaleFactor(0.7)
        }.frame(maxWidth: .infinity)
    }
    private func summaryCard(label: String, value: String, detail: String, symbol: String?, action: @escaping () -> Void) -> some View {
        SPARCCard(padding: 15) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 4) {
                    if let symbol { Image(systemName: symbol) }
                    Text(label)
                }.sparcFont(11).foregroundStyle(SPARCTheme.muted)
                Text(value).sparcFont(31, weight: .semibold).tracking(-1.1).foregroundStyle(SPARCTheme.green)
                    .lineLimit(1).minimumScaleFactor(0.65)
                if symbol == nil { Text("per kWh · estimated").sparcFont(10).foregroundStyle(SPARCTheme.muted) }
                Button(action: action) {
                    HStack(spacing: 4) { Text(detail); Image(systemName: "arrow.up.right").foregroundStyle(SPARCTheme.green) }
                        .sparcFont(11, weight: .medium)
                        .padding(.top, 5)
                }.foregroundStyle(.white)
            }
            .frame(maxWidth: .infinity, minHeight: 105, alignment: .topLeading)
        }
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
