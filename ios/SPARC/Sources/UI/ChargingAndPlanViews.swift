import SwiftUI

// The Charge and Plan tabs, plus the schedule screens reached from Home and Settings.
// Calm motion: the only animations here are press feedback, short ease-out state changes
// (timeline markers, the saved button, the finished card fading in) and the kit's own
// battery glide and light-bar breathing. Nothing repeats for decoration.

/// Room under the scroll content for the floating tab bar owned by the root view.
private let chargePlanBottomClearance: CGFloat = 16

private let chargeFootnoteColor = Color(white: 0.48)
private let chargeCardGradient = LinearGradient(
    colors: [Color(white: 0.086), Color(white: 0.027)],
    startPoint: UnitPoint(x: 0.37, y: 0), endPoint: UnitPoint(x: 0.63, y: 1))

// MARK: - Small layouts

/// Two or more children side by side in fixed proportions, all as tall as the tallest.
private struct WeightedRow: Layout {
    var weights: [CGFloat]
    var spacing: CGFloat = 10

    private func widths(total: CGFloat, count: Int) -> [CGFloat] {
        let used = Array(weights.prefix(count))
        guard !used.isEmpty else { return [] }
        let available = max(0, total - spacing * CGFloat(max(0, count - 1)))
        let sum = used.reduce(0, +)
        return used.map { available * $0 / sum }
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let total = proposal.replacingUnspecifiedDimensions(by: CGSize(width: 360, height: 100)).width
        let sizes = widths(total: total, count: subviews.count)
        var height: CGFloat = 0
        for (index, view) in subviews.enumerated() where index < sizes.count {
            height = max(height, view.sizeThatFits(ProposedViewSize(width: sizes[index], height: nil)).height)
        }
        return CGSize(width: total, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let sizes = widths(total: bounds.width, count: subviews.count)
        var x = bounds.minX
        for (index, view) in subviews.enumerated() where index < sizes.count {
            view.place(at: CGPoint(x: x, y: bounds.minY), anchor: .topLeading,
                       proposal: ProposedViewSize(width: sizes[index], height: bounds.height))
            x += sizes[index] + spacing
        }
    }
}

/// Items flow left to right and wrap onto the next line.
private struct WrapLayout: Layout {
    var spacing: CGFloat = 14
    var lineSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineHeight: CGFloat = 0, used: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth { y += lineHeight + lineSpacing; x = 0; lineHeight = 0 }
            x += size.width
            used = max(used, x)
            x += spacing
            lineHeight = max(lineHeight, size.height)
        }
        return CGSize(width: used, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, lineHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX { y += lineHeight + lineSpacing; x = bounds.minX; lineHeight = 0 }
            view.place(at: CGPoint(x: x, y: y), anchor: .topLeading, proposal: ProposedViewSize(size))
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }
}

private extension View {
    /// The Battery card on the Charge tab: a soft diagonal gradient with a hairline border.
    func chargeReadingCard(radius: CGFloat = 24) -> some View {
        background(chargeCardGradient, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous)
                .stroke(Color(white: 0.149), lineWidth: 1))
    }
}

/// A still sine wave, drawn in 60-point periods across whatever width it is given.
private struct PowerWave: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let midY = rect.midY
        let amplitude = rect.height * 0.4
        var x = rect.minX
        path.move(to: CGPoint(x: x, y: midY))
        while x < rect.maxX {
            path.addCurve(to: CGPoint(x: x + 30, y: midY),
                          control1: CGPoint(x: x + 10, y: midY - amplitude),
                          control2: CGPoint(x: x + 20, y: midY - amplitude))
            path.addCurve(to: CGPoint(x: x + 60, y: midY),
                          control1: CGPoint(x: x + 40, y: midY + amplitude),
                          control2: CGPoint(x: x + 50, y: midY + amplitude))
            x += 60
        }
        return path
    }
}

// MARK: - Shared helpers

private extension AppStore {
    /// The next charging window as plain words.
    var planWindowText: String {
        guard effectiveGoal != nil else { return "No charging window scheduled" }
        guard showsTelemetry else { return "Awaiting vehicle data" }
        guard snapshot.windowEnd > snapshot.windowStart else { return "No charging window scheduled" }
        return SPARCFormat.window(snapshot.windowStart, snapshot.windowEnd)
    }
}

/// "today", "tomorrow", or the weekday name, in Chicago time.
private func planDayWord(_ date: Date, now: Date = Date()) -> String {
    let calendar = ScheduleEngine.chicagoCalendar
    if calendar.isDate(date, inSameDayAs: now) { return "today" }
    if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now), calendar.isDate(date, inSameDayAs: tomorrow) {
        return "tomorrow"
    }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US")
    formatter.timeZone = SPARCFormat.zone
    formatter.dateFormat = "EEEE"
    return formatter.string(from: date)
}

private func planHourLabel(_ hour: Int) -> String {
    let twelve = hour % 12 == 0 ? 12 : hour % 12
    return "\(twelve) \(hour % 24 < 12 ? "AM" : "PM")"
}

/// The 24 hourly prices the app knows about, in cents per kWh.
private struct HourlyRates {
    let cents: [Double]

    static func model(now: Date = Date()) -> HourlyRates {
        let calendar = ScheduleEngine.chicagoCalendar
        let values = (0..<24).map { hour -> Double in
            let date = calendar.date(bySettingHour: hour, minute: 0, second: 0, of: now, matchingPolicy: .nextTime) ?? now
            return VirtualCarState.exampleRate(at: date)
        }
        return HourlyRates(cents: values)
    }

    var low: Double { cents.min() ?? 0 }
    var high: Double { cents.max() ?? 0 }
    func isLow(_ hour: Int) -> Bool { low < high && cents[((hour % 24) + 24) % 24] == low }

    /// Runs of consecutive low-price hours, e.g. 1..<5.
    var lowRanges: [Range<Int>] {
        var ranges: [Range<Int>] = []
        var start: Int?
        for hour in 0...24 {
            let on = hour < 24 && isLow(hour)
            if on, start == nil { start = hour }
            if !on, let begin = start { ranges.append(begin..<hour); start = nil }
        }
        return ranges
    }

    var lowText: String {
        lowRanges.map { "\(planHourLabel($0.lowerBound)) to \(planHourLabel($0.upperBound))" }.joined(separator: ", ")
    }

    var summary: String {
        let lowCents = SPARCFormat.decimal(low, digits: 0), highCents = SPARCFormat.decimal(high, digits: 0)
        guard low < high else { return "Electricity prices are \(highCents) cents per kilowatt-hour all day." }
        return "Electricity prices. \(lowCents) cents per kilowatt-hour from \(lowText), and \(highCents) cents at other times."
    }
}

/// A page used by the schedule screens: title, subtitle and cards on black.
private struct SchedulePage<Content: View>: View {
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(title).sparcFont(30, weight: .bold).tracking(-0.9)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    if let subtitle { Text(subtitle).sparcFont(14).foregroundStyle(SPARCTheme.muted) }
                }
                .padding(.bottom, 4)
                content
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 32)
            .frame(maxWidth: 640)
            .frame(maxWidth: .infinity)
        }
        .background(SPARCTheme.background)
        .scrollIndicators(.hidden)
    }
}

