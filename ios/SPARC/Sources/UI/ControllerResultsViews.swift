import SwiftUI
import Charts

@MainActor
private enum ResultsDateFormatting {
    private static var formatters: [String: DateFormatter] = [:]
    static func string(_ date: Date, pattern: String) -> String {
        let formatter: DateFormatter
        if let saved = formatters[pattern] { formatter = saved }
        else {
            formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US")
            formatter.timeZone = SPARCFormat.zone
            formatter.dateFormat = pattern
            formatters[pattern] = formatter
        }
        return formatter.string(from: date)
    }
}

@MainActor
private func resultDate(_ date: Date, _ pattern: String) -> String {
    ResultsDateFormatting.string(date, pattern: pattern)
}

private enum ResultMetric: String, CaseIterable, Identifiable {
    case savings = "Savings", cost = "Cost", energy = "Energy"
    /// A manual Tesla charge claims no savings.
    static let teslaCases: [ResultMetric] = [.cost, .energy]
    var id: String { rawValue }
    func amount(_ day: HistoryDay) -> Double? {
        switch self { case .savings: return day.savingsPercent; case .cost: return day.costUSD; case .energy: return day.energyKWh }
    }
    func total(_ week: HistoryWeek) -> Double? {
        switch self { case .savings: return week.savingsPercent; case .cost: return week.costUSD; case .energy: return week.energyKWh }
    }
    func text(_ value: Double?) -> String {
        guard let value else { return "—" }
        switch self {
        case .savings: return "\(SPARCFormat.decimal(value))%"
        case .cost: return SPARCFormat.money(value)
        case .energy: return "\(SPARCFormat.decimal(value)) kWh"
        }
    }
}

private struct ResultsPage<Content: View>: View {
    let title: String
    var lazyContent = false
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            Group {
                if lazyContent {
                    LazyVStack(alignment: .leading, spacing: 16) { pageContents }
                } else {
                    VStack(alignment: .leading, spacing: 16) { pageContents }
                }
            }
            .padding(.horizontal, 16).padding(.top, 12).padding(.bottom, 24)
            .frame(maxWidth: 640).frame(maxWidth: .infinity)
        }
        .background(SPARCTheme.background)
    }
    @ViewBuilder private var pageContents: some View {
        Text(title).sparcFont(30, weight: .bold).tracking(-0.9)
            .accessibilityAddTraits(.isHeader)
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--history-interface-test") {
            Text("INTERFACE TEST DATA").sparcFont(11, weight: .bold).foregroundStyle(SPARCTheme.warning)
        }
        #endif
        content
    }
}

/// Charging history belongs to a car: the virtual car's sessions never appear under the Tesla.
enum HistoryCar: Hashable { case virtual, tesla }

extension AppStore {
    /// Results follow the viewed profile, including a Tesla without charging-key approval.
    var defaultHistoryCar: HistoryCar { profileConnectionMode == .tesla ? .tesla : .virtual }
    var hasTeslaCar: Bool { connections?.tesla.authorized == true && connections?.tesla.vehicleId != nil }
}

private extension HistorySession {
    /// How a Tesla charge SPARC started came to an end, in the owner's words.
    var teslaOutcome: String {
        guard end != nil else { return "Charging" }
        switch endReason {
        case "limit_reached": return "Reached its limit"
        case "stopped": return "Stopped"
        case "unplugged": return "Unplugged"
        case "no_power": return "No power from the charger"
        case "not_observed": return "Readings stopped"
        default: return "Ended"
        }
    }
    /// Unknown energy stays unknown; a missed final reading means the car added at least this much.
    var teslaEnergyText: String {
        guard let energyKWh else { return "Energy unavailable" }
        return (energyPartial == true ? "At least " : "") + "\(SPARCFormat.decimal(energyKWh)) kWh"
    }
}

private struct TeslaHistoryEmpty: View {
    var body: some View {
        Text("No charges yet").sparcFont(20, weight: .semibold)
            .foregroundStyle(SPARCTheme.muted)
            .frame(maxWidth: .infinity, minHeight: 120)
            .accessibilityIdentifier("history.teslaEmpty")
    }
}

