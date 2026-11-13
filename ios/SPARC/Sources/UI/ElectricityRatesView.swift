import SwiftUI
import Charts

struct RatePageButton<Label: View>: View {
    @ViewBuilder var label: Label
    @State private var presented = false
    var body: some View {
        Button { presented = true } label: { label }
            .fullScreenCover(isPresented: $presented) {
                NavigationStack { PricesView() }
                    .preferredColorScheme(.dark).tint(SPARCTheme.spark)
            }
    }
}

struct ElectricityRatesView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var selectedDay = Date()
    @State private var page: ControllerRatePage?
    @State private var message: String?
    @State private var loading = false
    @State private var historyHeight: CGFloat = 0
    @State private var series = "Combined"
    private var day: String { ComEdDeliveryClass.day(selectedDay) }
    private var supply: Double? {
        if let current = store.currentSupplyRate { return current }
        guard let observed = page?.observedAt, (-300...1200).contains(Date().timeIntervalSince(observed)) else { return nil }
        return page?.currentSupplyCents
    }
    private var delivery: Double? { store.deliveryClass.cents(at: Date()) }
    private var combined: Double? { supply.flatMap { s in delivery.map { s + $0 } } }
    private var days: ClosedRange<Date> {
        let today = ComEdDeliveryClass.calendar.startOfDay(for: Date())
        return ComEdDeliveryClass.calendar.date(byAdding: .day, value: -7, to: today)!...Date()
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 0) {
                    Text("Rate details").sparcFont(32, weight: .bold).accessibilityAddTraits(.isHeader)
                    SPARCInfoButton("About these rates") {
                        Text("ComEd hourly supply + Time-of-Day delivery. Other supply charges, capacity, IEDT, taxes and monthly fees are additional.")
                        Text("Choose the delivery class shown on your ComEd bill. Rates are for the current hour in Chicago time.")
                        Text("History: up to 7 days kept on your Pi. Blank rates were not reported. Partial-hour and day-ahead values are estimates and can differ once settled; future estimates are lighter in the chart.")
                        Link("ComEd tariff and adjustments", destination: ComEdDeliveryClass.sourceURL)
                            .foregroundStyle(SPARCTheme.spark)
                        Text("Verified November 12, 2026 for November–December billing. Match the delivery class and Time-of-Day enrollment to your bill.")
                            .foregroundStyle(SPARCTheme.muted)
                    }
                    Spacer(minLength: 0)
                }
                currentCard
                deliveryCard
                historyCard
            }.padding(16).frame(maxWidth: 640).frame(maxWidth: .infinity)
        }
        .background(SPARCTheme.background).foregroundStyle(.white)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() }.accessibilityIdentifier("rates.done") } }
        .task(id: day) { await load() }
        .refreshable { await load() }
    }
    private var currentCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text("SUPPLY + DELIVERY NOW").sparcFont(11, weight: .bold).foregroundStyle(SPARCTheme.muted); Spacer(); Text(ComEdDeliveryClass.period(at: Date())).sparcFont(12).foregroundStyle(SPARCTheme.spark) }
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(rate(combined)).sparcFont(54, weight: .bold).monospacedDigit().minimumScaleFactor(0.6)
                Text("¢/kWh").sparcFont(16).foregroundStyle(SPARCTheme.muted)
            }.accessibilityIdentifier("rates.total")
            Divider().overlay(SPARCTheme.line)
            component("Hourly supply", value: supply)
            component("Time-of-Day delivery", value: delivery)
            if supply == nil { Text("Connect to your Pi for live supply.").sparcFont(12).foregroundStyle(SPARCTheme.warning) }
        }.padding(18).sparcSurface(radius: 24)
    }
    private var deliveryCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your delivery rate").sparcFont(17, weight: .semibold)
            Picker("Delivery class", selection: $store.deliveryClass) {
                ForEach(ComEdDeliveryClass.allCases) { value in Text(value.title).tag(value) }
            }.pickerStyle(.menu).tint(SPARCTheme.spark).accessibilityIdentifier("rates.deliveryClass")
            ForEach([("Overnight",0,"9pm–6am"),("Morning",6,"6am–1pm"),("Mid-day peak",13,"1–7pm"),("Evening",19,"7–9pm")], id: \.0) { item in
                let date = ComEdDeliveryClass.calendar.date(bySettingHour: item.1, minute: 0, second: 0, of: Date())!
                HStack { VStack(alignment: .leading) { Text(item.0).sparcFont(14); Text(item.2).sparcFont(11).foregroundStyle(SPARCTheme.muted) }; Spacer(); Text("\(rate(store.deliveryClass.cents(at: date)))¢").sparcFont(16, weight: .semibold).monospacedDigit() }
            }
        }.padding(18).sparcSurface(radius: 24)
    }
    private var historyCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Rate history").sparcFont(21, weight: .bold).accessibilityAddTraits(.isHeader)
            DatePicker("Day", selection: $selectedDay, in: days, displayedComponents: .date)
                .environment(\.timeZone, TimeZone(identifier: "America/Chicago")!).tint(SPARCTheme.spark)
                .accessibilityIdentifier("rates.day")
            if loading { ProgressView("Loading hourly rates…").tint(SPARCTheme.spark) }
            if let message { Text(message).sparcFont(13).foregroundStyle(SPARCTheme.warning); Button("Try again") { Task { await load() } }.foregroundStyle(SPARCTheme.spark) }
            if let page, page.day == day {
                Picker("Chart", selection: $series) { ForEach(["Combined","Supply","Delivery"], id: \.self) { Text($0) } }.pickerStyle(.segmented)
                Chart(page.hours) { hour in
                    if let value = chartRate(hour) {
                        BarMark(x: .value("Hour",hour.start,unit: .hour), y: .value("Cents per kWh",value))
                            .foregroundStyle(hour.status == "dayAhead" ? SPARCTheme.spark.opacity(0.35) : SPARCTheme.spark)
                    }
                    RuleMark(y: .value("Zero",0)).foregroundStyle(SPARCTheme.muted.opacity(0.3))
                }
                .chartXAxis { AxisMarks(values: .stride(by: .hour, count: 6)) { value in AxisGridLine(); AxisValueLabel { if let date = value.as(Date.self) { Text(hourText(date)) } } } }
                .chartYAxisLabel("¢/kWh").frame(height: 190).accessibilityLabel("\(series) rates for \(day). Hourly values listed below.")
                HStack { Text("Hour"); Spacer(); Text("Supply").frame(width: 64, alignment: .trailing); Text("Delivery").frame(width: 64, alignment: .trailing); Text("Combined").frame(width: 76, alignment: .trailing) }
                    .sparcFont(11, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                ForEach(page.hours) { hour in
                    VStack(spacing: 8) {
                        HStack(alignment: .top, spacing: 4) {
                            VStack(alignment: .leading, spacing: 3) { Text(hourText(hour.start)).sparcFont(12, weight: .semibold); Text(hour.statusTitle).sparcFont(9).foregroundStyle(SPARCTheme.muted) }
                            Spacer(minLength: 0)
                            Text(rate(hour.supplyCents)).frame(width: 64, alignment: .trailing)
                            Text(rate(store.deliveryClass.cents(at: hour.start))).frame(width: 64, alignment: .trailing)
                            Text(rate(total(hour))).foregroundStyle(SPARCTheme.spark).frame(width: 76, alignment: .trailing)
                        }.sparcFont(12).monospacedDigit().accessibilityElement(children: .combine)
                        Rectangle().fill(SPARCTheme.line).frame(height: 1)
                    }
                }
            }
        }
        .frame(minHeight: loading && historyHeight > 0 ? historyHeight : nil, alignment: .topLeading)
        .padding(18).sparcSurface(radius: 24)
        .onGeometryChange(for: CGFloat.self) { $0.size.height - 36 } action: { value in
            if !loading { historyHeight = value }
        }
    }
    private func component(_ title: String, value: Double?) -> some View {
        HStack { Text(title).sparcFont(14).foregroundStyle(SPARCTheme.muted); Spacer(); Text("\(rate(value))¢").sparcFont(17, weight: .semibold).monospacedDigit() }
    }
    private func total(_ hour: ControllerRatePage.Hour) -> Double? { hour.supplyCents.flatMap { s in store.deliveryClass.cents(at: hour.start).map { s + $0 } } }
    private func chartRate(_ hour: ControllerRatePage.Hour) -> Double? {
        switch series { case "Supply": return hour.supplyCents; case "Delivery": return store.deliveryClass.cents(at: hour.start); default: return total(hour) }
    }
    private func rate(_ value: Double?) -> String { value.map { SPARCFormat.decimal($0,digits: 3) } ?? "—" }
    private func hourText(_ date: Date) -> String {
        let formatter = DateFormatter(); formatter.timeZone = TimeZone(identifier: "America/Chicago"); formatter.dateFormat = "ha z"; return formatter.string(from: date)
    }
    @MainActor private func load() async {
        let requested = day
        loading = true; message = nil; page = nil
        defer { if day == requested { loading = false } }
        do {
            let value = try await store.rateHistory(day: requested)
            guard !Task.isCancelled, day == requested else { return }
            page = value
        } catch {
            guard !Task.isCancelled, day == requested else { return }
            message = "Hourly history unavailable."
        }
    }
}