private struct ScheduleNote: View {
    let text: String
    var symbol = "info.circle"
    var warning = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol).font(.system(size: 14, weight: .semibold))
                .foregroundStyle(warning ? SPARCTheme.warning : SPARCTheme.spark)
                .frame(width: 20)
                .accessibilityHidden(true)
            Text(text).sparcFont(13).foregroundStyle(warning ? SPARCTheme.warning : SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 20)
    }
}

// MARK: - Charge

struct ChargingView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.sparcToast) private var toast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var confirmEnd = false

    private let onPlan: () -> Void
    private let onConnection: () -> Void

    init(onPlan: @escaping () -> Void = {}, onConnection: @escaping () -> Void = {}) {
        self.onPlan = onPlan
        self.onConnection = onConnection
    }

    private var state: ChargeState { store.snapshot.state }
    private var charging: Bool { state == .charging }
    private var hasReadings: Bool { store.showsTelemetry }
    private var isConnected: Bool { state != .offline }

    /// The charge limit shown everywhere on this page.
    private var target: Int {
        store.showsTelemetry ? store.snapshot.targetPercent : (store.effectiveGoal?.targetPercent ?? store.snapshot.targetPercent)
    }

    /// When the next window opens, only if it is still ahead of us.
    private var planStartText: String? {
        guard store.showsTelemetry, store.effectiveGoal != nil,
              store.snapshot.windowEnd > store.snapshot.windowStart,
              store.snapshot.windowStart > Date() else { return nil }
        return SPARCFormat.time(store.snapshot.windowStart)
    }

    var body: some View {
        if store.profileConnectionMode == .tesla {
            TeslaManualChargingView()
        } else { virtualChargingBody }
    }

    private var virtualChargingBody: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                if state == .offline {
                    notice(store.connectionMessage ?? "Connect your home controller in Settings to view live charging.",
                           symbol: "wifi.slash")
                        .padding(.top, 12)
                }
                truck.padding(.top, 6)
                readings
                sessionTiles.padding(.top, 10)
                stateArea
                limitRow.padding(.top, 12)
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, chargePlanBottomClearance)
            .frame(maxWidth: 640)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refresh() }
        .background(alignment: .topLeading) { ambientGlow }
        .background(SPARCTheme.background)
        .confirmationDialog("End this charging session?", isPresented: $confirmEnd, titleVisibility: .visible) {
            Button("End session", role: .destructive) { end() }
            Button("Keep charging", role: .cancel) {}
        } message: { Text(endMessage) }
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Your charge.").sparcFont(30, weight: .bold).tracking(-0.9)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Text(vehicleLine).sparcFont(14).foregroundStyle(SPARCTheme.muted)
            }
            Spacer(minLength: 8)
            connectionPill
        }
    }

    private var vehicleLine: String {
        store.snapshot.controlOnly == true ? store.displayVehicleName : "\(store.displayVehicleName) · Home"
    }

    private var connectionPill: some View {
        Button(action: onConnection) {
            HStack(spacing: 7) {
                SPARCStatusDot(color: isConnected ? SPARCTheme.spark : SPARCTheme.tertiary, size: 8)
                Text(isConnected ? "Connected" : "Not connected").sparcFont(12.5, weight: .semibold)
            }
            .foregroundStyle(isConnected ? SPARCTheme.sparkText : Color(white: 0.78))
            .padding(.horizontal, 12)
            .frame(height: 36)
            .background(isConnected ? SPARCTheme.spark.opacity(0.08) : Color.white.opacity(0.06), in: Capsule())
            .overlay(Capsule().stroke(isConnected ? SPARCTheme.spark.opacity(0.3) : Color(white: 0.2), lineWidth: 1))
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .fixedSize()
        .accessibilityLabel(isConnected ? "Home controller connected. Open connection" : "Home controller not connected. Open connection")
        .accessibilityIdentifier("charge.connection")
    }

    // MARK: Truck

    private var ambientGlow: some View {
        EllipticalGradient(colors: [charging ? SPARCTheme.spark.opacity(0.20) : SPARCTheme.green.opacity(0.08),
                                    SPARCTheme.green.opacity(0)],
                           center: .center, startRadiusFraction: 0, endRadiusFraction: 0.44)
            .frame(width: 510, height: 420)
            .offset(x: -60, y: 120)
            .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: charging)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    private var truck: some View {
        ControllerVehicleView(charging: charging)
            .frame(maxWidth: .infinity)
            .background(alignment: .bottom) {
                if charging {
                    GeometryReader { proxy in
                        EllipticalGradient(colors: [SPARCTheme.spark.opacity(0.6), SPARCTheme.spark.opacity(0)],
                                           center: .center, startRadiusFraction: 0, endRadiusFraction: 0.5)
                            .frame(width: proxy.size.width * 0.76, height: 40)
                            .blur(radius: 7)
                            .offset(x: proxy.size.width * 0.14, y: proxy.size.height - 60)
                    }
                    .accessibilityHidden(true)
                    .transition(.opacity)
                }
            }
            .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: charging)
            .padding(.horizontal, -16)
    }

    // MARK: Readings

    private var readings: some View {
        WeightedRow(weights: [1.15, 1], spacing: 10) {
            batteryCard
            powerCard
        }
        .padding(.top, 4)
    }

    private var batteryCaption: String {
        switch state {
        case .charging: return "Charging to \(target)%"
        case .paused: return "Paused · goal \(target)%"
        case .complete: return "Goal \(target)% reached"
        case .waiting, .unplugged, .offline: return "Goal \(target)%"
        }
    }

    private var batteryCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Battery").sparcFont(12, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                Text(hasReadings ? "\(store.snapshot.batteryPercent)" : "—")
                    .sparcFont(52, weight: .bold).tracking(-2.6).monospacedDigit()
                    .foregroundStyle(hasReadings ? Color.white : SPARCTheme.muted)
                if hasReadings { Text("%").sparcFont(22, weight: .bold).foregroundStyle(SPARCTheme.muted) }
            }
            .lineLimit(1).minimumScaleFactor(0.6)
            .padding(.top, 4)
            SPARCBatteryBar(percent: hasReadings ? store.snapshot.batteryPercent : 0, target: target, height: 8)
                .padding(.top, 10)
            Text(batteryCaption).sparcFont(12).foregroundStyle(SPARCTheme.tertiary)
        }
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .chargeReadingCard()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Battery")
        .accessibilityValue(hasReadings ? "\(store.snapshot.batteryPercent) percent. \(batteryCaption)" : "Unavailable. \(batteryCaption)")
    }

    private var powerCaption: String {
        switch state {
        case .charging: return "Charging now"
        case .paused: return "Paused"
        case .complete: return "Done for tonight"
        case .waiting: return "Not charging"
        case .unplugged: return "Not plugged in"
        case .offline: return "Not connected"
        }
    }

    private var powerCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Power").sparcFont(12, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(hasReadings ? SPARCFormat.decimal(store.snapshot.powerKW) : "—")
                    .sparcFont(40, weight: .bold).tracking(-1.6).monospacedDigit()
                    .foregroundStyle(charging ? SPARCTheme.spark : Color.white)
                Text("kW").sparcFont(16).foregroundStyle(SPARCTheme.muted)
            }
            .lineLimit(1).minimumScaleFactor(0.6)
            .padding(.top, 4)
            ZStack {
                if charging {
                    PowerWave().stroke(SPARCTheme.spark, style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                } else {
                    Capsule().fill(SPARCTheme.controlLine).frame(height: 2)
                }
            }
            .frame(height: 30)
            .clipped()
            .padding(.top, 6)
            .accessibilityHidden(true)
            Text(powerCaption).sparcFont(12).foregroundStyle(SPARCTheme.tertiary).padding(.top, 6)
        }
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .sparcSurface(radius: 24, tinted: charging)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Power")
        .accessibilityValue(hasReadings ? "\(SPARCFormat.decimal(store.snapshot.powerKW)) kilowatts. \(powerCaption)" : "Unavailable. \(powerCaption)")
    }

    private var sessionTiles: some View {
        HStack(spacing: 8) {
            SPARCStatTile(title: "Added",
                          value: hasReadings ? SPARCFormat.decimal(store.snapshot.energyKWh) : "—",
                          unit: hasReadings ? "kWh" : nil)
            SPARCStatTile(title: "To \(target)%",
                          value: hasReadings ? (store.snapshot.remainingMinutes > 0 ? SPARCFormat.duration(store.snapshot.remainingMinutes) : "Done") : "—")
            SPARCStatTile(title: "Cost so far",
                          value: hasReadings && store.snapshot.costAvailable != false ? SPARCFormat.money(store.snapshot.estimatedCost) : "—")
        }
    }

    // MARK: State-specific area

    private var stateArea: some View {
        VStack(alignment: .leading, spacing: 0) {
            if state == .complete {
                completeCard.padding(.top, 12).transition(.opacity)
            } else if state == .unplugged {
                notice(store.snapshot.controlOnly == true
                       ? "No vehicle is connected yet. Plug in to see live charging."
                       : "Connect your charging cable before requesting a charge.",
                       symbol: "cable.connector")
                    .padding(.top, 12).transition(.opacity)
            }
            actions.padding(.top, 14)
        }
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: state)
    }

    private var completeCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark").font(.system(size: 18, weight: .heavy))
                .foregroundStyle(SPARCTheme.onSpark)
                .frame(width: 40, height: 40)
                .background(SPARCTheme.spark, in: Circle())
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(target)% and ready to go").sparcFont(16, weight: .semibold)
                if hasReadings {
                    Text("\(SPARCFormat.decimal(store.snapshot.energyKWh)) kWh added this session")
                        .sparcFont(13).monospacedDigit()
                        .foregroundStyle(Color(red: 201 / 255, green: 242 / 255, blue: 220 / 255))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .background(SPARCTheme.spark.opacity(0.10), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(SPARCTheme.spark.opacity(0.32), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var actions: some View {
        switch state {
        case .charging:
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { pauseButton; endButton }
                VStack(spacing: 10) { pauseButton; endButton }
            }
        case .paused:
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { resumeButton; endButton }
                VStack(spacing: 10) { resumeButton; endButton }
            }
        case .waiting:
            SPARCActionButton(title: "Charge now", symbol: "bolt.fill", kind: .primary, height: 58, busy: store.busy) { start() }
                .shadow(color: SPARCTheme.spark.opacity(0.3), radius: 15, y: 10)
                .accessibilityIdentifier("charge.start")
        case .complete, .unplugged, .offline:
            EmptyView()
        }
    }

    private var pauseButton: some View {
        SPARCActionButton(title: "Pause", symbol: "pause.fill", kind: .secondary, height: 58, busy: store.busy) { pause() }
            .accessibilityLabel("Pause charging")
            .accessibilityIdentifier("charge.pause")
    }

    private var resumeButton: some View {
        SPARCActionButton(title: store.isDemo ? "Resume" : "Resume plan", symbol: "play.fill", kind: .primary, height: 58, busy: store.busy) { resume() }
            .shadow(color: SPARCTheme.spark.opacity(0.28), radius: 14, y: 10)
            .accessibilityLabel(store.isDemo ? "Resume charging" : "Resume smart charging plan")
            .accessibilityIdentifier("charge.resume")
    }

    private var endButton: some View {
        SPARCActionButton(title: "End session", symbol: "stop.fill", kind: .danger, height: 58) { confirmEnd = true }
            .disabled(store.busy)
            .accessibilityIdentifier("charge.end")
    }

    // MARK: Charge limit row and footnote

    private var limitRow: some View {
        Button(action: onPlan) {
            HStack(spacing: 14) {
                Image(systemName: "calendar")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(SPARCTheme.spark)
                    .frame(width: 42, height: 42)
                    .background(SPARCTheme.spark.opacity(0.12), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Charge limit \(target)%").sparcFont(15, weight: .semibold).foregroundStyle(.white)
                    Text(limitSubtitle).sparcFont(12.5).foregroundStyle(SPARCTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 4)
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(SPARCTheme.tertiary)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, 16).padding(.vertical, 15)
            .frame(maxWidth: .infinity, alignment: .leading)
            .sparcSurface(radius: 22)
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens your charging plan")
        .accessibilityIdentifier("charge.planLink")
    }

    private var limitSubtitle: String {
        guard let goal = store.effectiveGoal else { return "No departure planned yet" }
        let ready = store.showsTelemetry ? store.snapshot.readyBy : goal.readyBy
        return "Ready by \(SPARCFormat.time(ready)) for your \(SPARCFormat.time(goal.departure)) drive"
    }

    private var endMessage: String {
        hasReadings ? "Battery stays at \(store.snapshot.batteryPercent)%." : "Your weekly plan stays saved."
    }

    private func notice(_ text: String, symbol: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol).font(.system(size: 14, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark)
                .frame(width: 20)
                .accessibilityHidden(true)
            Text(text).sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 20)
        .accessibilityElement(children: .combine)
    }

    // MARK: Actions

    private func pause() {
        Task { @MainActor in
            await store.pauseCharging()
            if store.errorMessage == nil { toast("Paused · 0 kW") }
        }
    }

    private func resume() {
        Task { @MainActor in
            await store.resumeCharging()
            if store.errorMessage == nil { toast(state == .charging ? "Charging resumed\(powerSuffix)" : "Smart plan resumed") }
        }
    }

    private func start() {
        Task { @MainActor in
            await store.chargeNow()
            if store.errorMessage == nil { toast("Charging started\(powerSuffix)") }
        }
    }

    private func end() {
        Task { @MainActor in
            await store.endCharging()
            if store.errorMessage == nil {
                toast(planStartText.map { "Session ended. Your plan starts at \($0)" } ?? "Session ended. Your plan stays saved")
            }
        }
    }

    private var powerSuffix: String {
        store.snapshot.powerKW > 0 ? " · \(SPARCFormat.decimal(store.snapshot.powerKW)) kW" : ""
    }
}

// MARK: - Plan

struct PlanView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        VStack(spacing: 0) {
            if !store.isDemo { ScheduleSourceControl(link: store.sleepCalendar) }
            if store.sleepCalendarStatus?.enabled == true { AppleCalendarPlanView() }
            else if !store.isDemo, store.campaign != nil { CampaignPlanView() }
            else { PlanContent(initial: store.weekly) }
        }
    }
}