/// Says nothing while synced; a short line only when results are saved copies or can't refresh.
private struct ResultsStatus: View {
    let stale: Bool
    let loading: Bool
    let message: String?
    var body: some View {
        if loading {
            ProgressView().tint(SPARCTheme.spark)
        } else if stale {
            Label(message ?? "Offline · showing saved results", systemImage: "wifi.slash")
                .sparcFont(12, weight: .semibold).foregroundStyle(SPARCTheme.warning)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct TeslaHistoryFreshness: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        ResultsStatus(stale: store.teslaHistoryIsStale, loading: store.loadingTeslaHistory,
                      message: store.teslaHistoryMessage)
            .accessibilityIdentifier("history.teslaFreshness")
    }
}

/// Real Tesla charges SPARC started: what they cost. A charge started right away has nothing to save against.
private struct TeslaSavingsContent: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        TeslaHistoryFreshness()
        if let history = store.selectedTeslaHistory, !history.sessions.isEmpty {
            let week = history.week
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text("This week").sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                    Spacer()
                    SPARCInfoButton("How it's calculated") {
                        VStack(spacing: 0) {
                            SPARCInfoRow("Energy added", "\(SPARCFormat.decimal(week.energyKWh)) kWh")
                            Divider().overlay(SPARCTheme.controlLine)
                            SPARCInfoRow("Electricity cost", ResultMetric.cost.text(week.costUSD), emphasized: true)
                        }
                        .padding(.horizontal, 16).sparcSurface(radius: 18)
                        Text("Energy is what your Tesla reports adding, priced at ComEd's hourly supply rate. Delivery, taxes and fixed fees aren't included. Charges started right away have no savings to show.")
                            .sparcFont(13).foregroundStyle(SPARCTheme.muted).fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.trailing, -10)
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(ResultMetric.cost.text(week.costUSD))
                        .sparcFont(60, weight: .bold).tracking(-1.8).monospacedDigit()
                        .lineLimit(1).minimumScaleFactor(0.5).foregroundStyle(SPARCTheme.spark)
                        .accessibilityIdentifier("savings.teslaCost")
                    Text("spent").sparcFont(19, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                }
                .accessibilityElement(children: .combine)
            }
            .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 20)
            .sparcSurface(radius: 26)
            ControllerWeekChart(week: week, metric: .cost)
        } else if !store.loadingTeslaHistory {
            TeslaHistoryEmpty()
        }
    }
}

private struct TeslaSessionsContent: View {
    @EnvironmentObject private var store: AppStore
    @Binding var selected: HistorySession?
    var body: some View {
        TeslaHistoryFreshness()
        let sessions = store.selectedTeslaHistory?.sessions ?? []
        if sessions.isEmpty, !store.loadingTeslaHistory { TeslaHistoryEmpty() }
        ForEach(sessions) { session in
            Button { selected = session } label: {
                RecordCard(date: session.start,
                           headline: session.energyKWh == nil ? session.teslaEnergyText : session.teslaEnergyText + " added",
                           headlineMuted: session.energyKWh == nil,
                           detail: "\(Int(session.initialBatteryPercent))% → \(Int(session.batteryPercent))%",
                           trailing: session.end == nil ? "Charging" : (session.costUSD.map(SPARCFormat.money) ?? "Price unavailable"))
            }.buttonStyle(SPARCPressStyle()).foregroundStyle(.white)
                .accessibilityIdentifier("history.teslaSession.\(session.id.uuidString)")
        }
        if store.selectedTeslaHistory?.nextCursor != nil {
            SPARCActionButton(title: "Load earlier charges", symbol: "clock.arrow.circlepath",
                              kind: .secondary, height: 50, busy: store.loadingTeslaHistory) {
                Task { await store.refreshTeslaHistory(loadMore: true) }
            }.accessibilityIdentifier("history.teslaLoadMore")
        }
    }
}

private struct HistoryFreshness: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        ResultsStatus(stale: store.historyIsStale && store.historyUpdatedAt != nil, loading: false, message: nil)
            .accessibilityIdentifier("history.freshness")
    }
}

/// A compact record: date and one figure on top, one detail below.
private struct RecordCard: View {
    let date: Date
    let headline: String
    var headlineMuted = false
    var headlineWarning = false
    let detail: String
    let trailing: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(resultDate(date, "MMM d")).sparcFont(17, weight: .semibold)
                Spacer(minLength: 10)
                Text(trailing).sparcFont(14).monospacedDigit().foregroundStyle(SPARCTheme.muted)
            }
            Text(headline).sparcFont(24, weight: .bold).monospacedDigit()
                .foregroundStyle(headlineMuted ? SPARCTheme.muted : (headlineWarning ? SPARCTheme.warning : SPARCTheme.spark))
            Text(detail).sparcFont(13).monospacedDigit().foregroundStyle(SPARCTheme.muted)
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading).sparcSurface(radius: 22)
        .contentShape(Rectangle())
    }
}

/// "$1.20 saved", or "$0.30 extra" when charging cost more than the comparison.
private func savedText(_ amount: Double?) -> String {
    guard let amount else { return "Savings unavailable" }
    return amount < 0 ? "\(SPARCFormat.money(-amount)) extra" : "\(SPARCFormat.money(amount)) saved"
}

