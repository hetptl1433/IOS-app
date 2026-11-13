import SwiftUI
import UIKit

// MARK: - Shared page scaffold

/// Space the floating tab bar covers at the bottom of a tab page.
private let insightsTabBarClearance: CGFloat = 16

private enum InsightsPageGlow { case none, topLeading, topTrailing }

/// Black page with the soft corner glow from the approved designs. The glow is still.
private struct InsightsSettingsPage<Content: View>: View {
    var glow: InsightsPageGlow = .none
    var bottomInset: CGFloat = insightsTabBarClearance
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) { content }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, bottomInset)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .background { backdrop }
    }

    @ViewBuilder private var backdrop: some View {
        switch glow {
        case .none:
            Color.black.ignoresSafeArea()
        case .topLeading:
            ZStack(alignment: .topLeading) {
                Color.black
                glowDisc(SPARCTheme.spark.opacity(0.13)).offset(x: -110, y: -150)
            }.ignoresSafeArea()
        case .topTrailing:
            ZStack(alignment: .topTrailing) {
                Color.black
                glowDisc(SPARCTheme.green.opacity(0.15)).offset(x: 130, y: -150)
            }.ignoresSafeArea()
        }
    }

    private func glowDisc(_ color: Color) -> some View {
        RadialGradient(colors: [color, .clear], center: .center, startRadius: 0, endRadius: 200)
            .frame(width: 440, height: 440)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

private func insightsPageTitle(_ text: String) -> some View {
    Text(text)
        .sparcFont(30, weight: .bold).tracking(-0.9)
        .lineSpacing(0)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityAddTraits(.isHeader)
}

private enum InsightsFormat {
    private static func make(_ pattern: String) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.timeZone = SPARCFormat.zone
        formatter.dateFormat = pattern
        return formatter
    }
    private static let nightFormat = make("EEEEE d")
    private static let weekdayDateFormat = make("EEE, MMM d")
    private static let fullDateFormat = make("EEEE, MMM d")
    private static let monthNameFormat = make("LLLL")
    private static let monthYearFormat = make("LLLL yyyy")

    static func night(_ date: Date) -> String { nightFormat.string(from: date) }
    static func weekdayDate(_ date: Date) -> String { weekdayDateFormat.string(from: date) }
    static func fullDate(_ date: Date) -> String { fullDateFormat.string(from: date) }
    static func month(_ date: Date) -> String {
        let calendar = ScheduleEngine.chicagoCalendar
        let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: .now)
        return (sameYear ? monthNameFormat : monthYearFormat).string(from: date)
    }
    static func kWh(_ value: Double) -> String { SPARCFormat.decimal(value) }

    /// Share of the "charge right away" price that was saved, 0...1.
    static func savedShare(_ record: ChargingRecord) -> Double {
        let immediate = record.cost + record.savings
        guard immediate > 0 else { return 0 }
        return min(1, max(0, record.savings / immediate))
    }
    static func percentLess(_ share: Double) -> Int { Int((share * 100).rounded()) }
}

// MARK: - Insights

private enum InsightPeriod: String, CaseIterable, Identifiable {
    case week = "Week", month = "Month", year = "Year"
    var id: String { rawValue }
    var start: Date {
        let component: Calendar.Component = self == .week ? .weekOfYear : self == .month ? .month : .year
        return ScheduleEngine.chicagoCalendar.dateInterval(of: component, for: .now)?.start ?? .distantPast
    }
    var word: String {
        switch self {
        case .week: return "this week"
        case .month: return "this month"
        case .year: return "in \(ScheduleEngine.chicagoCalendar.component(.year, from: .now))"
        }
    }
}

private enum InsightMetric: String, CaseIterable, Identifiable {
    case savings = "Savings", cost = "Cost", energy = "Energy"
    var id: String { rawValue }
    var heading: String {
        switch self {
        case .savings: return "Saved"
        case .cost: return "Charging cost"
        case .energy: return "Energy at the meter"
        }
    }
    func value(of record: ChargingRecord) -> Double {
        switch self {
        case .savings: return record.savings
        case .cost: return record.cost
        case .energy: return record.energyKWh
        }
    }
    func text(_ value: Double) -> String {
        self == .energy ? "\(SPARCFormat.decimal(value)) kWh" : SPARCFormat.money(value)
    }
    func barText(_ value: Double) -> String {
        self == .energy ? SPARCFormat.decimal(value, digits: 0) : SPARCFormat.money(value)
    }
}

private struct InsightBar: Identifiable, Equatable {
    let id: Int
    let label: String
    let value: Double
}

/// Everything the Insights page shows for one period and measure.
private struct InsightData {
    let records: [ChargingRecord]
    let cost: Double
    let savings: Double
    let energy: Double
    let total: Double
    let bars: [InsightBar]

    init(all: [ChargingRecord], period: InsightPeriod, metric: InsightMetric) {
        let start = period.start
        let records = all.filter { $0.date >= start }.sorted { $0.date < $1.date }
        self.records = records
        cost = records.reduce(0) { $0 + $1.cost }
        savings = records.reduce(0) { $0 + $1.savings }
        energy = records.reduce(0) { $0 + $1.energyKWh }
        total = records.reduce(0) { $0 + metric.value(of: $1) }

        let calendar = ScheduleEngine.chicagoCalendar
        if period == .year {
            let thisMonth = calendar.component(.month, from: .now)
            var months = Array(repeating: 0.0, count: thisMonth)
            for record in records {
                let month = calendar.component(.month, from: record.date)
                if (1...thisMonth).contains(month) { months[month - 1] += metric.value(of: record) }
            }
            let letters = calendar.veryShortMonthSymbols
            bars = months.enumerated().map { index, value in
                InsightBar(id: index, label: letters.indices.contains(index) ? letters[index] : "", value: value)
            }
        } else {
            var nights: [Date: Double] = [:]
            for record in records { nights[calendar.startOfDay(for: record.date), default: 0] += metric.value(of: record) }
            bars = nights.keys.sorted().enumerated().map { index, night in
                InsightBar(id: index, label: InsightsFormat.night(night), value: nights[night] ?? 0)
            }
        }
    }
}