private struct PlanEstimate {
    var label = "Tonight"
    var windowText = ""
    var readyText = ""
    var windowRange: ClosedRange<Double>?
    var ready: Double?
    var departure: Double?
    var cheap: [ClosedRange<Double>] = []
    var costText = "—"
    var savingsText: String?
    var warning: String?
    var timelineLabel = ""
}

private struct PlanContent: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.sparcToast) private var toast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize

    @State private var draft: WeeklySchedule
    @State private var baseline: WeeklySchedule
    @State private var justSaved = false
    @State private var saveToken = 0
    @State private var weeklyMessage: String?

    @State private var onceOpen = false
    @State private var onceMinutes: Int
    @State private var onceTarget: Int
    @State private var onceMessage: String?

    private static let onceTargets = [70, 80, 90, 100]

    init(initial: WeeklySchedule) {
        _draft = State(initialValue: initial)
        _baseline = State(initialValue: initial)
        _onceMinutes = State(initialValue: ((initial.departureMinutes - 60) % 1440 + 1440) % 1440)
        _onceTarget = State(initialValue: Self.onceTargets.contains(initial.targetPercent) ? initial.targetPercent : 90)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Your charging rhythm.").sparcFont(30, weight: .bold).tracking(-0.9)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                }
                tonightCard.padding(.top, 16)
                WeeklyRoutineCard(draft: $draft, justSaved: justSaved, message: weeklyMessage,
                                  saveTitle: "Save routine", busy: store.busy,
                                  onSave: saveWeekly,
                                  onBlockedDay: { toast("Keep at least one charging day") })
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("plan.edit")
                    .padding(.top, 12)
                onceCard.padding(.top, 12)
                goodHoursCard.padding(.top, 12)
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, chargePlanBottomClearance)
            .frame(maxWidth: 640)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refresh() }
        .background(alignment: .topTrailing) {
            RadialGradient(colors: [SPARCTheme.green.opacity(0.15), SPARCTheme.green.opacity(0)],
                           center: .center, startRadius: 0, endRadius: 193)
                .frame(width: 420, height: 420)
                .offset(x: 140, y: -160)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .background(SPARCTheme.background)
        .onChange(of: store.weekly) { _, updated in
            if draft == baseline { draft = updated }
            baseline = updated
        }
        .onChange(of: draft) {
            justSaved = false
            weeklyMessage = nil
        }
    }

    // MARK: Estimate

    private func makeEstimate() -> PlanEstimate {
        let now = Date()
        let calendar = ScheduleEngine.chicagoCalendar
        let rates: HourlyRates? = (store.isDemo && store.isVirtualCarActive) ? HourlyRates.model(now: now) : nil
        var plan = PlanEstimate()

        guard let goal = ScheduleEngine.effectiveGoal(weekly: draft, today: store.today, now: now) else {
            plan.label = "Next up"
            plan.windowText = "Routine paused"
            plan.readyText = "Charge now is still available."
            plan.timelineLabel = "No charging window planned."
            plan.costText = "—"
            if let rates {
                plan.cheap = Self.cheapSegments(rates: rates)
            }
            return plan
        }

        let midnight = calendar.startOfDay(for: goal.departure)
        func fraction(_ date: Date) -> Double {
            let minutes = date.timeIntervalSince(midnight) / 60
            return min(1, max(0, (minutes + 180) / 780))
        }

        plan.label = Self.label(for: goal, now: now)
        plan.readyText = "Ready by \(SPARCFormat.time(goal.readyBy)) · \(goal.targetPercent)% for your \(SPARCFormat.time(goal.departure)) drive"
        plan.ready = fraction(goal.readyBy)
        plan.departure = fraction(goal.departure)
        if let rates { plan.cheap = Self.cheapSegments(rates: rates) }

        var start: Date?
        var end: Date?

        if rates != nil {
            // The same model the controller uses for the planned night.
            let battery = Double(store.snapshot.batteryPercent)
            let minutesPerPercent = VirtualCarState.capacityKWh / 100 / (VirtualCarState.powerKW * VirtualCarState.efficiency) * 60
            let need = max(0, Double(goal.targetPercent) - battery) * minutesPerPercent
            if need <= 0.5 {
                plan.windowText = "Already charged"
                plan.costText = SPARCFormat.money(0)
            } else {
                let interval = ScheduleEngine.virtualWindow(for: goal, weekly: draft, remainingMinutes: Int(need.rounded(.up)), now: now)
                let available = max(0, goal.readyBy.timeIntervalSince(interval.start) / 60)
                let minutes = min(need, available)
                let finish = interval.start.addingTimeInterval(minutes * 60)
                start = interval.start
                end = finish
                plan.windowText = minutes > 0 ? SPARCFormat.window(interval.start, finish) : "Not enough time"
                let planned = Self.modelCost(from: interval.start, minutes: minutes, calendar: calendar)
                let immediate = Self.modelCost(from: now, minutes: minutes, calendar: calendar)
                plan.costText = SPARCFormat.money(planned)
                plan.savingsText = SPARCFormat.money(max(0, immediate - planned))
                if minutes < need - 0.5 {
                    let reached = min(goal.targetPercent, Int(floor(battery + minutes / minutesPerPercent)))
                    let advice = draft.mode == .fixed ? "Start earlier" : "Leave a little later"
                    plan.warning = "Reaches \(reached)% by \(SPARCFormat.time(goal.readyBy)). \(advice) to reach \(goal.targetPercent)%."
                }
            }
        } else if store.showsTelemetry {
            plan.windowText = store.planWindowText
            if store.snapshot.windowEnd > store.snapshot.windowStart {
                start = store.snapshot.windowStart
                end = store.snapshot.windowEnd
            }
            plan.costText = SPARCFormat.money(store.snapshot.estimatedCost)
        } else {
            plan.windowText = store.planWindowText
        }

        if let start, let end, end > start {
            plan.windowRange = fraction(start)...fraction(end)
            plan.timelineLabel = "Charging window \(SPARCFormat.time(start)) to \(SPARCFormat.time(end)). Ready by \(SPARCFormat.time(goal.readyBy)). Departure \(SPARCFormat.time(goal.departure))."
        } else {
            plan.timelineLabel = "Ready by \(SPARCFormat.time(goal.readyBy)). Departure \(SPARCFormat.time(goal.departure))."
        }
        return plan
    }

    private static func label(for goal: ScheduleGoal, now: Date) -> String {
        let calendar = ScheduleEngine.chicagoCalendar
        if goal.isOverride { return planDayWord(goal.departure, now: now).capitalized + " only" }
        if calendar.isDate(goal.departure, inSameDayAs: now) { return "This morning" }
        if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now), calendar.isDate(goal.departure, inSameDayAs: tomorrow) {
            return "Tonight"
        }
        let nightBefore = calendar.date(byAdding: .day, value: -1, to: goal.departure) ?? goal.departure
        return planDayWord(nightBefore, now: now).capitalized + " night"
    }

    /// Low-price hours inside the 9 PM to 10 AM strip, as fractions of its width.
    private static func cheapSegments(rates: HourlyRates) -> [ClosedRange<Double>] {
        var segments: [ClosedRange<Double>] = []
        for hour in -3...9 where rates.isLow(hour) {
            let lower = Double(hour * 60 + 180) / 780
            let upper = Double((hour + 1) * 60 + 180) / 780
            if let last = segments.last, abs(last.upperBound - lower) < 0.0001 {
                segments[segments.count - 1] = last.lowerBound...upper
            } else {
                segments.append(lower...upper)
            }
        }
        return segments
    }

    /// Cost of drawing the model's charging power from `start` for `minutes`, hour by hour.
    private static func modelCost(from start: Date, minutes: Double, calendar: Calendar) -> Double {
        guard minutes > 0 else { return 0 }
        let finish = start.addingTimeInterval(minutes * 60)
        var cursor = start
        var total = 0.0
        while cursor < finish {
            let hourEnd = min(finish, calendar.dateInterval(of: .hour, for: cursor)?.end ?? finish)
            guard hourEnd > cursor else { break }
            total += VirtualCarState.powerKW * hourEnd.timeIntervalSince(cursor) / 3600 * VirtualCarState.exampleRate(at: cursor) / 100
            cursor = hourEnd
        }
        return total
    }

    // MARK: Tonight

    private var tonightCard: some View {
        let plan = makeEstimate()
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 10) {
                Text(plan.label.uppercased()).sparcFont(12, weight: .semibold).tracking(1)
                    .foregroundStyle(SPARCTheme.tertiary)
                Spacer(minLength: 8)
                Text(draft.mode == .smart ? "Smart" : "Fixed start")
                    .sparcFont(12, weight: .bold)
                    .foregroundStyle(SPARCTheme.spark)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(SPARCTheme.spark.opacity(0.13), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
            }
            Text(plan.windowText)
                .sparcFont(30, weight: .bold).tracking(-0.9).monospacedDigit()
                .foregroundStyle(SPARCTheme.spark)
                .lineLimit(2).minimumScaleFactor(0.65)
                .padding(.top, 8)
            Text(plan.readyText)
                .sparcFont(13.5).monospacedDigit()
                .foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 4)
            PlanTimeline(window: plan.windowRange, ready: plan.ready, departure: plan.departure, cheap: plan.cheap)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(plan.timelineLabel)
                .padding(.top, 16)
            WeightedRow(weights: [1, 1], spacing: 10) {
                PlanHeroTile(title: "Estimated cost", value: plan.costText, tinted: false)
                if let savings = plan.savingsText {
                    PlanHeroTile(title: "Less than charging now", value: savings, tinted: true)
                }
            }
            .padding(.top, 16)
            if let warning = plan.warning {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle").font(.system(size: 13, weight: .semibold))
                        .padding(.top, 2)
                        .accessibilityHidden(true)
                    Text(warning).sparcFont(13).monospacedDigit().fixedSize(horizontal: false, vertical: true)
                }
                .foregroundStyle(SPARCTheme.warning)
                .padding(.top, 12)
                .transition(.opacity)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SPARCTheme.heroGradient, in: RoundedRectangle(cornerRadius: 28, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 28, style: .continuous).stroke(Color(white: 0.149), lineWidth: 1))
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: plan.warning)
    }

    // MARK: Weekly save

    private func saveWeekly() {
        weeklyMessage = nil
        do { try draft.validate() } catch {
            weeklyMessage = error.localizedDescription
            return
        }
        let schedule = draft
        Task { @MainActor in
            await store.saveWeekly(schedule)
            if let message = store.errorMessage {
                weeklyMessage = message
                store.errorMessage = nil
                return
            }
            baseline = schedule
            saveToken += 1
            let token = saveToken
            withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { justSaved = true }
            try? await Task.sleep(for: .seconds(2.6))
            guard token == saveToken else { return }
            withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { justSaved = false }
        }
    }

    // MARK: Just this departure

    private var onceCard: some View {
        let active = store.today
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 12) {
                Image(systemName: "calendar.badge.plus")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(SPARCTheme.spark)
                    .frame(width: 42, height: 42)
                    .background(SPARCTheme.spark.opacity(0.12), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Just this departure").sparcFont(15.5, weight: .semibold)
                    Text(onceSummary).sparcFont(12.5).monospacedDigit().foregroundStyle(SPARCTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 4)
                if active != nil {
                    Button { removeOnce() } label: {
                        Text("Remove").sparcFont(13, weight: .semibold)
                            .padding(.horizontal, 13)
                            .frame(minHeight: 44)
                            .background(SPARCTheme.control, in: Capsule())
                            .overlay(Capsule().stroke(SPARCTheme.controlLine, lineWidth: 1))
                    }
                    .buttonStyle(SPARCPressStyle())
                    .disabled(store.busy)
                    .accessibilityLabel("Remove the one-time change")
                    .accessibilityIdentifier("schedule.removeToday")
                }
            }
            if !onceOpen {
                SPARCActionButton(title: active == nil ? "Change the next departure" : "Change this departure",
                                  kind: .secondary, height: 48) {
                    onceMessage = nil
                    withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { onceOpen = true }
                }
                .padding(.top, 14)
                .accessibilityIdentifier("plan.once.open")
            } else {
                onceEditor.padding(.top, 14).transition(.opacity)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 28)
    }

    private var onceOccurrence: Date? { ScheduleEngine.nextOccurrence(minutes: onceMinutes, after: Date()) }
    private var onceDayWord: String { onceOccurrence.map { planDayWord($0) } ?? "tomorrow" }

    private var onceSummary: String {
        guard let override = store.today else { return "Leaving early or need extra range? Change one departure." }
        return "\(override.targetPercent)% by \(SPARCFormat.time(override.departure.addingTimeInterval(-30 * 60))) · \(planDayWord(override.departure)) only"
    }

    private var onceTimeChoices: [Int] {
        let departure = store.weekly.departureMinutes
        return [-90, -60, 0, 60].map { ((departure + $0) % 1440 + 1440) % 1440 }
    }

    private var onceColumns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: 6), count: typeSize.isAccessibilitySize ? 2 : 4)
    }

    private var onceEditor: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Leaving \(onceDayWord) at").sparcFont(12.5, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            LazyVGrid(columns: onceColumns, spacing: 6) {
                ForEach(onceTimeChoices, id: \.self) { minutes in
                    PlanChoiceChip(title: SPARCFormat.time(SPARCFormat.clockDate(minutes)), selected: onceMinutes == minutes) {
                        onceMinutes = minutes
                    }
                    .accessibilityIdentifier("plan.once.time.\(minutes)")
                }
            }
            .padding(.top, 8)
            Text("Charge to").sparcFont(12.5, weight: .semibold).foregroundStyle(SPARCTheme.muted).padding(.top, 14)
            LazyVGrid(columns: onceColumns, spacing: 6) {
                ForEach(Self.onceTargets, id: \.self) { percent in
                    PlanChoiceChip(title: "\(percent)%", selected: onceTarget == percent) { onceTarget = percent }
                        .accessibilityIdentifier("plan.once.target.\(percent)")
                }
            }
            .padding(.top, 8)
            if let onceMessage {
                Text(onceMessage).sparcFont(13).foregroundStyle(SPARCTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 12)
                    .accessibilityIdentifier("schedule.todayValidation")
            }
            SPARCActionButton(title: "Save for \(onceDayWord) only", kind: .primary, height: 50, busy: store.busy) { saveOnce() }
                .shadow(color: SPARCTheme.spark.opacity(0.25), radius: 12, y: 8)
                .padding(.top, 14)
                .accessibilityIdentifier("plan.once.save")
            SPARCActionButton(title: "Cancel", kind: .plain, height: 44) {
                withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { onceOpen = false }
                onceMessage = nil
            }
            .padding(.top, 4)
            .accessibilityIdentifier("plan.once.cancel")
        }
    }

    private func saveOnce() {
        onceMessage = nil
        guard let departure = onceOccurrence else { onceMessage = ScheduleValidationError.time.errorDescription; return }
        do { try TodayOverride(targetPercent: onceTarget, departure: departure).validate() } catch {
            onceMessage = error.localizedDescription
            return
        }
        let word = planDayWord(departure)
        let percent = onceTarget
        Task { @MainActor in
            await store.saveToday(target: percent, departure: departure)
            if let message = store.errorMessage {
                onceMessage = message
                store.errorMessage = nil
                return
            }
            withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { onceOpen = false }
            toast("Saved for \(word) only")
        }
    }

    private func removeOnce() {
        Task { @MainActor in
            await store.clearToday()
            if let message = store.errorMessage {
                onceMessage = message
                store.errorMessage = nil
                return
            }
            toast("Back to your weekly routine")
        }
    }

    // MARK: Good hours

    @ViewBuilder private var goodHoursCard: some View {
        if (store.isDemo && store.isVirtualCarActive) {
            GoodHoursCard(rates: HourlyRates.model(), title: "Your good hours")
        } else {
            RatePageButton {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Your good hours").sparcFont(17, weight: .semibold).tracking(-0.2)
                        .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 8)
                    Text("¢ per kWh").sparcFont(12).foregroundStyle(SPARCTheme.tertiary)
                }
                Text(store.currentCombinedRate.map { "\(SPARCFormat.decimal($0,digits: 1))¢" } ?? "—")
                    .sparcFont(34, weight: .bold).tracking(-1.2).monospacedDigit()
                    .foregroundStyle(SPARCTheme.spark)
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .sparcSurface(radius: 28)
            }.buttonStyle(SPARCPressStyle()).accessibilityIdentifier("plan.rates")
        }
    }
}