private struct ControllerWeekChart: View {
    let week: HistoryWeek
    let metric: ResultMetric
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("\(metric.rawValue) by charging night").sparcFont(14, weight: .semibold)
                if metric != .savings {
                    Spacer(minLength: 8)
                    Text(metric.text(metric.total(week))).sparcFont(16, weight: .bold).monospacedDigit()
                        .accessibilityIdentifier("savings.metricTotal")
                }
            }
            Chart {
                RuleMark(y: .value("Zero", 0)).foregroundStyle(SPARCTheme.controlLine)
                ForEach(week.days) { day in
                    if day.sessionCount > 0, let amount = metric.amount(day) {
                        BarMark(x: .value("Day", day.date), y: .value(metric.rawValue, amount))
                            .foregroundStyle(metric == .savings && amount < 0 ? SPARCTheme.warning : SPARCTheme.spark)
                            .cornerRadius(4)
                            .annotation(position: amount < 0 ? .bottom : .top, spacing: 4) {
                                if metric == .savings {
                                    Text(metric.text(amount)).sparcFont(11, weight: .semibold).monospacedDigit()
                                        .foregroundStyle(amount < 0 ? SPARCTheme.warning : SPARCTheme.spark)
                                }
                            }
                    }
                }
            }
            .chartXScale(domain: week.days.map(\.date))
            .chartXAxis {
                AxisMarks(values: week.days.map(\.date)) { value in
                    AxisValueLabel {
                        if let day = value.as(String.self), let date = CampaignUpdate.date(day) {
                            Text(resultDate(date, "EEE"))
                        }
                    }
                }
            }
            .chartYAxis {
                AxisMarks { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let amount = value.as(Double.self) { Text(metric.text(amount)) }
                    }
                }
            }
            .frame(height: 190)
        }
        .padding(16).sparcSurface(radius: 22)
        .accessibilityIdentifier("savings.weekChart")
    }
}

struct ControllerSavingsView: View {
    @EnvironmentObject private var store: AppStore
    private var shown: HistoryCar { store.defaultHistoryCar }
    var body: some View {
        ResultsPage(title: "Savings") {
            if shown == .tesla { TeslaSavingsContent() } else { CombinedSavingsContent() }
        }
        .refreshable {
            await store.refresh()
            if shown == .tesla { await store.refreshTeslaHistory() } else { await store.refreshCombinedSavings() }
        }
        .task(id: shown) { if shown == .tesla { await store.refreshTeslaHistory() } }
    }
}

