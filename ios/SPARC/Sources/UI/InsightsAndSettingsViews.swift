import SwiftUI
import Charts
import UIKit

struct InsightsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var period: InsightPeriod = .month
    @State private var metric: InsightMetric = .savings

    private var records: [ChargingRecord] { store.records.filter { $0.date >= period.start }.sorted { $0.date < $1.date } }
    private var cost: Double { records.reduce(0) { $0 + $1.cost } }
    private var savings: Double { records.reduce(0) { $0 + $1.savings } }
    private var energy: Double { records.reduce(0) { $0 + $1.energyKWh } }
    private var total: Double { switch metric { case .savings: return savings; case .cost: return cost; case .energy: return energy } }

    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Your good-energy story.")
            Picker("Period", selection: $period) {
                ForEach(InsightPeriod.allCases) { value in Text(value.rawValue).tag(value) }
            }.pickerStyle(.segmented).accessibilityIdentifier("insights.period")
            Picker("Metric", selection: $metric) {
                ForEach(InsightMetric.allCases) { value in Text(value.rawValue).tag(value) }
            }.pickerStyle(.segmented).accessibilityIdentifier("insights.metric")
            if records.isEmpty {
                SPARCCard {
                    VStack(alignment: .leading, spacing: 14) {
                        Image(systemName: "chart.xyaxis.line").sparcFont(30).foregroundStyle(SPARCTheme.green)
                        Text("Your story starts with a charge.").font(.title3.weight(.semibold))
                        Text("Completed sessions from your controller will appear here when session history is available.")
                            .sparcFont(14).foregroundStyle(SPARCTheme.muted)
                    }.padding(.vertical, 12)
                }
            } else {
                Text("\(metric == .savings ? "Modeled savings" : metric == .cost ? "Charging cost" : "Meter energy") · selected sessions")
                    .sparcFont(12).foregroundStyle(SPARCTheme.muted)
                Text(metric == .energy ? "\(SPARCFormat.decimal(total, digits: 0)) kWh" : SPARCFormat.money(total))
                    .sparcFont(56, weight: .semibold).tracking(-2).foregroundStyle(SPARCTheme.green)
                    .lineLimit(1).minimumScaleFactor(0.5)
                Text("\(records.count) \(store.isDemo ? "sample" : "completed") sessions · \(period.rawValue.lowercased()) to date")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                SPARCCard {
                    Chart(records) { record in
                        BarMark(x: .value("Date", record.date, unit: .day), y: .value(metric.rawValue, chartValue(record)))
                            .foregroundStyle(SPARCTheme.green)
                            .cornerRadius(4)
                    }
                    .frame(height: 165)
                    .chartXAxis { AxisMarks(values: .automatic(desiredCount: 5)) { _ in AxisValueLabel(format: .dateTime.day()) } }
                    .chartYAxis { AxisMarks(position: .leading) }
                    .accessibilityLabel("\(metric.rawValue) for \(records.count) selected sessions, total \(total)")
                }
                SPARCCard {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("A little perspective.").sparcFont(16, weight: .semibold).padding(.bottom, 8)
                        SPARCDetail(title: "Charging immediately", value: SPARCFormat.money(cost + savings))
                        Divider().overlay(SPARCTheme.line)
                        SPARCDetail(title: "With Sparc", value: SPARCFormat.money(cost))
                        Divider().overlay(SPARCTheme.line)
                        SPARCDetail(title: "Same-energy difference", value: "\(SPARCFormat.money(savings)) less")
                    }
                }
                HStack(spacing: 12) {
                    smallMetric("Session energy", "\(SPARCFormat.decimal(energy, digits: 0)) kWh")
                    smallMetric("Effective rate", energy > 0 ? "\(SPARCFormat.decimal(cost / energy * 100))¢/kWh" : "—")
                }
                Text("Savings compare the same energy with charging immediately. These modeled comparisons exclude taxes and fixed charges. Sample-session totals can differ from the monthly estimate on Home.")
                    .sparcFont(11).foregroundStyle(SPARCTheme.muted)
            }
            if store.isDemo { SPARCNotice(title: "Demo mode · sample charging history and estimates.", symbol: "sparkles") }
        }
    }
    private func chartValue(_ record: ChargingRecord) -> Double {
        switch metric { case .savings: return record.savings; case .cost: return record.cost; case .energy: return record.energyKWh }
    }
    private func smallMetric(_ label: String, _ value: String) -> some View {
        SPARCCard(padding: 15) {
            VStack(alignment: .leading, spacing: 10) {
                Text(label).sparcFont(11).foregroundStyle(SPARCTheme.muted)
                Text(value).sparcFont(21, weight: .semibold).foregroundStyle(SPARCTheme.green).lineLimit(1).minimumScaleFactor(0.6)
            }
        }
    }
}