/// The 9 PM to 10 AM strip: cheap hours, the charging window, and the ready and departure markers.
private struct PlanTimeline: View {
    let window: ClosedRange<Double>?
    let ready: Double?
    let departure: Double?
    let cheap: [ClosedRange<Double>]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var motionKey: [Double] {
        [window?.lowerBound ?? -1, window?.upperBound ?? -1, ready ?? -1, departure ?? -1]
    }

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    Capsule().fill(Color(white: 0.106)).frame(height: 10).offset(y: 17)
                    ForEach(Array(cheap.enumerated()), id: \.offset) { _, range in
                        Rectangle().fill(SPARCTheme.spark.opacity(0.13))
                            .frame(width: width * (range.upperBound - range.lowerBound), height: 10)
                            .offset(x: width * range.lowerBound, y: 17)
                    }
                    if let window {
                        Capsule().fill(SPARCTheme.spark)
                            .frame(width: max(8, width * (window.upperBound - window.lowerBound)), height: 16)
                            .shadow(color: SPARCTheme.spark.opacity(0.55), radius: 8)
                            .offset(x: width * window.lowerBound, y: 14)
                    }
                    if let ready {
                        RoundedRectangle(cornerRadius: 1).fill(.white)
                            .frame(width: 2, height: 32)
                            .offset(x: width * ready - 1, y: 6)
                    }
                    if let departure {
                        RoundedRectangle(cornerRadius: 1).fill(Color(white: 0.43))
                            .frame(width: 2, height: 32)
                            .offset(x: width * departure - 1, y: 6)
                    }
                }
                .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: motionKey)
            }
            .frame(height: 46)
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack {
                    tick("9 PM").frame(maxWidth: .infinity, alignment: .leading)
                    tick("12 AM").position(x: width * 0.231, y: 7.5)
                    tick("3 AM").position(x: width * 0.462, y: 7.5)
                    tick("6 AM").position(x: width * 0.692, y: 7.5)
                    tick("10 AM").frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
            .frame(height: 15)
        }
    }

    private func tick(_ text: String) -> some View {
        Text(text).sparcFont(11).foregroundStyle(SPARCTheme.tertiary).fixedSize()
    }
}