struct InsightsView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.sparcToast) private var toast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var period: InsightPeriod = .month
    @State private var metric: InsightMetric = .savings

    var body: some View {
        if store.isDemo { demoBody } else { ControllerSavingsView() }
    }

    @ViewBuilder private var demoBody: some View {
        let data = InsightData(all: store.records, period: period, metric: metric)
        InsightsSettingsPage(glow: .topLeading) {
            insightsPageTitle("Your good-energy story.")
            SPARCSegmented(segments: InsightPeriod.allCases.map { SPARCSegment(value: $0, title: $0.rawValue) },
                           selection: $period, height: 38)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Period")
                .accessibilityIdentifier("insights.period")
                .padding(.top, 16)
            if store.records.isEmpty {
                totalSection(data, empty: true)
                metricChips
                emptyCard(symbol: "chart.xyaxis.line", title: "Your story starts with a charge.")
                    .padding(.top, 8)
            } else {
                totalSection(data, empty: false)
                metricChips
                if data.records.isEmpty {
                    emptyCard(symbol: "moon.stars", title: "No charges \(period.word) yet.")
                        .padding(.top, 8)
                } else {
                    chartCard(data)
                    perspectiveCard(data)
                }
                milestonesSection
                if !data.records.isEmpty { tiles(data) }
            }
        }
    }

    // MARK: Total and measure

    private func totalSection(_ data: InsightData, empty: Bool) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 0) {
                Text(metric.heading + " " + period.word)
                    .sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                if metric == .savings {
                    SPARCInfoButton("How savings are calculated") {
                        Text("Compared with charging right away. Taxes and fixed charges are not included.")
                    }
                }
            }
            Text(empty ? "—" : metric.text(data.total))
                .sparcFont(60, weight: .bold).tracking(-3.3).monospacedDigit()
                .foregroundStyle(metric == .savings && !empty ? SPARCTheme.spark : .white)
                .lineLimit(1).minimumScaleFactor(0.5)
                .padding(.top, 4)
            Text(empty ? "No charges yet" : "\(data.records.count) smart \(data.records.count == 1 ? "charge" : "charges")")
                .sparcFont(13.5).monospacedDigit().foregroundStyle(SPARCTheme.muted)
                .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .padding(.top, 18)
    }

    private var metricChips: some View {
        HStack(spacing: 8) {
            ForEach(InsightMetric.allCases) { value in
                let on = value == metric
                Button {
                    withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { metric = value }
                } label: {
                    Text(value.rawValue).sparcFont(13.5, weight: .semibold)
                        .padding(.horizontal, 15)
                        .frame(minHeight: 36)
                        .foregroundStyle(on ? SPARCTheme.spark : Color(white: 0.79))
                        .background(on ? SPARCTheme.spark.opacity(0.14) : Color(white: 0.067), in: Capsule())
                        .overlay(Capsule().stroke(on ? SPARCTheme.spark.opacity(0.5) : Color(white: 0.141), lineWidth: 1))
                        .padding(.vertical, 4)
                        .contentShape(Rectangle())
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityAddTraits(on ? .isSelected : [])
                .accessibilityIdentifier("insights.metric.\(value.rawValue.lowercased())")
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Measure")
        .accessibilityIdentifier("insights.metric")
        .padding(.top, 12)
    }

    // MARK: Chart

    private func chartCard(_ data: InsightData) -> some View {
        let bars = data.bars
        let count = bars.count
        let top = bars.map(\.value).max() ?? 0
        let gap: CGFloat = period == .year ? 6 : (count <= 8 ? 10 : 4)
        let labelStep = period == .year || count <= 8 ? 1 : Int((Double(count) / 5).rounded(.up))
        return VStack(spacing: 8) {
            HStack(alignment: .bottom, spacing: gap) {
                ForEach(bars) { bar in
                    barColumn(bar, last: bar.id == count - 1, top: top,
                              showsValue: period == .year ? bar.id == count - 1 : count <= 8)
                }
            }
            .frame(height: 150)
            .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: bars)
            HStack(spacing: gap) {
                ForEach(bars) { bar in
                    Color.clear.frame(maxWidth: .infinity).frame(height: 14)
                        .overlay {
                            if (count - 1 - bar.id) % labelStep == 0 {
                                Text(bar.label).sparcFont(11, weight: .semibold)
                                    .foregroundStyle(SPARCTheme.tertiary).fixedSize()
                            }
                        }
                }
            }
        }
        .padding(.top, 18).padding(.horizontal, 16).padding(.bottom, 14)
        .sparcSurface(radius: 26)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(metric.heading) \(period == .year ? "by month" : "by night")")
        .accessibilityValue(bars.filter { $0.value > 0 }.map { "\($0.label), \(metric.barText($0.value))" }.joined(separator: ". "))
        .padding(.top, 8)
    }

    private func barColumn(_ bar: InsightBar, last: Bool, top: Double, showsValue: Bool) -> some View {
        let positive = bar.value > 0
        let height: CGFloat = positive ? max(6, (bar.value / max(top, 0.0001) * 118).rounded()) : 4
        let fill: Color = !positive ? Color(white: 0.11)
            : metric == .savings ? (last ? SPARCTheme.spark : SPARCTheme.spark.opacity(0.45))
            : (last ? .white : Color(white: 0.29))
        let glow = last && positive && metric == .savings
        return VStack(spacing: 6) {
            Color.clear.frame(maxWidth: .infinity).frame(height: 14)
                .overlay {
                    if positive && showsValue {
                        Text(metric.barText(bar.value)).sparcFont(10.5, weight: .semibold).monospacedDigit()
                            .foregroundStyle(last ? .white : SPARCTheme.tertiary).fixedSize()
                    }
                }
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(fill)
                .frame(maxWidth: 80)
                .frame(height: height)
                .shadow(color: glow ? SPARCTheme.spark.opacity(0.5) : .clear, radius: 8)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
    }

    // MARK: Perspective

    private func perspectiveCard(_ data: InsightData) -> some View {
        let immediate = data.cost + data.savings
        let fraction = immediate > 0 ? min(1, max(0, data.cost / immediate)) : 1
        let share = immediate > 0 ? max(0, data.savings / immediate) : 0
        return VStack(alignment: .leading, spacing: 0) {
            Text("A little perspective.").sparcFont(17, weight: .semibold).tracking(-0.2)
                .accessibilityAddTraits(.isHeader)
            comparisonRow("Charging right away", SPARCFormat.money(immediate), valueColor: .white)
                .padding(.top, 14)
            comparisonBar(fraction: 1, fill: AnyShapeStyle(Color(white: 0.29)), glows: false)
            comparisonRow("With SPARC", SPARCFormat.money(data.cost), valueColor: SPARCTheme.spark)
                .padding(.top, 14)
            comparisonBar(fraction: fraction, fill: AnyShapeStyle(SPARCTheme.fillGradient), glows: true)
            keptLine(data.savings, share: share)
                .padding(.top, 16)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 26)
        .padding(.top, 12)
    }

    private func comparisonRow(_ title: String, _ value: String, valueColor: Color) -> some View {
        HStack {
            Text(title).foregroundStyle(SPARCTheme.muted)
            Spacer(minLength: 8)
            Text(value).fontWeight(.semibold).monospacedDigit().foregroundStyle(valueColor)
        }
        .sparcFont(13)
        .accessibilityElement(children: .combine)
    }

    private func comparisonBar(fraction: Double, fill: AnyShapeStyle, glows: Bool) -> some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(Color(white: 0.086))
                RoundedRectangle(cornerRadius: 6, style: .continuous).fill(fill)
                    .frame(width: max(0, proxy.size.width * fraction))
                    .shadow(color: glows ? SPARCTheme.spark.opacity(0.45) : .clear, radius: 6)
            }
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        }
        .frame(height: 12)
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: fraction)
        .padding(.top, 7)
        .accessibilityHidden(true)
    }

    private func keptLine(_ savings: Double, share: Double) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "leaf").font(.system(size: 18, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark).accessibilityHidden(true)
            Group {
                if savings > 0 {
                    Text("You saved \(SPARCFormat.money(savings)) · ")
                        + Text("\(InsightsFormat.percentLess(share))% less").foregroundColor(SPARCTheme.spark)
                } else {
                    Text("About the same as charging right away.")
                }
            }
            .sparcFont(14, weight: .semibold).monospacedDigit()
            .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SPARCTheme.spark.opacity(0.08), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(SPARCTheme.spark.opacity(0.28), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    // MARK: Milestones

    private var milestonesSection: some View {
        let items = store.milestones
        let unlocked = items.filter(\.unlocked).count
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Milestones").sparcFont(17, weight: .semibold).tracking(-0.2)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 8)
                Text("\(unlocked) of \(items.count) unlocked").sparcFont(12.5).foregroundStyle(SPARCTheme.tertiary)
            }
            .padding(.horizontal, 2)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
                ForEach(items) { milestoneTile($0) }
            }
        }
        .padding(.top, 12)
    }

    private func milestoneTile(_ item: SPARCMilestone) -> some View {
        let done = item.unlocked
        return Button {
            toast(done ? "\(item.title) · unlocked \(item.detail)" : "\(item.title) · \(item.detail)")
        } label: {
            VStack(spacing: 8) {
                ZStack {
                    Circle().fill(done ? SPARCTheme.spark : Color(white: 0.078))
                    Image(systemName: item.symbol).font(.system(size: 20, weight: .semibold))
                        .foregroundStyle(done ? SPARCTheme.onSpark : SPARCTheme.tertiary)
                    if !done {
                        Circle().stroke(Color(white: 0.165), lineWidth: 3).frame(width: 45, height: 45)
                        Circle().trim(from: 0, to: item.progress)
                            .stroke(SPARCTheme.spark, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                            .frame(width: 45, height: 45)
                    }
                }
                .frame(width: 48, height: 48)
                .accessibilityHidden(true)
                Text(item.title).sparcFont(12.5, weight: .semibold)
                    .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                Text(item.detail).sparcFont(11).monospacedDigit()
                    .foregroundStyle(done ? SPARCTheme.mint : SPARCTheme.muted)
                    .multilineTextAlignment(.center)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 8).padding(.top, 14).padding(.bottom, 12)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .sparcSurface(radius: 22, tinted: done)
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel(done ? "\(item.title), unlocked \(item.detail)" : "\(item.title), \(item.detail)")
        .accessibilityIdentifier("insights.milestone.\(item.id)")
    }

    // MARK: Tiles

    private func tiles(_ data: InsightData) -> some View {
        HStack(spacing: 10) {
            tile("Effective rate", data.energy > 0 ? SPARCFormat.decimal(data.cost / data.energy * 100) : "—", unit: "¢/kWh")
            tile("Energy", SPARCFormat.decimal(data.energy), unit: "kWh")
        }
        .padding(.top, 12)
    }

    private func tile(_ title: String, _ value: String, unit: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).sparcFont(12, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                Text(value).sparcFont(24, weight: .bold).tracking(-0.7).monospacedDigit()
                Text(" \(unit)").sparcFont(13).foregroundStyle(SPARCTheme.muted)
            }
            .lineLimit(1).minimumScaleFactor(0.7)
        }
        .padding(15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 22)
        .accessibilityElement(children: .combine)
    }

    private func emptyCard(symbol: String, title: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: symbol).font(.system(size: 28, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark).accessibilityHidden(true)
            Text(title).sparcFont(20, weight: .semibold).tracking(-0.3)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 26)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Records

