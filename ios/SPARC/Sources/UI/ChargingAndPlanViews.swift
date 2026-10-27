import SwiftUI
import Charts

struct ChargingView: View {
    @EnvironmentObject private var store: AppStore
    @State private var confirmEnd = false
    @State private var confirmStart = false

    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Your charge.", subtitle: store.isVirtualCarActive ? (store.isDemo ? "Virtual car · on this iPhone" : "Virtual car · on your Raspberry Pi") : store.isDemo ? "Demo mode · virtual car off" : store.isControllerTest ? "App test mode · virtual car off" : "Your home Raspberry Pi")
            if !store.isDemo && store.snapshot.state == .offline {
                SPARCNotice(title: store.connectionMessage ?? "Connect your home controller in Settings to view live charging.", symbol: "wifi.slash")
            }
            SPARCCard(glossy: true) {
                VStack(spacing: 20) {
                    HStack {
                        Text(store.snapshot.vehicleName).font(.headline)
                        Spacer()
                        SPARCPill(title: store.snapshot.state.shortTitle, symbol: store.snapshot.state.symbol)
                    }
                    HStack(spacing: 12) {
                        Group { if store.showsTelemetry { BatteryRing(percent: store.snapshot.batteryPercent, size: 128) } else { UnavailableBatteryRing(size: 128) } }
                        CybertruckView(charging: store.snapshot.state == .charging).frame(maxWidth: .infinity)
                    }
                    Text(store.snapshot.state.displayTitle)
                        .sparcFont(14).foregroundStyle(SPARCTheme.muted).multilineTextAlignment(.center)
                    HStack(alignment: .top, spacing: 4) {
                        chargeStat(store.showsTelemetry ? SPARCFormat.decimal(store.snapshot.powerKW) : "—", "Power · kW")
                        chargeStat(store.showsTelemetry ? SPARCFormat.decimal(store.snapshot.energyKWh) : "—", "Energy · kWh")
                        chargeStat(store.showsTelemetry ? SPARCFormat.duration(store.snapshot.remainingMinutes) : "—", "To target")
                    }
                }
            }
            SPARCCard {
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text("Your charge limit").sparcFont(14)
                        Spacer()
                        Text(store.displayedTarget).sparcFont(27, weight: .semibold).foregroundStyle(SPARCTheme.green)
                    }
                    SPARCDetail(title: "Ready by", value: store.displayedReadyTime)
                    Divider().overlay(SPARCTheme.line)
                    SPARCDetail(title: "Estimated session cost", value: store.showsTelemetry ? SPARCFormat.money(store.snapshot.estimatedCost) : "—")
                }
            }
            Group {
                if store.snapshot.state == .charging {
                    SPARCButton(title: "Pause charging", symbol: "pause", busy: store.busy) { Task { await store.pauseCharging() } }
                        .accessibilityIdentifier("charge.pause")
                    SPARCButton(title: "End charging session", symbol: "stop") { confirmEnd = true }
                        .disabled(store.busy)
                        .accessibilityIdentifier("charge.end")
                } else if store.snapshot.state == .paused {
                    SPARCButton(title: "Resume charging", symbol: "play", busy: store.busy) { Task { await store.resumeCharging() } }
                        .accessibilityIdentifier("charge.resume")
                    SPARCButton(title: "End charging session", symbol: "stop") { confirmEnd = true }
                        .disabled(store.busy)
                } else {
                    SPARCButton(title: "Charge now", symbol: "bolt", busy: store.busy) { confirmStart = true }
                        .accessibilityIdentifier("charge.start")
                }
            }
            if store.snapshot.state == .complete {
                SPARCNotice(title: store.isVirtualCarActive ? "Your virtual car has reached its \(store.snapshot.targetPercent)% target." : store.isControllerTest ? "The scheduled charging test has finished. No vehicle was charged." : "\(store.snapshot.targetPercent)% and ready to go. Every little bit of smarter charging adds up.", symbol: "checkmark.circle")
            } else if store.snapshot.state == .unplugged {
                SPARCNotice(title: "Connect your charging cable before requesting a charge.", symbol: "cable.connector")
            }
            Text(store.isVirtualCarActive ? "Virtual readings · 11.5 kW while charging, 0 kW while paused or waiting. Battery and energy increase with elapsed charging time. Costs use example prices." : store.isControllerTest || store.isDemo ? "Virtual car is off. Enable it in Settings to try charging from 40%." : "In-progress costs are estimates. Your weekly routine stays saved.")
                .sparcFont(11).foregroundStyle(SPARCTheme.muted)
        }
        .refreshable { await store.refresh() }
        .confirmationDialog("End this charging session?", isPresented: $confirmEnd, titleVisibility: .visible) {
            Button("End session", role: .destructive) { Task { await store.endCharging() } }
            Button("Keep charging", role: .cancel) {}
        } message: { Text("Your weekly schedule stays saved for the next departure.") }
        .confirmationDialog("Charge now?", isPresented: $confirmStart, titleVisibility: .visible) {
            Button("Start now") { Task { await store.chargeNow() } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func chargeStat(_ value: String, _ label: String) -> some View {
        VStack(spacing: 7) {
            Text(value).sparcFont(22, weight: .semibold).foregroundStyle(SPARCTheme.green)
                .lineLimit(1).minimumScaleFactor(0.7)
            Text(label).sparcFont(10).foregroundStyle(SPARCTheme.muted)
        }.frame(maxWidth: .infinity)
    }
}

struct CurrentScheduleView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Current schedule.", subtitle: "A little planning. A calmer morning.")
            if let error = store.errorMessage { SPARCNotice(title: error, symbol: "exclamationmark.circle") }
            SPARCCard(glossy: true) {
                VStack(alignment: .leading, spacing: 14) {
                    Text(store.today == nil ? "YOUR WEEKLY ROUTINE" : "JUST THIS DEPARTURE")
                        .sparcFont(10, weight: .medium).tracking(1.4).foregroundStyle(SPARCTheme.muted)
                    Text(store.displayedWindow)
                        .sparcFont(25, weight: .semibold).foregroundStyle(SPARCTheme.green)
                        .minimumScaleFactor(0.7)
                    Text("Estimated charging window").sparcFont(12).foregroundStyle(SPARCTheme.muted)
                    HStack {
                        scheduleMetric("Target battery", store.displayedTarget)
                        Divider().overlay(SPARCTheme.line)
                        scheduleMetric("Ready by", store.displayedReadyTime)
                    }.fixedSize(horizontal: false, vertical: true)
                    Divider().overlay(SPARCTheme.line)
                    SPARCDetail(title: "Time zone", value: "Chicago")
                }
            }
            NavigationLink(destination: TodayScheduleEditor()) {
                SPARCNavigationButton(title: "Update just today", subtitle: "One departure. Your routine stays saved.", symbol: "calendar.badge.clock")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("schedule.today")
            NavigationLink(destination: WeeklyScheduleEditor()) {
                SPARCNavigationButton(title: "Change weekly schedule", subtitle: "Update your recurring charging routine.", symbol: "calendar")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("schedule.weekly")
            if store.today != nil {
                Button("Remove today's update") { Task { await store.clearToday() } }
                    .disabled(store.busy)
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                    .frame(maxWidth: .infinity).padding(.vertical, 10)
                    .accessibilityIdentifier("schedule.removeToday")
            }
            Text("Ready 30 minutes before departure. A one-time update expires at departure automatically.")
                .sparcFont(12).foregroundStyle(SPARCTheme.muted)
        }
        .navigationBarTitleDisplayMode(.inline)
    }
    private func scheduleMetric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).sparcFont(11).foregroundStyle(SPARCTheme.muted)
            Text(value).sparcFont(23, weight: .semibold).foregroundStyle(SPARCTheme.green)
                .lineLimit(1).minimumScaleFactor(0.7)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct TodayScheduleEditor: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var target = 80.0
    @State private var departure = Date().addingTimeInterval(12 * 3600)
    @State private var loaded = false
    @State private var validation: String?

    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Just this departure.", subtitle: "Early start? Extra miles? Make one small change.")
            SPARCCard {
                VStack(alignment: .leading, spacing: 18) {
                    targetControl(target: $target)
                    Divider().overlay(SPARCTheme.line)
                    DatePicker("Departure", selection: $departure, in: Date()...Date().addingTimeInterval(48 * 3600), displayedComponents: [.date, .hourAndMinute])
                        .datePickerStyle(.compact)
                        .tint(.white)
                        .environment(\.timeZone, SPARCFormat.zone)
                        .accessibilityIdentifier("schedule.departure")
                    Text("America/Chicago · ready 30 minutes earlier")
                        .sparcFont(11).foregroundStyle(SPARCTheme.muted)
                }
            }
            SPARCNotice(title: "Your saved weekly schedule resumes after this departure.", symbol: "calendar.badge.checkmark")
            if let message = validation ?? store.errorMessage { Text(message).sparcFont(13).foregroundStyle(.white).accessibilityIdentifier("schedule.validation") }
            SPARCButton(title: "Save today's update", symbol: "checkmark", busy: store.busy) { save() }
                .accessibilityIdentifier("schedule.save")
        }
        .navigationBarTitleDisplayMode(.inline)
        .disabled(store.busy)
        .task {
            guard !loaded else { return }
            loaded = true
            target = Double(store.today?.targetPercent ?? store.weekly.targetPercent)
            departure = store.today?.departure ?? ScheduleEngine.nextOccurrence(minutes: store.weekly.departureMinutes, after: .now) ?? .now.addingTimeInterval(12 * 3600)
        }
    }
    private func save() {
        validation = nil
        do { try TodayOverride(targetPercent: Int(target), departure: departure).validate() }
        catch { validation = error.localizedDescription; return }
        Task {
            await store.saveToday(target: Int(target), departure: departure)
            if store.errorMessage == nil { dismiss() }
        }
    }
}