private struct PlanHeroTile: View {
    let title: String
    let value: String
    let tinted: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).sparcFont(11.5, weight: .semibold)
                .foregroundStyle(tinted ? SPARCTheme.mint : SPARCTheme.muted)
            Text(value).sparcFont(21, weight: .bold).tracking(-0.4).monospacedDigit()
                .foregroundStyle(tinted ? SPARCTheme.spark : .white)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .padding(.horizontal, 13).padding(.vertical, 12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .sparcSurface(radius: 18, tinted: tinted)
        .accessibilityElement(children: .combine)
    }
}

private struct PlanChoiceChip: View {
    let title: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title).sparcFont(13.5, weight: .semibold)
                .lineLimit(1).minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(selected ? SPARCTheme.spark : Color(white: 0.816))
                .background(selected ? SPARCTheme.spark.opacity(0.14) : SPARCTheme.control,
                            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(selected ? SPARCTheme.spark.opacity(0.5) : Color(white: 0.149), lineWidth: 1))
                .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// 24 price bars with a legend. Cheap hours glow green; the current hour is white.
private struct GoodHoursCard: View {
    let rates: HourlyRates
    var title: String? = nil
    private var currentHour: Int { ScheduleEngine.chicagoCalendar.component(.hour, from: Date()) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                if let title {
                    Text(title).sparcFont(17, weight: .semibold).tracking(-0.2).accessibilityAddTraits(.isHeader)
                }
                Spacer(minLength: 8)
                Text("¢ per kWh").sparcFont(12).foregroundStyle(SPARCTheme.tertiary)
            }
            bars.padding(.top, 16)
            HStack(spacing: 0) {
                ForEach(Array(["12 AM", "6 AM", "12 PM", "6 PM", "11 PM"].enumerated()), id: \.offset) { index, label in
                    if index > 0 { Spacer(minLength: 0) }
                    Text(label).sparcFont(11).foregroundStyle(SPARCTheme.tertiary)
                }
            }
            .padding(.top, 8)
            .accessibilityHidden(true)
            WrapLayout(spacing: 14, lineSpacing: 8) {
                if rates.low < rates.high {
                    legend(color: SPARCTheme.spark, text: "\(SPARCFormat.decimal(rates.low, digits: 0))¢ · \(rates.lowText)")
                    legend(color: Color(white: 0.227), text: "\(SPARCFormat.decimal(rates.high, digits: 0))¢ · other hours")
                } else {
                    legend(color: Color(white: 0.227), text: "\(SPARCFormat.decimal(rates.high, digits: 0))¢ · all day")
                }
                legend(color: .white, text: "Now")
            }
            .padding(.top, 12)
            .accessibilityHidden(true)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 28)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(rates.summary)
    }

    private var bars: some View {
        HStack(alignment: .bottom, spacing: 3) {
            ForEach(0..<24, id: \.self) { hour in
                let low = rates.isLow(hour)
                let span = rates.high - rates.low
                let height: CGFloat = span > 0 ? 26 + CGFloat((rates.cents[hour] - rates.low) / span) * 52 : 52
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(hour == currentHour ? Color.white : (low ? SPARCTheme.spark : Color(white: 0.227)))
                    .frame(height: height)
                    .frame(maxWidth: .infinity)
                    .shadow(color: low ? SPARCTheme.spark.opacity(0.45) : .clear, radius: 5)
            }
        }
        .frame(height: 84, alignment: .bottom)
    }

    private func legend(color: Color, text: String) -> some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 3, style: .continuous).fill(color).frame(width: 9, height: 9)
            Text(text).sparcFont(12.5).foregroundStyle(SPARCTheme.muted).fixedSize()
        }
    }
}