private enum InsightPeriod: String, CaseIterable, Identifiable {
    case week = "Week", month = "Month", year = "Year"
    var id: String { rawValue }
    var start: Date {
        let component: Calendar.Component = self == .week ? .weekOfYear : self == .month ? .month : .year
        return ScheduleEngine.chicagoCalendar.dateInterval(of: component, for: .now)?.start ?? .distantPast
    }
}
private enum InsightMetric: String, CaseIterable, Identifiable {
    case savings = "Savings", cost = "Cost", energy = "Energy"
    var id: String { rawValue }
}

struct RecordsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var selectedRecord: ChargingRecord?
    private var records: [ChargingRecord] { store.records.sorted { $0.date > $1.date } }
    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Every charge,\naccounted for.")
            if records.isEmpty {
                SPARCCard {
                    VStack(alignment: .leading, spacing: 15) {
                        Image(systemName: "clock.arrow.circlepath").sparcFont(32).foregroundStyle(SPARCTheme.green)
                        Text("Good things take a first charge.").font(.title3.weight(.semibold))
                        Text("Your completed charging sessions will appear here when the controller supports session history.")
                            .sparcFont(14).foregroundStyle(SPARCTheme.muted)
                    }.padding(.vertical, 14)
                }
            } else {
                HStack(spacing: 12) {
                    recordTotal("Session energy", "\(SPARCFormat.decimal(records.reduce(0) { $0 + $1.energyKWh }, digits: 0)) kWh")
                    recordTotal("Session cost", SPARCFormat.money(records.reduce(0) { $0 + $1.cost }))
                }
                Text("\(records.count) \(store.isDemo ? "sample" : "completed") records shown").sparcFont(12).foregroundStyle(SPARCTheme.muted)
                SPARCCard(padding: 0) {
                    VStack(spacing: 0) {
                        ForEach(Array(records.enumerated()), id: \.element.id) { index, record in
                            if index > 0 { Divider().overlay(SPARCTheme.line).padding(.horizontal, 16) }
                            Button { selectedRecord = record } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: "moon.stars").foregroundStyle(SPARCTheme.green)
                                    VStack(alignment: .leading, spacing: 7) {
                                        Text(record.date, format: .dateTime.weekday(.abbreviated).month(.abbreviated).day())
                                            .sparcFont(14, weight: .medium)
                                        Text("\(SPARCFormat.decimal(record.energyKWh)) kWh · Smart charging")
                                            .sparcFont(11).foregroundStyle(SPARCTheme.muted)
                                    }
                                    Spacer(minLength: 0)
                                    VStack(alignment: .trailing, spacing: 7) {
                                        Text(SPARCFormat.money(record.cost)).sparcFont(14, weight: .semibold)
                                        Text("\(SPARCFormat.money(record.savings)) saved").sparcFont(10).foregroundStyle(SPARCTheme.green)
                                    }
                                    Image(systemName: "chevron.right").sparcFont(10).foregroundStyle(SPARCTheme.muted)
                                }
                                .foregroundStyle(.white)
                                .padding(17)
                            }
                            .buttonStyle(SPARCPressStyle())
                            .accessibilityIdentifier("records.session.\(index)")
                        }
                    }
                }
            }
            if store.isDemo { SPARCNotice(title: "Sample records for exploring the app. No sessions have been recorded from a vehicle.", symbol: "sparkles") }
        }
        .sheet(item: $selectedRecord) { record in
            NavigationStack {
                SessionDetailView(record: record)
                    .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selectedRecord = nil } } }
            }.preferredColorScheme(.dark).tint(.white)
        }
    }
    private func recordTotal(_ label: String, _ value: String) -> some View {
        SPARCCard(padding: 15) {
            VStack(alignment: .leading, spacing: 10) {
                Text(label).sparcFont(11).foregroundStyle(SPARCTheme.muted)
                Text(value).sparcFont(23, weight: .semibold).foregroundStyle(SPARCTheme.green).lineLimit(1).minimumScaleFactor(0.6)
            }
        }
    }
}