struct WeeklyScheduleEditor: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var draft: WeeklySchedule = .default
    @State private var loaded = false
    @State private var validation: String?

    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Make it your routine.", subtitle: "The charge you want. The morning you need.")
            HStack(spacing: 5) {
                modeButton("Smart charging", mode: .smart)
                modeButton("Fixed start", mode: .fixed)
            }
            .padding(5).background(Color(white: 0.055), in: RoundedRectangle(cornerRadius: 16))
            SPARCNotice(title: store.isVirtualCarActive
                        ? (draft.mode == .smart ? "Your virtual car uses an example lower-cost window from 1–5 a.m., adjusting the start to your battery target and departure." : "Your virtual car starts at your selected time and stops at its target or 30 minutes before departure.")
                        : store.isDemo
                        ? (draft.mode == .smart ? "Your controller chooses lower-cost hours within the available window." : "Charging uses your selected start time.")
                        : store.isControllerTest ? (draft.mode == .smart ? "Smart optimization is not connected yet. Use Fixed for a scheduled charging test, or Charge now for a manual test." : "Your Pi will start the charging test at this fixed time and finish 30 minutes before departure.")
                        : (draft.mode == .smart ? "Save your target and departure on your controller." : "Save a fixed start time on your controller."),
                        symbol: draft.mode == .smart ? "sparkles" : "clock")
            SPARCCard {
                VStack(alignment: .leading, spacing: 18) {
                    Toggle("Weekly routine", isOn: $draft.enabled).tint(.gray)
                        .accessibilityIdentifier("schedule.enabled")
                    Text("Days you leave").sparcFont(14, weight: .medium)
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 5), count: typeSize.isAccessibilitySize ? 4 : 7), spacing: 8) {
                        ForEach(1...7, id: \.self) { day in
                            Button {
                                if draft.days.contains(day) { draft.days.remove(day) }
                                else { draft.days.insert(day) }
                            } label: {
                                Text(["M", "T", "W", "T", "F", "S", "S"][day - 1])
                                    .sparcFont(13, weight: .semibold)
                                    .frame(maxWidth: .infinity, minHeight: 44)
                                    .foregroundStyle(draft.days.contains(day) ? .white : SPARCTheme.muted)
                                    .background(draft.days.contains(day) ? Color(white: 0.18) : .black, in: RoundedRectangle(cornerRadius: 12))
                                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(draft.days.contains(day) ? Color(white: 0.4) : SPARCTheme.line))
                            }
                            .buttonStyle(SPARCPressStyle())
                            .accessibilityLabel(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][day - 1])
                            .accessibilityValue(draft.days.contains(day) ? "Selected" : "Not selected")
                            .accessibilityIdentifier("schedule.day.\(day)")
                        }
                    }
                    DatePicker("Departure time", selection: Binding(get: { SPARCFormat.clockDate(draft.departureMinutes) }, set: { draft.departureMinutes = SPARCFormat.minutes($0) }), displayedComponents: .hourAndMinute)
                        .environment(\.timeZone, SPARCFormat.zone)
                        .accessibilityIdentifier("schedule.departure")
                    if draft.mode == .fixed {
                        DatePicker("Start charging at", selection: Binding(get: { SPARCFormat.clockDate(draft.fixedStartMinutes) }, set: { draft.fixedStartMinutes = SPARCFormat.minutes($0) }), displayedComponents: .hourAndMinute)
                            .environment(\.timeZone, SPARCFormat.zone)
                    }
                    Text("All times in America/Chicago").sparcFont(11).foregroundStyle(SPARCTheme.muted)
                    Divider().overlay(SPARCTheme.line)
                    targetControl(target: Binding(get: { Double(draft.targetPercent) }, set: { draft.targetPercent = Int($0) }))
                }
            }
            SPARCDetail(title: "Ready buffer", value: "30 minutes before departure")
            if let message = validation ?? store.errorMessage { Text(message).sparcFont(13).accessibilityIdentifier("schedule.validation") }
            SPARCButton(title: "Save routine", symbol: "checkmark", busy: store.busy) { save() }
                .accessibilityIdentifier("schedule.save")
        }
        .navigationBarTitleDisplayMode(.inline)
        .disabled(store.busy)
        .task { if !loaded { draft = store.weekly; loaded = true } }
    }
    private func modeButton(_ title: String, mode: ChargingMode) -> some View {
        Button { draft.mode = mode } label: {
            Text(title).sparcFont(13, weight: .medium).frame(maxWidth: .infinity).padding(.vertical, 11)
                .foregroundStyle(draft.mode == mode ? .white : SPARCTheme.muted)
                .background(draft.mode == mode ? Color(white: 0.17) : .clear, in: RoundedRectangle(cornerRadius: 12))
                .contentShape(Rectangle())
        }.buttonStyle(SPARCPressStyle())
    }
    private func save() {
        validation = nil
        do { try draft.validate() }
        catch { validation = error.localizedDescription; return }
        Task {
            await store.saveWeekly(draft)
            if store.errorMessage == nil { dismiss() }
        }
    }
}