// MARK: - Weekly routine card (Plan tab and the schedule sheet)

private struct WeeklyRoutineCard: View {
    @Binding var draft: WeeklySchedule
    var justSaved = false
    var message: String? = nil
    var saveTitle = "Save routine"
    var busy = false
    var onSave: () -> Void
    var onBlockedDay: () -> Void = {}

    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let dayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    private static let dayLetters = ["M", "T", "W", "T", "F", "S", "S"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 10) {
                Text("Weekly routine").sparcFont(17, weight: .semibold).tracking(-0.2)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 8)
                Text(draft.enabled ? "On" : "Paused").sparcFont(12.5).foregroundStyle(SPARCTheme.tertiary)
                Toggle("Weekly routine", isOn: $draft.enabled)
                    .labelsHidden()
                    .tint(SPARCTheme.spark)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("schedule.enabled")
            }
            SPARCSegmented(segments: [SPARCSegment(value: ChargingMode.smart, title: "Smart charging", symbol: "sparkles"),
                                      SPARCSegment(value: ChargingMode.fixed, title: "Fixed start", symbol: "clock")],
                           selection: $draft.mode, height: 44)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Charging mode")
                .accessibilityIdentifier("schedule.mode")
                .padding(.top, 8)
            Text(draft.mode == .smart
                 ? "Starts in the cheapest hours and finishes before you leave."
                 : "Starts at your chosen time and stops 30 minutes before you leave.")
                .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10).padding(.horizontal, 2)

            Text("Charging days").sparcFont(12.5, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                .padding(.top, 18)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: typeSize.isAccessibilitySize ? 4 : 7), spacing: 6) {
                ForEach(1...7, id: \.self) { day in dayChip(day) }
            }
            .padding(.top, 8)
            .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: draft.days)

            divider.padding(.top, 18)
            RoutineStepperRow(title: "Departure",
                              value: SPARCFormat.time(SPARCFormat.clockDate(draft.departureMinutes)),
                              valueID: "schedule.departure",
                              earlierLabel: "Depart 15 minutes earlier", laterLabel: "Depart 15 minutes later",
                              earlierID: "schedule.departure.earlier", laterID: "schedule.departure.later",
                              onEarlier: { stepDeparture(-15) }, onLater: { stepDeparture(15) })
                .padding(.top, 16)

            if draft.mode == .fixed {
                divider.padding(.top, 14)
                RoutineStepperRow(title: "Start charging at",
                                  value: SPARCFormat.time(SPARCFormat.clockDate(draft.fixedStartMinutes)),
                                  valueID: "schedule.fixedStart",
                                  earlierLabel: "Start 30 minutes earlier", laterLabel: "Start 30 minutes later",
                                  earlierID: "schedule.fixedStart.earlier", laterID: "schedule.fixedStart.later",
                                  onEarlier: { stepStart(-30) }, onLater: { stepStart(30) })
                    .padding(.top, 14)
                    .transition(.opacity)
            }

            divider.padding(.top, 16)
            RoutineTargetControl(target: Binding(get: { Double(draft.targetPercent) },
                                                 set: { draft.targetPercent = Int($0) }),
                                 title: "Charge to")
                .padding(.top, 16)

            if let message {
                Text(message).sparcFont(13).foregroundStyle(SPARCTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 14)
                    .accessibilityIdentifier("schedule.validation")
            }

            SPARCActionButton(title: justSaved ? "Saved to your home controller" : saveTitle,
                              symbol: justSaved ? "checkmark" : nil,
                              kind: justSaved ? .tinted : .primary,
                              height: 56, busy: busy, action: onSave)
                .shadow(color: justSaved ? .clear : SPARCTheme.spark.opacity(0.28), radius: 14, y: 10)
                .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: justSaved)
                .padding(.top, 18)
                .accessibilityIdentifier("schedule.save")
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 28)
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: draft.mode)
    }

    private var divider: some View {
        Rectangle().fill(Color(white: 0.118)).frame(height: 1)
    }

    private func dayChip(_ day: Int) -> some View {
        let on = draft.days.contains(day)
        return Button {
            if on {
                if draft.days.count == 1, draft.enabled { onBlockedDay(); return }
                draft.days.remove(day)
            } else {
                draft.days.insert(day)
            }
        } label: {
            Text(Self.dayLetters[day - 1]).sparcFont(14, weight: .bold)
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(on ? SPARCTheme.onSpark : SPARCTheme.tertiary)
                .background(on ? SPARCTheme.spark : SPARCTheme.control, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(on ? SPARCTheme.spark : Color(white: 0.149), lineWidth: 1))
                .shadow(color: on ? SPARCTheme.spark.opacity(0.35) : .clear, radius: 7)
                .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel(Self.dayNames[day - 1])
        .accessibilityValue(on ? "Selected" : "Not selected")
        .accessibilityIdentifier("schedule.day.\(day)")
    }

    private func stepDeparture(_ delta: Int) {
        draft.departureMinutes = min(1425, max(0, draft.departureMinutes + delta))
    }

    /// Fixed starts run from 9 PM to 5 AM; older saved values outside that span just wrap the clock.
    private func stepStart(_ delta: Int) {
        let current = draft.fixedStartMinutes
        let signed = current >= 1260 ? current - 1440 : current
        if (-180...300).contains(signed) {
            let next = signed + delta
            guard (-180...300).contains(next) else { return }
            draft.fixedStartMinutes = (next + 1440) % 1440
        } else {
            draft.fixedStartMinutes = ((current + delta) % 1440 + 1440) % 1440
        }
    }
}

