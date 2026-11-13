import SwiftUI
import Charts

/// Savings at a glance: one amount, with the math one tap away.
struct CombinedSavingsContent: View {
    @EnvironmentObject private var store: AppStore
    @AppStorage(SavingsComparison.storageKey) private var comparison: SavingsComparison = .fixedPlan
    @State private var loading = true
    var body: some View {
        Group {
            if let report = store.combinedSavings {
                SavingsHero(report: report, comparison: comparison)
            } else if loading {
                ProgressView().tint(SPARCTheme.spark).frame(maxWidth: .infinity, minHeight: 180)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Couldn't load savings").sparcFont(17, weight: .semibold)
                    Button("Try again") { Task { await load() } }.foregroundStyle(SPARCTheme.spark)
                }
            }
        }
        .task { await load() }
    }
    @MainActor private func load() async {
        loading = true
        await store.refreshCombinedSavings()
        loading = false
    }
}

private func savingsMoney(_ amount: Double?) -> String { amount.map(SPARCFormat.money) ?? "—" }

extension SPARCFormat {
    /// "67%": the size of a saving or an extra cost, without its sign.
    static func percent(_ value: Double) -> String { "\(Int(abs(value).rounded()))%" }
}

/// "Oct 28 – Nov 11" from the report's Chicago dates.
private func savingsRange(_ report: CombinedSavingsReport) -> String {
    func short(_ day: String) -> String {
        guard let date = CampaignUpdate.date(day) else { return day }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US"); formatter.timeZone = SPARCFormat.zone
        formatter.dateFormat = "MMM d"
        return formatter.string(from: date)
    }
    return "\(short(report.start)) – \(short(report.end))"
}

private struct SavingsHero: View {
    let report: CombinedSavingsReport
    let comparison: SavingsComparison
    private var saved: Double? { report.totals(comparison)?.savedPercent }
    private var extraCost: Bool { (saved ?? 0) < 0 }
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(comparison.isHourly ? "\(savingsRange(report)) · vs hourly" : savingsRange(report))
                    .sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                Spacer()
                SPARCInfoButton("How it's calculated") { SavingsMath(report: report, comparison: comparison) }
                    .padding(.trailing, -10)
                    .accessibilityIdentifier("savings.info")
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(saved.map(SPARCFormat.percent) ?? "—")
                    .sparcFont(60, weight: .bold).tracking(-1.8).monospacedDigit()
                    .foregroundStyle(saved == nil ? SPARCTheme.muted : (extraCost ? SPARCTheme.warning : SPARCTheme.spark))
                    .lineLimit(1).minimumScaleFactor(0.5)
                    .accessibilityIdentifier("savings.total")
                if saved != nil {
                    Text(extraCost ? "more" : "saved").sparcFont(19, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                }
            }
            .accessibilityElement(children: .combine)
            NightBars(nights: report.nights, comparison: comparison).padding(.top, 18)
        }
        .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 20)
        .sparcSurface(radius: 26)
    }
}

/// Each night's savings as a bar. No labels: the amount above says it all.
private struct NightBars: View {
    let nights: [CombinedSavingsReport.Night]
    let comparison: SavingsComparison
    var body: some View {
        Chart(nights) { night in
            let value = night.saved(comparison) ?? 0
            BarMark(x: .value("Night", night.date), y: .value("Saved", value))
                .foregroundStyle(value < 0 ? SPARCTheme.warning : SPARCTheme.spark)
                .cornerRadius(3)
        }
        .chartXAxis(.hidden).chartYAxis(.hidden)
        .frame(height: 64)
        .accessibilityHidden(true)
    }
}

private struct SavingsMath: View {
    let report: CombinedSavingsReport
    let comparison: SavingsComparison
    var body: some View {
        if let total = report.totals(comparison) {
            VStack(spacing: 0) {
                SPARCInfoRow(comparison.baselineTitle, savingsMoney(total.baselineUSD))
                Divider().overlay(SPARCTheme.controlLine)
                SPARCInfoRow("With SPARC", savingsMoney(total.systemUSD))
            }
            .padding(.horizontal, 16).sparcSurface(radius: 18)
            VStack(spacing: 0) {
                ForEach(Array(zip(comparison.partTitles, total.parts)), id: \.0) { title, amount in
                    SPARCInfoRow(title, savingsMoney(amount))
                    Divider().overlay(SPARCTheme.controlLine)
                }
                SPARCInfoRow("Saved", savingsMoney(total.savedUSD), emphasized: true)
            }
            .padding(.horizontal, 16).sparcSurface(radius: 18)
            Text(note(total)).sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Text("No prices for this comparison yet.").sparcFont(15).foregroundStyle(SPARCTheme.muted)
        }
    }
    private func note(_ total: CombinedSavingsReport.Comparison) -> String {
        let nights = "\(total.nightCount) \(total.nightCount == 1 ? "night" : "nights")"
        var text = report.kind == "simulation" ? "Historical replay using ComEd prices. " : ""
        text += "Same charging both ways: \(SPARCFormat.decimal(total.energyKWh)) kWh over \(nights), \(savingsRange(report)). "
        switch comparison {
        case .fixedPlan:
            text += "The fixed plan is \(SPARCFormat.decimal(report.fixedSupplyCents, digits: 3))¢/kWh plus standard delivery; "
                + "SPARC uses ComEd's hourly price and Time-of-Day delivery (single-family, electric heat)."
        case .hourlyAt6PM, .hourlyAtPlugIn:
            text += "Without SPARC, charging starts right away \(comparison == .hourlyAt6PM ? "at 6 PM" : "at plug-in") "
                + "on ComEd's hourly price with standard delivery. SPARC uses Time-of-Day delivery and picks the cheapest hours."
        }
        text += " Taxes and fixed fees aren't included."
        if total.unpricedNightCount > 0 {
            let one = total.unpricedNightCount == 1
            text += " \(total.unpricedNightCount) \(one ? "night" : "nights") without prices \(one ? "isn't" : "aren't") counted."
        }
        return text
    }
}