private func targetControl(target: Binding<Double>) -> some View {
    VStack(spacing: 16) {
        HStack {
            Text("Target battery").sparcFont(14).foregroundStyle(SPARCTheme.muted)
            Spacer()
            Text("\(Int(target.wrappedValue))%")
                .sparcFont(34, weight: .semibold).tracking(-1.2).foregroundStyle(SPARCTheme.green)
                .accessibilityIdentifier("schedule.targetValue")
        }
        Slider(value: target, in: 50...100, step: 5).tint(.gray)
            .accessibilityLabel("Target battery")
            .accessibilityValue("\(Int(target.wrappedValue)) percent")
            .accessibilityIdentifier("schedule.target")
    }
}

struct PlanView: View {
    @EnvironmentObject private var store: AppStore
    @State private var showSchedule = false
    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Your charging rhythm.", subtitle: "Less thinking about charging.\nMore getting on with your day.")
            HStack {
                Text("Weekly routine").sparcFont(16, weight: .semibold)
                Spacer()
                Button("Edit") { showSchedule = true }.sparcFont(13).foregroundStyle(.white)
                    .accessibilityIdentifier("plan.edit")
            }
            SPARCCard(glossy: true) {
                VStack(alignment: .leading, spacing: 20) {
                    HStack { SPARCPill(title: store.weekly.mode == .smart ? "Smart charging" : "Fixed start", symbol: "sparkles"); Spacer(); Text(store.weekly.enabled ? "On" : "Paused").sparcFont(12).foregroundStyle(SPARCTheme.muted) }
                    HStack {
                        ForEach(1...7, id: \.self) { day in
                            Text(["M", "T", "W", "T", "F", "S", "S"][day - 1])
                                .sparcFont(13).foregroundStyle(store.weekly.days.contains(day) ? .white : Color(white: 0.35))
                                .frame(maxWidth: .infinity)
                                .accessibilityLabel(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][day - 1])
                                .accessibilityValue(store.weekly.days.contains(day) ? "Scheduled" : "Not scheduled")
                        }
                    }
                    Text("\(SPARCFormat.time(SPARCFormat.clockDate(store.weekly.departureMinutes))) departure")
                        .sparcFont(25, weight: .semibold).tracking(-0.6)
                    Text("\(store.weekly.targetPercent)% target · Home · Chicago time").sparcFont(13).foregroundStyle(SPARCTheme.muted)
                    Divider().overlay(SPARCTheme.line)
                    Label("30-minute ready buffer", systemImage: "checkmark.shield").sparcFont(12).foregroundStyle(SPARCTheme.muted)
                }
            }
            if store.today != nil { SPARCNotice(title: "A one-time update is active for your next departure.", symbol: "calendar.badge.clock") }
            SPARCPageTitle(title: "Sleep on it.", subtitle: "Your next estimated window")
            SPARCCard {
                VStack(alignment: .leading, spacing: 14) {
                    Text(store.displayedWindow)
                        .sparcFont(25, weight: .semibold).foregroundStyle(SPARCTheme.green)
                    SPARCDetail(title: "Estimated session cost", value: store.showsTelemetry ? SPARCFormat.money(store.snapshot.estimatedCost) : "—")
                    Divider().overlay(SPARCTheme.line)
                    SPARCDetail(title: "Ready before", value: store.displayedReadyTime)
                }
            }
            SPARCButton(title: "Manage my schedule", symbol: "calendar") { showSchedule = true }
            if store.isVirtualCarActive { SPARCNotice(title: "A virtual charging plan with example prices. Save a weekly routine or change just the next departure.", symbol: "sparkles") }
            Text(store.isVirtualCarActive ? (store.isDemo ? "Virtual charging is calculated in this app. A fresh launch starts at 40%." : "The virtual schedule runs on your Pi, including when this app is closed.") : store.isControllerTest ? "Turn on Virtual car in Settings to test charging and schedules with a battery at 40%." : "Plug in at home. Scheduling and optimization run locally on your Raspberry Pi.").sparcFont(12).foregroundStyle(SPARCTheme.muted)
        }
        .sheet(isPresented: $showSchedule) {
            NavigationStack {
                CurrentScheduleView().toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { showSchedule = false } } }
            }.tint(.white).preferredColorScheme(.dark)
        }
    }
}