struct ControllerRecordsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var selected: HistorySession?
    private var shown: HistoryCar { store.defaultHistoryCar }
    var body: some View {
        ResultsPage(title: "History", lazyContent: true) {
            if shown == .tesla { TeslaSessionsContent(selected: $selected) } else { virtualSessions }
        }
        .refreshable {
            await store.refresh()
            if shown == .tesla { await store.refreshTeslaHistory() }
        }
        .task { await store.refreshHistory() }
        .task(id: shown) { if shown == .tesla { await store.refreshTeslaHistory() } }
        .sheet(item: $selected) { session in session.isTesla ? AnyView(teslaDetail(session)) : AnyView(sessionDetail(session)) }
    }
    @ViewBuilder private var virtualSessions: some View {
        VirtualProfileHistory()
        HistoryFreshness()
        if !store.historySessions.isEmpty {
            Text("Recent sessions").sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.muted).padding(.top, 6)
        } else if store.combinedSavings == nil, !store.loadingHistory {
            Text("No charges yet").sparcFont(20, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                .frame(maxWidth: .infinity, minHeight: 120)
        }
        ForEach(store.historySessions) { session in
            Button { selected = session } label: {
                RecordCard(date: session.start,
                           headline: savedText(session.savingsUSD),
                           headlineMuted: session.savingsUSD == nil,
                           headlineWarning: (session.savingsUSD ?? 0) < 0,
                           detail: session.energyKWh.map { "\(SPARCFormat.decimal($0)) kWh" } ?? "Energy unavailable",
                           trailing: session.costUSD.map(SPARCFormat.money) ?? "Price unavailable")
            }.buttonStyle(SPARCPressStyle()).foregroundStyle(.white)
                .accessibilityIdentifier("history.session.\(session.id.uuidString)")
        }
        if store.historyNextCursor != nil {
            SPARCActionButton(title: "Load earlier", symbol: "clock.arrow.circlepath",
                              kind: .secondary, height: 50, busy: store.loadingHistory) {
                Task { await store.refreshHistory(loadMore: true) }
            }.accessibilityIdentifier("history.loadMore")
        }
    }
    private func sessionDetail(_ session: HistorySession) -> some View {
        NavigationStack {
            ResultsPage(title: "Charging session") {
                Text(savedText(session.savingsUSD))
                    .sparcFont(40, weight: .bold).monospacedDigit()
                    .foregroundStyle(session.savingsUSD == nil ? SPARCTheme.muted : ((session.savingsUSD ?? 0) < 0 ? SPARCTheme.warning : SPARCTheme.spark))
                Text(resultDate(session.start, "EEEE, MMM d")).sparcFont(17, weight: .semibold)
                VStack(spacing: 0) {
                    SPARCInfoRow("Time", "\(SPARCFormat.time(session.start)) – \(session.departure.map(SPARCFormat.time) ?? "—")")
                    SPARCInfoRow("Battery", "\(Int(session.initialBatteryPercent))% → \(Int(session.batteryPercent))%")
                    SPARCInfoRow("Energy", session.energyKWh.map { "\(SPARCFormat.decimal($0)) kWh" } ?? "Unavailable")
                    SPARCInfoRow("Cost", session.costUSD.map(SPARCFormat.money) ?? "Unavailable")
                    SPARCInfoRow("Saved", session.savingsPercent.map { ResultMetric.savings.text($0) } ?? "Unavailable")
                }
                .padding(.horizontal, 16).sparcSurface(radius: 20)
                if session.targetReachedByDeadline == false {
                    Label("Missed the ready-by time", systemImage: "exclamationmark.circle")
                        .sparcFont(13).foregroundStyle(SPARCTheme.warning)
                }
            }
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selected = nil } } }
            .preferredColorScheme(.dark)
        }
    }
    private func teslaDetail(_ session: HistorySession) -> some View {
        NavigationStack {
            ResultsPage(title: "Tesla charge") {
                Text(session.teslaEnergyText)
                    .sparcFont(40, weight: .bold).monospacedDigit()
                    .foregroundStyle(session.energyKWh == nil ? SPARCTheme.muted : SPARCTheme.spark)
                    .accessibilityIdentifier("history.teslaDetail.energy")
                Text(resultDate(session.start, "EEEE, MMM d")).sparcFont(17, weight: .semibold)
                VStack(spacing: 0) {
                    SPARCInfoRow("Time", session.end.map { "\(SPARCFormat.time(session.start)) – \(SPARCFormat.time($0))" }
                                 ?? "Since \(SPARCFormat.time(session.start))")
                    SPARCInfoRow("Battery", "\(Int(session.initialBatteryPercent))% → \(Int(session.batteryPercent))%")
                    SPARCInfoRow("Cost", session.costUSD.map(SPARCFormat.money) ?? "Unavailable")
                    SPARCInfoRow("Ended", session.teslaOutcome)
                }
                .padding(.horizontal, 16).sparcSurface(radius: 20)
                if session.energyPartial == true {
                    Label("Last reading missed · may be higher", systemImage: "exclamationmark.circle")
                        .sparcFont(13).foregroundStyle(SPARCTheme.warning)
                }
            }
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selected = nil } } }
            .preferredColorScheme(.dark)
        }
    }
}