private struct RecordMonth: Identifiable {
    let key: DateComponents
    let title: String
    var items: [(index: Int, record: ChargingRecord)]
    var id: String { "\(key.year ?? 0)-\(key.month ?? 0)" }
}

struct RecordsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var selectedRecord: ChargingRecord?

    private var records: [ChargingRecord] { store.records.sorted { $0.date > $1.date } }

    private var months: [RecordMonth] {
        let calendar = ScheduleEngine.chicagoCalendar
        var result: [RecordMonth] = []
        for (index, record) in records.enumerated() {
            let key = calendar.dateComponents([.year, .month], from: record.date)
            if let last = result.last, last.key == key {
                result[result.count - 1].items.append((index, record))
            } else {
                result.append(RecordMonth(key: key, title: InsightsFormat.month(record.date), items: [(index, record)]))
            }
        }
        return result
    }

    var body: some View {
        Group {
            if store.isDemo { demoBody } else { ControllerRecordsView() }
        }
        .navigationTitle("History")
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder private var demoBody: some View {
        let list = records
        InsightsSettingsPage(glow: .topTrailing) {
            insightsPageTitle("Every charge,\naccounted for.")
            if list.isEmpty {
                emptyCard.padding(.top, 16)
            } else {
                totals(list).padding(.top, 16)
                if list.count > 1, let best = list.max(by: { $0.savings < $1.savings }) { bestNight(best).padding(.top, 12) }
                ForEach(months) { month in
                    Text(month.title).sparcFont(17, weight: .semibold).tracking(-0.2)
                        .accessibilityAddTraits(.isHeader)
                        .padding(.top, 20).padding(.horizontal, 2)
                    monthCard(month).padding(.top, 10)
                }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selectedRecord) { record in
            SessionDetailView(record: record)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .presentationCornerRadius(38)
                .presentationBackground(Color(white: 0.071))
                .preferredColorScheme(.dark)
        }
    }

    private func totals(_ list: [ChargingRecord]) -> some View {
        HStack(spacing: 8) {
            SPARCStatTile(title: "Energy", value: SPARCFormat.decimal(list.reduce(0) { $0 + $1.energyKWh }), unit: "kWh")
            SPARCStatTile(title: "Cost", value: SPARCFormat.money(list.reduce(0) { $0 + $1.cost }))
            SPARCStatTile(title: "Saved", value: SPARCFormat.money(list.reduce(0) { $0 + $1.savings }), tinted: true)
        }
    }

    private func bestNight(_ record: ChargingRecord) -> some View {
        let less = InsightsFormat.percentLess(InsightsFormat.savedShare(record))
        let gradient = LinearGradient(stops: [
            .init(color: Color(red: 0x13 / 255, green: 0x26 / 255, blue: 0x1C / 255), location: 0),
            .init(color: Color(red: 0x0A / 255, green: 0x12 / 255, blue: 0x0E / 255), location: 0.6),
            .init(color: Color(red: 0x06 / 255, green: 0x08 / 255, blue: 0x07 / 255), location: 1)
        ], startPoint: .topLeading, endPoint: .bottomTrailing)
        return Button { selectedRecord = record } label: {
            HStack(spacing: 14) {
                Image(systemName: "trophy").font(.system(size: 21, weight: .semibold))
                    .foregroundStyle(SPARCTheme.onSpark)
                    .frame(width: 48, height: 48)
                    .background(SPARCTheme.spark, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Best night yet").sparcFont(12, weight: .semibold).tracking(0.7)
                        .textCase(.uppercase).foregroundStyle(SPARCTheme.mint)
                    Text(InsightsFormat.weekdayDate(record.date)).sparcFont(17, weight: .bold).tracking(-0.2)
                        .foregroundStyle(.white)
                    Text("\(SPARCFormat.money(record.savings)) saved · \(less)% less")
                        .sparcFont(13).monospacedDigit()
                        .foregroundStyle(Color(red: 0xC9 / 255, green: 0xF2 / 255, blue: 0xDC / 255))
                }
                Spacer(minLength: 0)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(gradient, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).stroke(SPARCTheme.spark.opacity(0.32), lineWidth: 1))
            .overlay(alignment: .topTrailing) {
                ZStack(alignment: .topTrailing) {
                    Image(systemName: "sparkle").font(.system(size: 13, weight: .bold))
                        .foregroundStyle(SPARCTheme.spark).padding(.top, 14).padding(.trailing, 22)
                    Image(systemName: "sparkle").font(.system(size: 8, weight: .bold))
                        .foregroundStyle(Color(red: 0xBD / 255, green: 0xF5 / 255, blue: 0xD7 / 255))
                        .padding(.top, 34).padding(.trailing, 46)
                }
                .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel("Best night yet, \(InsightsFormat.weekdayDate(record.date)), \(SPARCFormat.money(record.savings)) saved, \(less) percent less")
        .accessibilityIdentifier("records.bestNight")
    }

    private func monthCard(_ month: RecordMonth) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(month.items.enumerated()), id: \.element.record.id) { position, item in
                if position > 0 { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }
                row(item.record, index: item.index)
            }
        }
        .sparcSurface(radius: 24)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
    }

    private func row(_ record: ChargingRecord, index: Int) -> some View {
        let share = InsightsFormat.savedShare(record)
        return Button { selectedRecord = record } label: {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Image(systemName: "moon").font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(SPARCTheme.spark)
                        .frame(width: 38, height: 38)
                        .background(SPARCTheme.control, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(InsightsFormat.weekdayDate(record.date)).sparcFont(15, weight: .semibold)
                        Text("\(InsightsFormat.kWh(record.energyKWh)) kWh").sparcFont(12).monospacedDigit()
                            .foregroundStyle(SPARCTheme.muted)
                    }
                    Spacer(minLength: 8)
                    VStack(alignment: .trailing, spacing: 3) {
                        Text(SPARCFormat.money(record.cost)).sparcFont(15, weight: .semibold).monospacedDigit()
                        Text("\(SPARCFormat.money(record.savings)) saved")
                            .sparcFont(11.5, weight: .bold).monospacedDigit()
                            .foregroundStyle(SPARCTheme.spark)
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(SPARCTheme.spark.opacity(0.13), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }
                    Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Color(white: 0.43)).accessibilityHidden(true)
                }
                GeometryReader { proxy in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color(white: 0.106))
                        Capsule().fill(SPARCTheme.fillGradient).frame(width: proxy.size.width * share)
                    }
                }
                .frame(height: 4)
                .padding(.top, 12).padding(.leading, 50)
                .accessibilityHidden(true)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 16).padding(.top, 15).padding(.bottom, 14)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel("\(InsightsFormat.weekdayDate(record.date)), \(InsightsFormat.kWh(record.energyKWh)) kilowatt hours, cost \(SPARCFormat.money(record.cost)), saved \(SPARCFormat.money(record.savings))")
        .accessibilityIdentifier("records.session.\(index)")
    }

    private var emptyCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "clock.arrow.circlepath").font(.system(size: 28, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark).accessibilityHidden(true)
            Text("Good things take a first charge.").sparcFont(20, weight: .semibold).tracking(-0.3)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 26)
        .accessibilityElement(children: .combine)
    }
}

