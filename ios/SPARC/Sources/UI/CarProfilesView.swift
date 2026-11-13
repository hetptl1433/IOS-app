import SwiftUI

struct CarProfilesView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Your cars").sparcFont(30, weight: .bold)
                ChargingCarSwitcher()
                if store.connectionsIsStale {
                    Button("Reconnect to your Pi") { Task { await store.refresh() } }.foregroundStyle(SPARCTheme.spark)
                }
            }.padding(16)
        }.background(SPARCTheme.background).navigationTitle("Car profiles")
    }
}

/// The virtual car's nightly charging, newest first.
struct VirtualProfileHistory: View {
    @EnvironmentObject private var store: AppStore
    @State private var selected: CombinedSavingsReport.Night?
    @AppStorage(SavingsComparison.storageKey) private var comparison: SavingsComparison = .fixedPlan
    var body: some View {
        if let report = store.combinedSavings {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(report.nights.reversed()) { night in
                    Button { selected = night } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(Self.day(night.date)).sparcFont(16, weight: .semibold)
                                Text("\(SPARCFormat.decimal(night.energyKWh)) kWh").sparcFont(12).monospacedDigit()
                                    .foregroundStyle(SPARCTheme.muted)
                            }
                            Spacer()
                            let saved = night.savedPercent(comparison)
                            Text(Self.saved(saved))
                                .sparcFont(17, weight: .semibold).monospacedDigit()
                                .foregroundStyle(saved == nil ? SPARCTheme.muted : ((saved ?? 0) < 0 ? SPARCTheme.warning : SPARCTheme.spark))
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(SPARCTheme.muted)
                        }.padding(16).sparcSurface(radius: 20)
                    }.buttonStyle(SPARCPressStyle()).foregroundStyle(.white)
                }
            }
            .sheet(item: $selected) { night in
                NavigationStack {
                    Form {
                        Section {
                            LabeledContent("Energy", value: "\(SPARCFormat.decimal(night.energyKWh)) kWh")
                            LabeledContent("Battery", value: "20% → 80%")
                            if night.simulated { LabeledContent("Source", value: "Historical replay") }
                            let costs = night.costs(comparison)
                            LabeledContent(comparison.baselineTitle, value: costs.map { SPARCFormat.money($0.baseline) } ?? "Unavailable")
                            LabeledContent("With SPARC", value: night.systemCostUSD.map(SPARCFormat.money) ?? "Unavailable")
                            LabeledContent("Saved", value: night.saved(comparison).map(SPARCFormat.money) ?? "Unavailable")
                        }
                        if let note = night.note { Section { Text(note).foregroundStyle(SPARCTheme.muted) } }
                    }.navigationTitle(Self.day(night.date))
                        .navigationBarTitleDisplayMode(.inline)
                        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selected = nil } } }
                }
                .presentationDetents([.medium, .large])
                .preferredColorScheme(.dark)
            }
        }
    }
    /// "Tue, Nov 11" from a Chicago calendar date.
    static func day(_ value: String) -> String {
        guard let date = CampaignUpdate.date(value) else { return value }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US"); formatter.timeZone = SPARCFormat.zone
        formatter.dateFormat = "EEE, MMM d"
        return formatter.string(from: date)
    }
    /// "71% saved" or "4% more" from a night's percentage.
    static func saved(_ percent: Double?) -> String {
        guard let percent else { return "—" }
        return "\(SPARCFormat.percent(percent)) \(percent < 0 ? "more" : "saved")"
    }
}