struct CampaignPlanView: View {
    @EnvironmentObject private var store: AppStore
    @State private var draft: CampaignUpdate?
    @State private var draftOrigin: CampaignUpdate?
    @State private var newerSettingsAvailable = false
    @State private var saving = false
    @State private var saved = false
    var body: some View {
        ResultsPage(title: "Your nightly schedule.") {
            if let campaign = store.campaign {
                Text("\(campaign.nights) nights · Day-ahead + live prices").sparcFont(16, weight: .semibold)
                Text("\(resultDate(campaign.firstStart, "MMM d")) – \(resultDate(campaign.finalDeparture, "MMM d")) · Chicago time")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                if let current = campaign.current {
                    Text("\(current.finalizedAt == nil ? "Current night" : "Last night"): \(SPARCFormat.time(current.start)) – \(SPARCFormat.time(current.departure)), target \(current.targetBatteryPercent)%")
                        .sparcFont(14).foregroundStyle(SPARCTheme.spark)
                }
                Text("Status: \(phase(campaign))")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                if store.campaignIsStale {
                    Label("Offline · connect to edit", systemImage: "wifi.slash")
                        .sparcFont(13).foregroundStyle(SPARCTheme.warning)
                }
                if newerSettingsAvailable {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Changed on another device").sparcFont(13).foregroundStyle(SPARCTheme.warning)
                        Button("Reload") {
                            if let current = store.campaign { loadDraft(current) }
                        }
                        .sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.spark)
                        .frame(minHeight: 44).accessibilityIdentifier("campaign.reload")
                    }.accessibilityIdentifier("campaign.newerSettings")
                }
                if draft != nil { editor }
            }
            if let error = store.errorMessage {
                Text(error).sparcFont(13).foregroundStyle(SPARCTheme.dangerText)
                    .accessibilityIdentifier("campaign.error")
            }
        }
        .refreshable { await store.refresh() }
        .onAppear { if draft == nil, let campaign = store.campaign { loadDraft(campaign) } }
        .onChange(of: store.campaign) { _, value in
            guard let value, !saving else { return }
            let update = CampaignUpdate(value)
            if draft == nil || draft == draftOrigin { loadDraft(value) }
            else if update != draftOrigin { newerSettingsAvailable = true }
        }
        .onChange(of: draft) { _, value in
            if let campaign = store.campaign, value != CampaignUpdate(campaign) { saved = false }
        }
    }

    private func loadDraft(_ campaign: CampaignSchedule) {
        let current = CampaignUpdate(campaign)
        draft = current; draftOrigin = current; newerSettingsAvailable = false
    }

    private func phase(_ campaign: CampaignSchedule) -> String {
        guard campaign.enabled else { return "Paused" }
        switch campaign.phase {
        case "running", "active": return "Tonight is running"
        case "waiting": return "Waiting for tonight"
        case "betweenNights": return "Waiting for the next night"
        case "arming": return "Preparing the next night"
        case "finishing": return "Saving tonight's results"
        case "complete", "completed": return "Run finished"
        default: return "Scheduled"
        }
    }

    private func field<T>(_ key: WritableKeyPath<CampaignUpdate, T>, fallback: T) -> Binding<T> {
        Binding(get: { draft?[keyPath: key] ?? fallback }, set: { draft?[keyPath: key] = $0 })
    }
    private func clock(_ key: WritableKeyPath<CampaignUpdate, Int>) -> Binding<Date> {
        Binding(get: { SPARCFormat.clockDate(draft?[keyPath: key] ?? 0) }, set: { value in
            let parts = ScheduleEngine.chicagoCalendar.dateComponents([.hour, .minute], from: value)
            draft?[keyPath: key] = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        })
    }
    private var firstDate: Binding<Date> {
        Binding(get: { CampaignUpdate.date(draft?.startDate ?? "") ?? Date() },
                set: { draft?.startDate = CampaignUpdate.dayString($0) })
    }
    private var editor: some View {
        VStack(alignment: .leading, spacing: 16) {
            Toggle("Nightly schedule", isOn: field(\.enabled, fallback: false)).tint(SPARCTheme.spark)
            DatePicker("First night", selection: firstDate, displayedComponents: .date)
                .environment(\.timeZone, SPARCFormat.zone)
            Stepper("\(draft?.nights ?? 7) nights", value: field(\.nights, fallback: 7), in: 1...90)
            DatePicker("Earliest charging", selection: clock(\.startMinutes), displayedComponents: .hourAndMinute)
                .environment(\.timeZone, SPARCFormat.zone)
            DatePicker("Ready by morning", selection: clock(\.departureMinutes), displayedComponents: .hourAndMinute)
                .environment(\.timeZone, SPARCFormat.zone)
            Stepper("Start at \(draft?.initialBatteryPercent ?? 20)%", value: field(\.initialBatteryPercent, fallback: 20), in: 0...99)
            Stepper("Target \(draft?.targetBatteryPercent ?? 80)%", value: field(\.targetBatteryPercent, fallback: 80), in: 50...100)
            Stepper("Screen sleeps after \(draft?.screenSleepMinutes ?? 5) min", value: field(\.screenSleepMinutes, fallback: 5), in: 1...120)
            SPARCActionButton(title: saved ? "Schedule saved" : "Save nightly schedule", symbol: "checkmark",
                              kind: .primary, height: 52, busy: store.busy) {
                guard var update = draft else { return }
                update.expectedRevision = store.campaign?.revision ?? update.expectedRevision
                saving = true
                Task {
                    await store.saveCampaign(update)
                    saving = false
                    saved = store.errorMessage == nil
                    if saved, let current = store.campaign { loadDraft(current) }
                    else if let current = store.campaign, CampaignUpdate(current) != draftOrigin { newerSettingsAvailable = true }
                }
            }.accessibilityIdentifier("campaign.save")
        }
        .sparcFont(14).padding(16).sparcSurface(radius: 24)
        .disabled(store.busy || store.campaignIsStale)
    }
}