private struct RoutineStepperRow: View {
    let title: String
    let value: String
    let valueID: String
    let earlierLabel: String
    let laterLabel: String
    let earlierID: String
    let laterID: String
    var onEarlier: () -> Void
    var onLater: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).sparcFont(12.5, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                Text(value).sparcFont(28, weight: .bold).tracking(-0.8).monospacedDigit()
                    .lineLimit(1).minimumScaleFactor(0.7)
                    .accessibilityIdentifier(valueID)
            }
            Spacer(minLength: 8)
            HStack(spacing: 8) {
                stepButton(symbol: "minus", label: earlierLabel, id: earlierID, action: onEarlier)
                stepButton(symbol: "plus", label: laterLabel, id: laterID, action: onLater)
            }
        }
    }

    private func stepButton(symbol: String, label: String, id: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 16, weight: .bold))
                .frame(width: 46, height: 46)
                .background(SPARCTheme.control, in: Circle())
                .overlay(Circle().stroke(SPARCTheme.controlLine, lineWidth: 1))
                .contentShape(Circle())
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel(label)
        .accessibilityIdentifier(id)
    }
}

/// Big percentage, native slider from 50 to 100, and a quiet scale.
private struct RoutineTargetControl: View {
    @Binding var target: Double
    var title = "Target battery"

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).sparcFont(12.5, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                Spacer(minLength: 8)
                Text("\(Int(target))%").sparcFont(28, weight: .bold).tracking(-0.8).monospacedDigit()
                    .foregroundStyle(SPARCTheme.spark)
                    .accessibilityIdentifier("schedule.targetValue")
            }
            Slider(value: $target, in: 50...100, step: 5)
                .tint(SPARCTheme.spark)
                .padding(.top, 6)
                .accessibilityLabel(title)
                .accessibilityValue("\(Int(target)) percent")
                .accessibilityIdentifier("schedule.target")
            HStack(spacing: 0) {
                ForEach(Array(["50%", "60", "70", "80", "90", "100%"].enumerated()), id: \.offset) { index, label in
                    if index > 0 { Spacer(minLength: 0) }
                    Text(label).sparcFont(11).foregroundStyle(chargeFootnoteColor)
                }
            }
            .padding(.top, 4)
            .accessibilityHidden(true)
        }
    }
}