private struct SessionDetailView: View {
    @EnvironmentObject private var store: AppStore
    let record: ChargingRecord
    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "A smart little finish.", subtitle: record.date.formatted(date: .abbreviated, time: .omitted))
            Text(SPARCFormat.money(record.savings)).sparcFont(62, weight: .semibold).foregroundStyle(SPARCTheme.green).tracking(-2)
            Text("Modeled savings on this charge").sparcFont(14).foregroundStyle(SPARCTheme.muted)
            SPARCCard {
                VStack(spacing: 2) {
                    SPARCDetail(title: "Energy at the meter", value: "\(SPARCFormat.decimal(record.energyKWh)) kWh")
                    Divider().overlay(SPARCTheme.line)
                    SPARCDetail(title: "Charging cost", value: SPARCFormat.money(record.cost))
                    Divider().overlay(SPARCTheme.line)
                    SPARCDetail(title: "Immediate-charge comparison", value: SPARCFormat.money(record.cost + record.savings))
                }
            }
            SPARCNotice(title: "The comparison uses the same energy at an immediate-charge rate. It is an estimate, not a bill credit.", symbol: "leaf")
            if store.isDemo { Text("Sample session").sparcFont(12).foregroundStyle(SPARCTheme.muted) }
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var provisioning: ProvisioningManager
    @State private var confirmForget = false

    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Make Sparc yours.", subtitle: "A little control. Right where you need it.")
            if let message = store.errorMessage ?? store.connectionMessage { SPARCNotice(title: message, symbol: "exclamationmark.circle") }
            SPARCCard {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Image(systemName: "sparkles").foregroundStyle(SPARCTheme.green)
                        Text(store.isDemo ? "You're exploring the demo" : store.isControllerTest ? "Your controller · app test mode" : "Your home controller").sparcFont(16, weight: .semibold)
                    }
                    Text(store.isDemo ? "Explore charging here, or pair your Raspberry Pi to control it over Bluetooth." : (store.controllerName ?? "No home controller paired"))
                        .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                    Toggle("Demo mode", isOn: Binding(get: { store.isDemo }, set: { store.setDemoMode($0) }))
                        .sparcFont(14).tint(.gray)
                        .accessibilityIdentifier("settings.demoMode")
                        .disabled(store.busy)
                    Divider().overlay(SPARCTheme.line)
                    Toggle(isOn: Binding(get: { store.virtualCarEnabled }, set: { enabled in
                        Task { await store.setVirtualCarEnabled(enabled) }
                    })) {
                        VStack(alignment: .leading, spacing: 5) {
                            Text("Virtual car").sparcFont(14)
                            Text("Starts plugged in at 40%. Try charging, pause, and schedules.")
                                .sparcFont(11).foregroundStyle(SPARCTheme.muted)
                        }
                    }
                    .tint(SPARCTheme.green)
                    .accessibilityIdentifier("settings.virtualCar")
                    .disabled(store.busy)
                    Text(store.isDemo ? "The virtual car runs in this app. Turn Demo mode off to use your paired Pi." : "The virtual car runs on your Pi. Battery, energy, and prices are virtual readings.")
                        .sparcFont(11).foregroundStyle(SPARCTheme.muted)
                }
            }
            NavigationLink(destination: ControllerSetupView()) {
                SPARCNavigationButton(title: provisioning.savedController == nil ? "Connect home controller" : "Manage home controller", subtitle: "Bluetooth controls · home network", symbol: "network")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("settings.connectController")
            NavigationLink(destination: CurrentScheduleView()) {
                SPARCNavigationButton(title: "Charging schedule", subtitle: "Your weekly routine and next departure", symbol: "calendar")
            }.buttonStyle(SPARCPressStyle())
            SPARCCard {
                VStack(alignment: .leading, spacing: 12) {
                    Label("Vehicle connection", systemImage: "car.side").sparcFont(15, weight: .semibold)
                    Text("Use the virtual car while the vehicle hardware is being connected. Its controls change the virtual charge; they do not operate a real vehicle.")
                        .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                }
            }
            NavigationLink(destination: PrivacyInformationView()) {
                SPARCNavigationButton(title: "Privacy & permissions", subtitle: "Local by design", symbol: "hand.raised")
            }.buttonStyle(SPARCPressStyle())
            Button { openSystemSettings() } label: {
                SPARCNavigationButton(title: "iPhone permissions", subtitle: "Bluetooth and local network access", symbol: "gearshape")
            }.buttonStyle(SPARCPressStyle())
            if provisioning.savedController != nil {
                SPARCButton(title: "Forget home controller", symbol: "trash") { confirmForget = true }
                    .disabled(store.busy)
                    .accessibilityIdentifier("settings.forgetController")
            }
            Text("SPARC · Built for your Raspberry Pi 4\nTimes shown in America/Chicago")
                .sparcFont(11).foregroundStyle(SPARCTheme.muted).frame(maxWidth: .infinity).multilineTextAlignment(.center).padding(.top, 8)
        }
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("Forget this home controller?", isPresented: $confirmForget, titleVisibility: .visible) {
            Button("Forget controller", role: .destructive) { store.forgetController(); provisioning.forget() }
            Button("Cancel", role: .cancel) {}
        } message: { Text("This removes its pairing key from this iPhone. Your controller's Wi-Fi and saved schedule remain on the Raspberry Pi.") }
    }
}