/// The charge detail, shown as a native bottom sheet.
private struct SessionDetailView: View {
    @Environment(\.dismiss) private var dismiss
    let record: ChargingRecord

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text(InsightsFormat.fullDate(record.date))
                    .sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                    .accessibilityAddTraits(.isHeader)
                Text(SPARCFormat.money(record.savings))
                    .sparcFont(58, weight: .bold).tracking(-3.2).monospacedDigit()
                    .foregroundStyle(SPARCTheme.spark)
                    .lineLimit(1).minimumScaleFactor(0.5)
                    .padding(.top, 6)
                    .accessibilityLabel("\(SPARCFormat.money(record.savings)) saved on this charge")
                Text("saved on this charge").sparcFont(14).foregroundStyle(SPARCTheme.muted)
                    .padding(.top, 6)
                    .accessibilityHidden(true)
                VStack(spacing: 0) {
                    detailRow("Energy at the meter", "\(InsightsFormat.kWh(record.energyKWh)) kWh", divider: true)
                    detailRow("Charging cost", SPARCFormat.money(record.cost), divider: true)
                    detailRow("Charging right away", SPARCFormat.money(record.cost + record.savings), divider: false)
                }
                .background(Color(white: 0.039), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Color(white: 0.137), lineWidth: 1))
                .padding(.top, 18)
                SPARCActionButton(title: "Done", kind: .secondary, height: 52) { dismiss() }
                    .accessibilityIdentifier("records.sessionDone")
                    .padding(.top, 16)
            }
            .padding(.horizontal, 20).padding(.top, 28).padding(.bottom, 20)
            .frame(maxWidth: 640, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .scrollBounceBehavior(.basedOnSize)
        .accessibilityIdentifier("records.sessionDetail")
    }

    private func detailRow(_ title: String, _ value: String, divider: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(title).foregroundStyle(SPARCTheme.muted)
            Spacer(minLength: 8)
            Text(value).fontWeight(.semibold).monospacedDigit().multilineTextAlignment(.trailing)
        }
        .sparcFont(14)
        .padding(.horizontal, 16).padding(.vertical, 12)
        .overlay(alignment: .bottom) {
            if divider { Rectangle().fill(Color(white: 0.118)).frame(height: 1) }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Settings

private struct BluetoothRune: Shape {
    func path(in rect: CGRect) -> Path {
        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + x / 24 * rect.width, y: rect.minY + y / 24 * rect.height)
        }
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

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore
    @AppStorage(SavingsComparison.storageKey) private var savingsComparison: SavingsComparison = .fixedPlan
    @EnvironmentObject private var provisioning: ProvisioningManager
    @Environment(\.sparcToast) private var toast

    var body: some View {
        InsightsSettingsPage(bottomInset: 40) {
            insightsPageTitle("Settings")
            if let message = store.errorMessage { errorNotice(message).padding(.top, 14) }
            vehicleCard.padding(.top, 18)
            sectionLabel("Connection")
            connectionRow
            sectionLabel("Your car")
            chargingConnectionsCard
            sectionLabel("Charging")
            chargingCard
            sectionLabel("Privacy")
            privacyCard
            developerSection
            footer.padding(.top, 28)
        }
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: Pieces

    private func sectionLabel(_ title: String, tag: String? = nil) -> some View {
        HStack(spacing: 8) {
            Text(title)
            if let tag {
                Text(tag).font(.system(size: 10.5, weight: .semibold)).tracking(0.6)
                    .foregroundStyle(SPARCTheme.spark)
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(SPARCTheme.spark.opacity(0.13), in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            }
        }
        .sparcFont(12, weight: .semibold).tracking(1)
        .textCase(.uppercase)
        .foregroundStyle(SPARCTheme.tertiary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 22).padding(.bottom, 8).padding(.horizontal, 4)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private func errorNotice(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.circle").foregroundStyle(SPARCTheme.danger).accessibilityHidden(true)
            Text(message).sparcFont(13).foregroundStyle(SPARCTheme.dangerText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SPARCTheme.danger.opacity(0.08), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(SPARCTheme.danger.opacity(0.35), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    // MARK: Vehicle

    /// Plugged in or not, only when the app is really reading the vehicle.
    private var plugState: (title: String, live: Bool)? {
        guard store.showsTelemetry else { return nil }
        switch store.snapshot.state {
        case .unplugged: return ("Unplugged", false)
        case .offline: return nil
        case .waiting, .charging, .paused, .complete: return ("Plugged in", true)
        }
    }

    private var vehicleCard: some View {
        let plug = plugState
        let name = store.displayVehicleName
        return HStack(spacing: 6) {
            ControllerVehicleView(charging: false)
                .frame(width: 138, height: 92)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                Text(name).sparcFont(18, weight: .bold).tracking(-0.2)
                if let plug {
                    HStack(spacing: 6) {
                        Image(systemName: plug.live ? "powerplug" : "powerplug.portrait")
                            .font(.system(size: 11, weight: .bold)).accessibilityHidden(true)
                        Text(plug.title).sparcFont(12, weight: .bold)
                    }
                    .foregroundStyle(plug.live ? SPARCTheme.spark : Color(white: 0.82))
                    .padding(.horizontal, 9).padding(.vertical, 4)
                    .background(plug.live ? SPARCTheme.spark.opacity(0.12) : Color.white.opacity(0.07),
                                in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .padding(.top, 8)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.leading, 4).padding(.trailing, 16).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LinearGradient(stops: [
            .init(color: Color(white: 0.09), location: 0),
            .init(color: Color(white: 0.031), location: 0.6),
            .init(color: .black, location: 1)
        ], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 26, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous).stroke(Color(white: 0.15), lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(name), charges at home\(plug.map { ", " + $0.title.lowercased() } ?? "")")
    }

    // MARK: Connection

    private enum ControllerLinkState {
        case notPaired
        case connected(viaBluetooth: Bool)
        case offline
        case standby
    }

    private var link: ControllerLinkState {
        guard let controller = provisioning.savedController else { return .notPaired }
        if store.isDemo { return .standby }
        let reachable = store.snapshot.state != .offline && (store.hasLiveTelemetry || store.isControllerTest)
        return reachable ? .connected(viaBluetooth: controller.bluetoothPeripheralID != nil) : .offline
    }

    /// "SPARC-XXXX · " for the saved Pi, so two controllers with the same device name are told apart.
    private var controllerLabel: String {
        guard !store.isDemo, let name = provisioning.savedController?.bluetoothName else { return "" }
        return name + " · "
    }

    @ViewBuilder private var connectionRow: some View {
        switch link {
        case .notPaired:
            NavigationLink { ControllerSetupView() } label: {
                connectionLabel(title: "Connect a controller",
                                subtitle: "Pair your Raspberry Pi",
                                subtitleColor: SPARCTheme.muted, dot: nil, bluetooth: true)
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.connectController")
        case .connected(let viaBluetooth):
            let how = viaBluetooth ? "Bluetooth" : "your home network"
            NavigationLink { ConnectionView() } label: {
                connectionLabel(title: "Home controller",
                                subtitle: "\(controllerLabel)Connected · \(how)",
                                subtitleColor: SPARCTheme.mint, dot: SPARCTheme.spark, bluetooth: viaBluetooth)
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.connection")
        case .offline:
            NavigationLink { ConnectionView() } label: {
                connectionLabel(title: "Home controller",
                                subtitle: "\(controllerLabel)Not connected",
                                subtitleColor: SPARCTheme.warning, dot: SPARCTheme.warning, bluetooth: true)
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.connection")
        case .standby:
            NavigationLink { ConnectionView() } label: {
                connectionLabel(title: "Home controller",
                                subtitle: "\(controllerLabel)Paired · not in use",
                                subtitleColor: SPARCTheme.muted, dot: Color(white: 0.6), bluetooth: true)
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.connection")
        }
    }

    private func connectionLabel(title: String, subtitle: String, subtitleColor: Color, dot: Color?, bluetooth: Bool) -> some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .fill(dot == nil ? Color(white: 0.086) : SPARCTheme.spark.opacity(0.12))
                if bluetooth {
                    BluetoothRune()
                        .stroke(SPARCTheme.spark, style: StrokeStyle(lineWidth: 2.1, lineCap: .round, lineJoin: .round))
                        .frame(width: 20, height: 20)
                } else {
                    Image(systemName: "wifi").font(.system(size: 17, weight: .semibold)).foregroundStyle(SPARCTheme.spark)
                }
            }
            .frame(width: 42, height: 42)
            .overlay(alignment: .topTrailing) {
                if let dot {
                    Circle().fill(dot).frame(width: 10, height: 10)
                        .overlay(Circle().stroke(SPARCTheme.card, lineWidth: 2).frame(width: 12, height: 12))
                        .offset(x: 3, y: -3)
                }
            }
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).sparcFont(15.5, weight: .semibold)
                Text(subtitle).sparcFont(12.5).foregroundStyle(subtitleColor)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color(white: 0.43)).accessibilityHidden(true)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        .sparcSurface(radius: 22)
        .contentShape(Rectangle())
    }

    // MARK: Charging and privacy

    private var scheduleSubtitle: String {
        if store.sleepCalendarStatus?.enabled == true {
            return store.sleepCalendarStatus?.nextWake.map { "Calendar · wake at " + SPARCFormat.time($0) } ?? "Calendar · waiting for a sleep event"
        }
        if !store.isDemo, let campaign = store.campaign {
            return "\(campaign.nights) nights · \(campaign.targetBatteryPercent)% by \(SPARCFormat.time(SPARCFormat.clockDate(campaign.departureMinutes)))\(campaign.enabled ? "" : " · paused")"
        }
        let weekly = store.weekly
        guard weekly.enabled, !weekly.days.isEmpty else { return "Weekly routine is off" }
        let names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        let days: String
        switch weekly.days {
        case [1, 2, 3, 4, 5]: days = "Weekdays"
        case [6, 7]: days = "Weekends"
        case Set(1...7): days = "Every day"
        default: days = weekly.days.sorted().compactMap { names.indices.contains($0 - 1) ? names[$0 - 1] : nil }.joined(separator: ", ")
        }
        return "\(days) · \(weekly.targetPercent)% by \(SPARCFormat.time(SPARCFormat.clockDate(weekly.departureMinutes)))"
    }

    private var historySubtitle: String {
        if store.profileConnectionMode == .tesla {
            guard let history = store.selectedTeslaHistory else { return "No Tesla charges yet" }
            return history.sessions.isEmpty ? "No Tesla charges yet" : "\(history.sessions.count) charges\(store.teslaHistoryIsStale ? " · offline" : "")"
        }
        if store.combinedSavings != nil { return "Past 15 days" }
        if !store.isDemo {
            return store.historySessions.isEmpty ? "No sessions yet" : "\(store.historySessions.count) sessions\(store.historyIsStale ? " · offline" : "")"
        }
        let calendar = ScheduleEngine.chicagoCalendar
        let interval = calendar.dateInterval(of: .month, for: .now)
        let thisMonth = store.records.filter { interval?.contains($0.date) == true }.count
        func charges(_ count: Int) -> String { "\(count) \(count == 1 ? "charge" : "charges")" }
        if thisMonth > 0 { return "\(charges(thisMonth)) this month" }
        if !store.records.isEmpty { return "\(charges(store.records.count))" }
        return "No charges yet"
    }

    private var chargingConnectionsCard: some View {
        VStack(spacing: 0) {
            NavigationLink { SmartPlugSettingsView() } label: {
                menuRow(symbol: "powerplug", title: "Smart Plug", subtitle: store.connections?.smartPlug.address ?? "Shelly Plug US Gen4")
            }.buttonStyle(SPARCPressStyle()).accessibilityIdentifier("settings.smartPlug")
            divider
            NavigationLink { TeslaCarSettingsView() } label: {
                menuRow(symbol: "bolt.car", title: "Tesla", subtitle: teslaSubtitle)
            }.buttonStyle(SPARCPressStyle()).accessibilityIdentifier("settings.teslaCar")
            divider
            NavigationLink { VirtualCarSettingsView() } label: {
                menuRow(symbol: "car.side", title: "Virtual Car", subtitle: store.connections.map { $0.virtual.model.title } ?? "Model, battery, plug")
            }.buttonStyle(SPARCPressStyle()).accessibilityIdentifier("settings.virtualCar")
            if store.connections != nil, store.connectionsIsStale {
                divider
                Text("Last saved · connect to edit")
                    .sparcFont(12).foregroundStyle(SPARCTheme.warning)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(16)
            }
        }.sparcSurface(radius: 22).clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
    }

    private var teslaSubtitle: String {
        guard let tesla = store.connections?.tesla else { return "Connect your Tesla" }
        let accounts = store.teslaAccounts
        guard tesla.authorized else { return accounts.isEmpty ? "Connect your Tesla" : "Sign in to Tesla again" }
        if accounts.count > 1 {
            let cars = tesla.vehicles?.count ?? 0
            return "\(accounts.count) accounts · \(cars) car\(cars == 1 ? "" : "s")"
        }
        if let name = tesla.vehicleName, !name.isEmpty { return "Connected · \(name)" }
        return "Connected · pick your car"
    }

    private var chargingCard: some View {
        VStack(spacing: 0) {
            Group {
            if store.profileConnectionMode == .tesla {
                NavigationLink { TeslaManualChargingView() } label: {
                    menuRow(symbol: "bolt.car", title: "Tesla charging", subtitle: store.chargingCarName)
                }
            } else {
                NavigationLink { CurrentScheduleView() } label: {
                    menuRow(symbol: "calendar", title: "Charging schedule", subtitle: scheduleSubtitle)
                }
            }
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.schedule")
            divider
            if store.profileConnectionMode != .tesla {
                NavigationLink { AppleSleepCalendarView() } label: {
                    menuRow(symbol: "moon.zzz", title: "Sleep calendar event", subtitle: "Use a Calendar event")
                }
                .buttonStyle(SPARCPressStyle())
                divider
            }
            NavigationLink { RecordsView() } label: {
                menuRow(symbol: "clock", title: "History", subtitle: historySubtitle)
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.history")
            if store.profileConnectionMode != .tesla {
                divider
                NavigationLink { SavingsComparisonView() } label: {
                    menuRow(symbol: "chart.bar.xaxis", title: "Savings comparison", subtitle: savingsComparison.settingsSubtitle)
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityIdentifier("settings.savingsComparison")
            }
        }
        .sparcSurface(radius: 22)
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
    }

    private var privacyCard: some View {
        VStack(spacing: 0) {
            NavigationLink { PrivacyInformationView() } label: {
                menuRow(symbol: "shield", title: "Privacy & permissions", subtitle: "Local by design")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.privacy")
            divider
            Button { openSystemSettings() } label: {
                menuRow(symbol: "iphone", title: "iPhone permissions", subtitle: "Bluetooth and local network", trailing: "arrow.up.right")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.permissions")
        }
        .sparcSurface(radius: 22)
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
    }

    private var divider: some View { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }

    private func menuRow(symbol: String, title: String, subtitle: String, trailing: String = "chevron.right") -> some View {
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.system(size: 16, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark)
                .frame(width: 36, height: 36)
                .background(Color(white: 0.086), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).sparcFont(15, weight: .semibold)
                Text(subtitle).sparcFont(12.5).foregroundStyle(SPARCTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Image(systemName: trailing).font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color(white: 0.43)).accessibilityHidden(true)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        .contentShape(Rectangle())
    }

    // MARK: Developer

    private var developerSection: some View {
        VStack(spacing: 0) {
            sectionLabel("Developer")
            NavigationLink { VirtualCarSettingsView(preserveConnectionMode: true) } label: {
                menuRow(symbol: "slider.horizontal.3", title: "Developer settings", subtitle: "Virtual car and plug")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.developer")
            .sparcSurface(radius: 22)
        }
    }

    // MARK: Footer

    private var versionText: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = info?["CFBundleVersion"] as? String ?? "1"
        return "Version \(version) (\(build))"
    }

    private var footer: some View {
        VStack(spacing: 4) {
            Text("SPARC").sparcFont(15, weight: .bold).tracking(0.3)
            Text(versionText).sparcFont(13).foregroundStyle(SPARCTheme.tertiary)
                .padding(.horizontal, 16).frame(minHeight: 44)
            .accessibilityLabel(versionText.replacingOccurrences(of: " (", with: ", build ").replacingOccurrences(of: ")", with: ""))
            .accessibilityIdentifier("settings.version")
        }
        .frame(maxWidth: .infinity)
        .multilineTextAlignment(.center)
    }


}

private struct ConnectionSetupContext: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if store.connectionsIsStale {
                Label("Last saved · connect to edit", systemImage: "wifi.slash")
                    .sparcFont(13).foregroundStyle(SPARCTheme.warning)
            }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.top, 16)
    }
}

private struct ConnectionSetupError: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        if let error = store.errorMessage {
            Text(error).sparcFont(13).foregroundStyle(SPARCTheme.dangerText)
                .fixedSize(horizontal: false, vertical: true).padding(.top, 12)
        }
    }
}

private struct VirtualCarSettingsView: View {
    @EnvironmentObject private var store: AppStore
    var preserveConnectionMode = false
    @State private var model: VirtualVehicleModel = .cybertruck
    @State private var batteryPercent: Double?
    @State private var batteryEdited = false
    @State private var plugEdited = false
    @State private var pluggedIn = false
    @State private var original: ControllerConnections.Virtual?
    @State private var edited = false
    @State private var newerData = false
    @State private var saving = false
    @State private var saved = false
    private var battery: Binding<Double> {
        Binding(get: { batteryPercent ?? 0 }, set: { batteryPercent = $0; batteryEdited = true; edited = true; saved = false })
    }
    var body: some View {
        InsightsSettingsPage(bottomInset: 40) {
            insightsPageTitle("Virtual Car")
            if let current = store.connections?.virtual {
                Text("\(store.connectionsIsStale ? "Last recorded" : "Controller now"): \(current.model.title), \(SPARCFormat.decimal(current.batteryPercent))% · \(current.pluggedIn ? "plugged in" : "unplugged")")
                    .sparcFont(14, weight: .semibold).foregroundStyle(SPARCTheme.spark).padding(.top, 16)
                VStack(alignment: .leading, spacing: 20) {
                    Picker("Vehicle model", selection: Binding(get: { model }, set: { model = $0; edited = true; saved = false })) {
                        ForEach(VirtualVehicleModel.allCases) { Text($0.title).tag($0) }
                    }.pickerStyle(.segmented).accessibilityIdentifier("virtual.model")
                    if batteryPercent != nil {
                        HStack {
                            Text("Current battery")
                            Spacer(minLength: 12)
                            TextField("Percentage", value: battery, format: .number.precision(.fractionLength(0...2)))
                                .keyboardType(.decimalPad).multilineTextAlignment(.trailing).frame(width: 85)
                                .accessibilityIdentifier("virtual.batteryPercent")
                            Text("%")
                        }
                        Slider(value: battery, in: 0...100, step: 0.1).tint(SPARCTheme.spark)
                            .accessibilityLabel("Virtual battery percentage")
                        Toggle("Plugged in", isOn: Binding(get: { pluggedIn }, set: { pluggedIn = $0; plugEdited = true; edited = true; saved = false }))
                            .tint(SPARCTheme.spark).accessibilityIdentifier("virtual.pluggedIn")
                    }
                    if newerData {
                        Text("Car state changed. Your edits are kept.").foregroundStyle(SPARCTheme.warning)
                        Button("Reload current car state") { load() }.foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
                    }
                    SPARCActionButton(title: saved ? "Virtual setup saved" : "Save virtual setup", symbol: "checkmark", kind: .primary, height: 52, busy: store.busy) {
                        guard let settings = store.connections else { return }
                        let update = ConnectionSettingsUpdate(mode: preserveConnectionMode ? settings.mode : .virtual,
                            virtual: .init(model: model, batteryPercent: batteryEdited ? batteryPercent : nil, pluggedIn: plugEdited ? pluggedIn : nil), expectedRevision: settings.revision)
                        saving = true
                        Task {
                            await store.saveConnectionSettings(update)
                            saving = false; saved = store.errorMessage == nil
                            if saved { load() }
                        }
                    }.accessibilityIdentifier("virtual.save")
                }.sparcFont(14).padding(16).sparcSurface(radius: 22).padding(.top, 16)
                    .disabled(store.busy || store.connectionsIsStale)
            } else {
                Text("Connect to the Pi to edit.")
                    .sparcFont(14).foregroundStyle(SPARCTheme.muted).padding(.top, 16)
            }
            ConnectionSetupError()
        }.navigationBarTitleDisplayMode(.inline)
        .onAppear { if original == nil { load() } }
        .onChange(of: store.connections?.virtual) { _, value in
            guard !saving else { return }
            if !edited { load() } else if value != original { newerData = true }
        }
        .task { await store.refresh() }
    }
    private func load() {
        guard let state = store.connections?.virtual else { return }
        original = state; model = state.model; batteryPercent = state.batteryPercent; pluggedIn = state.pluggedIn
        edited = false; newerData = false; batteryEdited = false; plugEdited = false
    }
}

private struct SmartPlugSettingsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var address = ""
    @State private var password = ""
    @State private var vehicleSource: VehicleDataSource = .virtual
    @State private var originalAddress: String?
    @State private var originalSource: VehicleDataSource?
    @State private var edited = false
    @State private var newerData = false
    @State private var saving = false
    @State private var requestedPower: Bool?
    var body: some View {
        InsightsSettingsPage(bottomInset: 40) {
            HStack(spacing: 0) {
                insightsPageTitle("Smart Plug")
                SPARCInfoButton("About the smart plug") {
                    Text("Shelly Plug US Gen4. Enter its local IP address or .local name. Saving checks the plug without switching its outlet.")
                    Text("Follow virtual car switches the real outlet on while the virtual car charges and off when it stops.")
                    Text("If enabled, enter the plug’s local password to verify it.")
                }
                Spacer(minLength: 0)
            }
            ConnectionSetupContext()
            VStack(alignment: .leading, spacing: 18) {
                TextField("Local IP address or .local name", text: Binding(get: { address }, set: { address = $0; edited = true }))
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("smartPlug.address")
                SecureField("Local plug password (if enabled)", text: $password)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive()
                    .accessibilityIdentifier("smartPlug.password")
                Picker("Vehicle data", selection: Binding(get: { vehicleSource }, set: { vehicleSource = $0; edited = true })) {
                    ForEach(VehicleDataSource.allCases) { Text($0.title).tag($0) }
                }.pickerStyle(.segmented).accessibilityIdentifier("smartPlug.vehicleSource")
                if vehicleSource == .virtual {
                    NavigationLink("Edit virtual car") { VirtualCarSettingsView(preserveConnectionMode: true) }
                        .foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
                } else if store.connections?.tesla.authorized != true {
                    Text("Connect Tesla first (Settings › Tesla).").foregroundStyle(SPARCTheme.warning)
                }
                if newerData {
                    Text("Plug changed on the Pi. Your edits are kept.").foregroundStyle(SPARCTheme.warning)
                    Button("Reload current plug settings") { load() }.foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
                }
                SPARCActionButton(title: "Save and check plug", symbol: "powerplug", kind: .primary, height: 52, busy: store.busy) {
                    guard let settings = store.connections else { return }
                    let update = ConnectionSettingsUpdate(mode: .smartPlug,
                        smartPlug: .init(address: address.trimmingCharacters(in: .whitespacesAndNewlines), vehicleSource: vehicleSource), expectedRevision: settings.revision)
                    saving = true
                    let checkPassword = password.isEmpty ? nil : password
                    password = ""
                    Task {
                        await store.saveConnectionSettings(update)
                        if store.errorMessage == nil { await store.checkSmartPlug(password: checkPassword) }
                        saving = false
                        if store.errorMessage == nil { load() }
                    }
                }.accessibilityIdentifier("smartPlug.connect")
            }.sparcFont(14).padding(16).sparcSurface(radius: 22).padding(.top, 16)
                .disabled(store.busy || store.connectionsIsStale)
            if let plug = store.connections?.smartPlug {
                VStack(alignment: .leading, spacing: 10) {
                    Label(plug.verified == true ? "Connected" : plug.status.replacingOccurrences(of: "_", with: " ").capitalized,
                          systemImage: plug.verified == true ? "checkmark.circle.fill" : "powerplug")
                        .sparcFont(15, weight: .semibold)
                        .foregroundStyle(plug.verified == true ? SPARCTheme.spark : SPARCTheme.muted)
                    HStack {
                        Text(plug.switchOn.map { $0 ? "Outlet on" : "Outlet off" } ?? "Outlet unavailable")
                        Spacer()
                        if let watts = plug.powerW { Text("\(SPARCFormat.decimal(watts)) W").monospacedDigit() }
                    }
                    if let date = plug.lastCheckedAt { Text("Checked \(SPARCFormat.ago(date).lowercased())").foregroundStyle(SPARCTheme.muted) }
                    if let error = plug.error { Text(error).foregroundStyle(SPARCTheme.dangerText).fixedSize(horizontal: false, vertical: true) }
                    if plug.verified == true, plug.followVirtual != nil, store.profileConnectionMode == .virtual, let settings = store.connections, let plugAddress = plug.address {
                        Toggle(isOn: Binding(get: { plug.followVirtual == true }, set: { value in
                            Task {
                                await store.saveConnectionSettings(ConnectionSettingsUpdate(mode: settings.mode,
                                    smartPlug: .init(address: plugAddress, vehicleSource: .virtual, followVirtual: value), expectedRevision: settings.revision))
                            }
                        })) {
                            Text("Follow virtual car")
                        }.tint(SPARCTheme.spark).frame(minHeight: 44)
                            .disabled(store.busy || store.connectionsIsStale)
                            .accessibilityIdentifier("smartPlug.followVirtual")
                    }
                    if plug.verified == true, plug.followVirtual == nil, store.profileConnectionMode == .virtual {
                        Text("Update your Pi to enable virtual-car following.").foregroundStyle(SPARCTheme.warning)
                    }
                    if plug.verified == true, store.profileConnectionMode == .virtual, plug.followVirtual == true {
                        Text("Following the virtual car").foregroundStyle(SPARCTheme.muted)
                    } else {
                        HStack {
                            Button("Turn outlet on") { requestedPower = true }
                            Spacer()
                            Button("Turn outlet off") { requestedPower = false }
                        }.foregroundStyle(SPARCTheme.spark).frame(minHeight: 44)
                            .disabled(store.busy || store.connectionsIsStale || plug.verified != true)
                    }
                }.sparcFont(13).frame(maxWidth: .infinity, alignment: .leading).padding(16).sparcSurface(radius: 22).padding(.top, 16)
            }
            ConnectionSetupError()
        }.navigationBarTitleDisplayMode(.inline)
        .onAppear { if originalSource == nil { load() } }
        .onChange(of: store.connections?.smartPlug) { _, value in
            guard !saving else { return }
            if !edited { load() }
            else if value?.address != originalAddress || value?.vehicleSource != originalSource { newerData = true }
        }
        .confirmationDialog("Control the real Shelly outlet?", isPresented: Binding(get: { requestedPower != nil }, set: { if !$0 { requestedPower = nil } })) {
            Button(requestedPower == true ? "Turn real outlet on" : "Turn real outlet off") {
                guard let on = requestedPower else { return }; requestedPower = nil
                Task { await store.setSmartPlugPower(on: on) }
            }
            Button("Cancel", role: .cancel) { requestedPower = nil }
        } message: { Text("This switches the physical outlet.") }
        .task { await store.refresh() }
    }
    private func load() {
        guard let plug = store.connections?.smartPlug else { return }
        address = plug.address ?? ""; vehicleSource = plug.vehicleSource
        originalAddress = plug.address; originalSource = plug.vehicleSource; edited = false; newerData = false
    }
}

private struct TeslaCarSettingsView: View {
    var body: some View {
        TeslaConnectionSetupView()
            .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Privacy

/// Settings → Savings comparison: what "saved" is measured against.
private struct SavingsComparisonView: View {
    @AppStorage(SavingsComparison.storageKey) private var comparison: SavingsComparison = .fixedPlan
    var body: some View {
        InsightsSettingsPage(bottomInset: 40) {
            insightsPageTitle("Savings comparison")
            group("Compare SPARC with", [
                ("Fixed-price plan", !comparison.isHourly, { comparison = .fixedPlan }),
                ("Hourly pricing", comparison.isHourly, { if !comparison.isHourly { comparison = .hourlyAt6PM } })])
            if comparison.isHourly {
                group("Charging starts", [
                    ("6 PM", comparison == .hourlyAt6PM, { comparison = .hourlyAt6PM }),
                    ("At plug-in", comparison == .hourlyAtPlugIn, { comparison = .hourlyAtPlugIn })])
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .animation(.snappy, value: comparison)
    }
    private func group(_ title: String, _ items: [(String, Bool, () -> Void)]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title).sparcFont(12, weight: .semibold).tracking(1).textCase(.uppercase)
                .foregroundStyle(SPARCTheme.tertiary)
                .padding(.top, 22).padding(.bottom, 8).padding(.horizontal, 4)
            VStack(spacing: 0) {
                ForEach(items.indices, id: \.self) { index in
                    if index > 0 { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }
                    Button(action: items[index].2) {
                        HStack {
                            Text(items[index].0).sparcFont(15, weight: .semibold)
                            Spacer()
                            if items[index].1 {
                                Image(systemName: "checkmark").font(.system(size: 14, weight: .bold)).foregroundStyle(SPARCTheme.spark)
                            }
                        }
                        .padding(.horizontal, 16).frame(maxWidth: .infinity, minHeight: 52).contentShape(Rectangle())
                    }
                    .buttonStyle(SPARCPressStyle()).foregroundStyle(.white)
                    .accessibilityAddTraits(items[index].1 ? .isSelected : [])
                    .accessibilityIdentifier("savingsComparison." + items[index].0)
                }
            }
            .sparcSurface(radius: 22)
        }
    }
}

private struct PrivacyInformationView: View {
    var body: some View {
        InsightsSettingsPage(bottomInset: 40) {
            insightsPageTitle("Your home.\nYour data.")
            VStack(alignment: .leading, spacing: 0) {
                privacyItem("Bluetooth", "Find and pair your Pi.")
                privacyDivider
                privacyItem("Local network", "Reach your Pi over home Wi-Fi.")
                privacyDivider
                privacyItem("Pairing key", "Stored in the iPhone Keychain.")
                privacyDivider
                privacyItem("Optional Tesla account", "Credentials stay on your Pi.")
            }
            .sparcSurface(radius: 22)
            .padding(.top, 12)
        }
        .navigationBarTitleDisplayMode(.inline)
    }

    private var privacyDivider: some View { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }

    private func privacyItem(_ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).sparcFont(15, weight: .semibold)
            Text(text).sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

@MainActor func openSystemSettings() {
    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
    UIApplication.shared.open(url)
}

/// Everything about the paired Pi in one place, and where its Wi-Fi is set up.
struct ControllerStatusView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var manager: ProvisioningManager
    @State private var ssid = ""
    @State private var password = ""
    @State private var changingWiFi = false
    @FocusState private var passwordFocused: Bool

    private var status: ControllerStatus? { manager.controllerStatus }
    private var wifi: ControllerStatus.WiFi? { status?.wifi }

    var body: some View {
        InsightsSettingsPage(bottomInset: 40) {
            insightsPageTitle("Status")
            if let paired = manager.savedController {
                if let error = manager.statusError {
                    statusNote(error, symbol: "exclamationmark.circle", isError: true)
                        .accessibilityIdentifier("status.error")
                        .padding(.top, 16)
                }
                controllerCard(paired).padding(.top, 20)
                if paired.bluetoothPeripheralID != nil {
                    wifiCard.padding(.top, 20)
                } else {
                    statusNote("Wi-Fi setup needs a Bluetooth pairing.", symbol: "network")
                        .padding(.top, 12)
                }
                if !store.isDemo { chargingCard.padding(.top, 20) }
            } else {
                statusNote("No Pi connected.", symbol: "antenna.radiowaves.left.and.right")
                    .padding(.top, 16)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await manager.refreshStatus() }
        .task { await manager.refreshStatus() }
        .onDisappear { password = "" }
    }

    // MARK: Building blocks

    private func statusNote(_ text: String, symbol: String, isError: Bool = false) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol).font(.system(size: 15, weight: .semibold))
                .foregroundStyle(isError ? SPARCTheme.danger : SPARCTheme.spark)
                .frame(width: 20).accessibilityHidden(true)
            Text(text).sparcFont(13.5).foregroundStyle(isError ? SPARCTheme.dangerText : SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14).padding(.vertical, 13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(isError ? SPARCTheme.danger.opacity(0.08) : SPARCTheme.card,
                    in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
            .stroke(isError ? SPARCTheme.danger.opacity(0.35) : SPARCTheme.cardLine, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    private func sectionHeader(_ title: String, busy: Bool = false) -> some View {
        HStack(spacing: 10) {
            SPARCSectionTitle(title: title)
            if busy { ProgressView().tint(.white) }
        }
        .padding(.bottom, 10)
    }

    private func statusRow(_ title: String, _ value: String, dot: Color? = nil, divider: Bool = true) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(title).foregroundStyle(SPARCTheme.muted)
            Spacer(minLength: 8)
            HStack(spacing: 8) {
                if let dot { SPARCStatusDot(color: dot, size: 8) }
                Text(value).fontWeight(.semibold).monospacedDigit().multilineTextAlignment(.trailing)
            }
        }
        .sparcFont(14)
        .padding(.vertical, 13)
        .overlay(alignment: .bottom) {
            if divider { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Controller

    private var connectionValue: String {
        guard let paired = manager.savedController else { return "—" }
        if paired.bluetoothPeripheralID == nil { return "Home network" }
        return status != nil && manager.statusError == nil ? "Connected over Bluetooth" : "Bluetooth · not reached"
    }

    private var connectionDot: Color? {
        guard let paired = manager.savedController, paired.bluetoothPeripheralID != nil else { return nil }
        return status != nil && manager.statusError == nil ? SPARCTheme.spark : SPARCTheme.warning
    }

    private func controllerCard(_ paired: PairedController) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionHeader("Home controller", busy: manager.statusWorking)
            VStack(spacing: 0) {
                statusRow("Controller", paired.bluetoothName)
                statusRow("Connection", connectionValue, dot: connectionDot)
                statusRow("Bluetooth on Pi", status.map { $0.bluetooth ? "On" : "Off" } ?? "—")
                statusRow("Running for", status.map { SPARCFormat.duration($0.uptimeSeconds / 60) } ?? "—")
                statusRow("Pi temperature", status?.temperatureC.map { "\(Int($0.rounded()))°C" } ?? "—", divider: false)
            }
            .padding(.horizontal, 16)
            .sparcSurface(radius: 24)
        }
        .accessibilityIdentifier("status.controller")
    }

    // MARK: Wi-Fi

    private var wifiCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionHeader("Pi Wi-Fi")
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: wifi?.connected == false ? "wifi.slash" : "wifi")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(wifi?.connected == false ? SPARCTheme.warning : SPARCTheme.spark)
                        .frame(width: 36, height: 36)
                        .background(SPARCTheme.control, in: RoundedRectangle(cornerRadius: 11, style: .continuous))
                        .accessibilityHidden(true)
                    Text(wifiSummary).sparcFont(13.5).foregroundStyle(SPARCTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 2)
                        .accessibilityIdentifier("status.wifi")
                    Spacer(minLength: 0)
                }
                if let wifi {
                    if wifi.canSetUp && (!wifi.connected || changingWiFi) {
                        wifiForm
                    } else if wifi.canSetUp {
                        SPARCActionButton(title: "Change Wi-Fi network", symbol: "wifi", kind: .secondary,
                                          height: 52, busy: manager.statusWorking) {
                            changingWiFi = true
                            Task { await manager.scanWiFi() }
                        }
                        .accessibilityIdentifier("status.changeWiFi")
                    }
                }
                if let notice = manager.wifiNotice {
                    Text(notice).sparcFont(12.5).foregroundStyle(SPARCTheme.sparkText)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("status.wifiNotice")
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .sparcSurface(radius: 24)
        }
    }

    private var wifiSummary: String {
        guard let status else { return manager.statusWorking ? "Checking your Pi…" : "Status unavailable." }
        guard let wifi = status.wifi else { return "This Pi keeps its own network settings." }
        if wifi.connected { return wifi.ssid.map { "Connected to \($0)." } ?? "Connected to your network." }
        return "Your Pi isn't on Wi-Fi."
    }

    private var canJoin: Bool {
        !ssid.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !manager.statusWorking
    }

    private var wifiForm: some View {
        VStack(alignment: .leading, spacing: 12) {
            if manager.wifiNetworks.isEmpty {
                SPARCActionButton(title: "Find networks near your Pi", symbol: "antenna.radiowaves.left.and.right",
                                  kind: .secondary, height: 48, busy: manager.statusWorking) {
                    Task { await manager.scanWiFi() }
                }
                .accessibilityIdentifier("status.scan")
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(manager.wifiNetworks.enumerated()), id: \.element.id) { position, network in
                        if position > 0 { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }
                        Button { ssid = network.ssid; passwordFocused = true } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "wifi", variableValue: Double(network.strength) / 100)
                                    .foregroundStyle(SPARCTheme.spark).accessibilityHidden(true)
                                Text(network.ssid).sparcFont(15, weight: .medium).lineLimit(1)
                                Spacer(minLength: 8)
                                if ssid == network.ssid {
                                    Image(systemName: "checkmark").font(.system(size: 13, weight: .bold))
                                        .foregroundStyle(SPARCTheme.spark).accessibilityHidden(true)
                                }
                            }
                            .padding(.horizontal, 14)
                            .frame(minHeight: 46)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(SPARCPressStyle())
                        .foregroundStyle(.white)
                        .accessibilityAddTraits(ssid == network.ssid ? .isSelected : [])
                    }
                }
                .background(SPARCTheme.control, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(SPARCTheme.controlLine, lineWidth: 1))
            }
            TextField("Network name", text: $ssid)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .sparcFont(15)
                .padding(.horizontal, 14).frame(minHeight: 50)
                .background(SPARCTheme.control, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(SPARCTheme.controlLine, lineWidth: 1))
                .accessibilityIdentifier("status.ssid")
            SecureField("Wi-Fi password", text: $password)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .sparcFont(15)
                .padding(.horizontal, 14).frame(minHeight: 50)
                .background(SPARCTheme.control, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(SPARCTheme.controlLine, lineWidth: 1))
                .focused($passwordFocused)
                .accessibilityIdentifier("status.password")
            SPARCActionButton(title: "Connect Pi to Wi-Fi", symbol: "wifi", kind: .primary,
                              height: 52, busy: manager.statusWorking) {
                passwordFocused = false
                let network = ssid.trimmingCharacters(in: .whitespacesAndNewlines)
                let secret = password
                password = ""
                Task { if await manager.configureWiFi(ssid: network, password: secret) { changingWiFi = false } }
            }
            .disabled(!canJoin)
            .opacity(canJoin ? 1 : 0.45)
            .accessibilityIdentifier("status.joinWiFi")
        }
    }

    // MARK: Charging

    private var chargingDot: Color {
        switch store.snapshot.state {
        case .charging: return SPARCTheme.spark
        case .offline: return SPARCTheme.warning
        case .waiting, .paused, .complete, .unplugged: return Color(white: 0.6)
        }
    }

    private var chargingCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionHeader("Charging")
            VStack(spacing: 0) {
                statusRow("Now", store.snapshot.state.displayTitle, dot: chargingDot)
                statusRow("Battery", store.showsTelemetry ? "\(store.snapshot.batteryPercent)%" : "—")
                statusRow("Power", store.showsTelemetry ? "\(SPARCFormat.decimal(store.snapshot.powerKW)) kW" : "—")
                statusRow("Target", store.displayedTarget)
                statusRow("Ready by", store.displayedReadyTime, divider: false)
            }
            .padding(.horizontal, 16)
            .sparcSurface(radius: 24)
        }
        .accessibilityIdentifier("status.charging")
    }
}