// MARK: - Schedule screens (Home calendar button and Settings)

struct CurrentScheduleView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.sparcToast) private var toast

    var body: some View {
        VStack(spacing: 0) {
            if !store.isDemo { ScheduleSourceControl(link: store.sleepCalendar) }
            if store.sleepCalendarStatus?.enabled == true { AppleCalendarPlanView() }
            else if !store.isDemo, store.campaign != nil { CampaignPlanView() }
            else { legacyBody }
        }
    }

    private var legacyBody: some View {
        SchedulePage(title: "Current schedule.") {
            if let error = store.errorMessage { ScheduleNote(text: error, symbol: "exclamationmark.circle", warning: true) }
            summaryCard
            NavigationLink(destination: TodayScheduleEditor()) {
                SPARCNavigationButton(title: "Update just today", symbol: "calendar.badge.clock")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("schedule.today")
            NavigationLink(destination: WeeklyScheduleEditor()) {
                SPARCNavigationButton(title: "Change weekly schedule", symbol: "calendar")
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("schedule.weekly")
            if store.today != nil {
                Button {
                    Task { @MainActor in
                        await store.clearToday()
                        if store.errorMessage == nil { toast("Back to your weekly routine") }
                    }
                } label: {
                    Text("Remove today's update").sparcFont(14, weight: .semibold)
                        .foregroundStyle(SPARCTheme.muted)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(SPARCPressStyle())
                .disabled(store.busy)
                .accessibilityIdentifier("schedule.removeToday")
            }
        }
        .navigationBarTitleDisplayMode(.inline)
    }

    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(store.today == nil ? "YOUR WEEKLY ROUTINE" : "JUST THIS DEPARTURE")
                .sparcFont(12, weight: .semibold).tracking(1).foregroundStyle(SPARCTheme.tertiary)
            Text(store.planWindowText)
                .sparcFont(28, weight: .bold).tracking(-0.8).monospacedDigit()
                .foregroundStyle(SPARCTheme.spark)
                .lineLimit(2).minimumScaleFactor(0.7)
            Text("Estimated charging window").sparcFont(13).foregroundStyle(SPARCTheme.muted)
            WeightedRow(weights: [1, 1], spacing: 10) {
                metric("Target battery", store.displayedTarget)
                metric("Ready by", store.displayedReadyTime)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SPARCTheme.heroGradient, in: RoundedRectangle(cornerRadius: 28, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 28, style: .continuous).stroke(Color(white: 0.149), lineWidth: 1))
    }

    private func metric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).sparcFont(11.5, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            Text(value).sparcFont(20, weight: .bold).tracking(-0.4).monospacedDigit()
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .padding(.horizontal, 12).padding(.vertical, 13)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .sparcSurface(radius: 20)
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
        SchedulePage(title: "Just this departure.") {
            VStack(alignment: .leading, spacing: 18) {
                RoutineTargetControl(target: $target, title: "Target battery")
                Rectangle().fill(Color(white: 0.118)).frame(height: 1)
                DatePicker("Departure", selection: $departure,
                           in: Date()...Date().addingTimeInterval(48 * 3600),
                           displayedComponents: [.date, .hourAndMinute])
                    .datePickerStyle(.compact)
                    .tint(SPARCTheme.spark)
                    .environment(\.timeZone, SPARCFormat.zone)
                    .accessibilityIdentifier("schedule.departure")
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .sparcSurface(radius: 28)
            if let message = validation ?? store.errorMessage {
                Text(message).sparcFont(13).foregroundStyle(SPARCTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("schedule.validation")
            }
            SPARCActionButton(title: "Save today's update", symbol: "checkmark", kind: .primary, height: 56, busy: store.busy) { save() }
                .shadow(color: SPARCTheme.spark.opacity(0.28), radius: 14, y: 10)
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
    @State private var draft: WeeklySchedule = .default
    @State private var loaded = false
    @State private var validation: String?

    var body: some View {
        SchedulePage(title: "Make it your routine.") {
            WeeklyRoutineCard(draft: $draft, message: validation ?? store.errorMessage,
                              saveTitle: "Save routine", busy: store.busy,
                              onSave: save,
                              onBlockedDay: { validation = ScheduleValidationError.days.errorDescription })
        }
        .navigationBarTitleDisplayMode(.inline)
        .disabled(store.busy)
        .task { if !loaded { draft = store.weekly; loaded = true } }
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

// MARK: - Prices (opened from Home)

struct PricesView: View {
    var body: some View { ElectricityRatesView() }
}

// MARK: - Navigation row

struct SPARCNavigationButton: View {
    let title: String
    var subtitle: String? = nil
    var symbol: String

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark)
                .frame(width: 42, height: 42)
                .background(SPARCTheme.spark.opacity(0.12), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).sparcFont(15, weight: .semibold).foregroundStyle(.white)
                if let subtitle {
                    Text(subtitle).sparcFont(12.5).foregroundStyle(SPARCTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 4)
            Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                .foregroundStyle(SPARCTheme.tertiary)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, 16).padding(.vertical, 15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 22)
        .contentShape(Rectangle())
    }
}