struct PricesView: View {
    @EnvironmentObject private var store: AppStore
    private let rates = (0..<24).map { (1..<5).contains($0) ? 7.0 : 21.0 }
    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Find your good hours.", subtitle: store.isVirtualCarActive ? "Example hourly prices · Chicago time" : "Rates reported by your home controller")
            Text(store.showsTelemetry ? "\(SPARCFormat.decimal(store.snapshot.currentRateCents, digits: 1))¢" : "—")
                .sparcFont(62, weight: .semibold).tracking(-2).foregroundStyle(SPARCTheme.green)
            Text("Current total rate per kWh · estimated").sparcFont(13).foregroundStyle(SPARCTheme.muted)
            if store.isVirtualCarActive {
                SPARCCard {
                    Chart(Array(rates.enumerated()), id: \.offset) { item in
                        BarMark(x: .value("Hour", item.offset), y: .value("Cents per kWh", item.element))
                            .foregroundStyle((1..<5).contains(item.offset) ? SPARCTheme.green : Color(white: 0.29))
                            .cornerRadius(3)
                    }
                    .frame(height: 200)
                    .chartXAxis { AxisMarks(values: [0, 6, 12, 18, 23]) { value in AxisValueLabel { if let hour = value.as(Int.self) { Text("\(hour):00").sparcFont(10) } } } }
                    .chartYAxis { AxisMarks(position: .leading) }
                    .accessibilityLabel("Example electricity prices. Seven cents per kilowatt-hour from 1 to 5 AM, and twenty-one cents at other times.")
                }
                SPARCNotice(title: "Virtual-car example prices: 7¢/kWh from 1–5 a.m.; 21¢/kWh at other times. These are not your utility's rates.", symbol: "chart.bar")
            } else {
                SPARCNotice(title: "The controller reports the current rate. Hourly history will appear when the controller provides it.", symbol: "chart.bar")
            }
        }.navigationBarTitleDisplayMode(.inline)
    }
}

struct SPARCNavigationButton: View {
    let title: String
    let subtitle: String
    var symbol: String
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: symbol).sparcFont(20).foregroundStyle(SPARCTheme.green).frame(width: 25)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).sparcFont(15, weight: .semibold).foregroundStyle(.white)
                Text(subtitle).sparcFont(12).foregroundStyle(SPARCTheme.muted)
            }
            Spacer(minLength: 4)
            Image(systemName: "chevron.right").sparcFont(12).foregroundStyle(SPARCTheme.muted)
        }
        .padding(17)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(white: 0.055), in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(SPARCTheme.line))
    }
}

private extension ChargeState {
    var shortTitle: String {
        switch self {
        case .waiting: return "Waiting"
        case .charging: return "Charging"
        case .paused: return "Paused"
        case .complete: return "Complete"
        case .unplugged: return "Unplugged"
        case .offline: return "Offline"
        }
    }
}