private struct PrivacyInformationView: View {
    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Your home. Your data.")
            SPARCNotice(title: "Charging schedules stay on your Raspberry Pi. The iPhone talks directly to your paired controller.", symbol: "house")
            SPARCCard {
                VStack(alignment: .leading, spacing: 18) {
                    privacyItem("Bluetooth", "Find and securely pair your nearby Raspberry Pi. Access is requested when you tap Search for controllers.")
                    Divider().overlay(SPARCTheme.line)
                    privacyItem("Local network", "Reach your paired controller over your home Wi-Fi. Access is requested when you connect locally.")
                    Divider().overlay(SPARCTheme.line)
                    privacyItem("Pairing key", "Stored in the iPhone Keychain. Wi-Fi passwords are sent encrypted to the controller and aren't saved by this app.")
                    Divider().overlay(SPARCTheme.line)
                    privacyItem("No account required", "This app has no cloud sign-in, analytics or calendar access.")
                }
            }
        }.navigationBarTitleDisplayMode(.inline)
    }
    private func privacyItem(_ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).sparcFont(15, weight: .semibold)
            Text(text).sparcFont(13).foregroundStyle(SPARCTheme.muted)
        }
    }
}

@MainActor func openSystemSettings() {
    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
    UIApplication.shared.open(url)
}
