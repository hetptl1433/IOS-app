import SwiftUI

/// The approved midnight palette. Green is reserved for data and small symbols.
enum SPARCTheme {
    static let background = Color.black
    static let surface = Color(white: 0.035)
    static let raised = Color(white: 0.09)
    static let line = Color(white: 0.18)
    static let muted = Color(white: 0.66)
    static let green = Color(red: 46 / 255, green: 139 / 255, blue: 99 / 255)
    static let cardGradient = LinearGradient(colors: [Color(white: 0.085), Color(white: 0.025), .black], startPoint: .topLeading, endPoint: .bottomTrailing)
}

struct SPARCCard<Content: View>: View {
    var glossy = false
    var padding: CGFloat = 18
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                if glossy { SPARCTheme.cardGradient }
                else { SPARCTheme.surface }
            }
            .clipShape(RoundedRectangle(cornerRadius: glossy ? 27 : 23, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: glossy ? 27 : 23, style: .continuous).stroke(glossy ? Color(white: 0.26) : SPARCTheme.line, lineWidth: 1))
    }
}

struct SPARCButton: View {
    let title: String
    var symbol: String = "arrow.right"
    var busy = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                if busy { ProgressView().tint(.white) }
                else { Image(systemName: symbol).foregroundStyle(SPARCTheme.green).accessibilityHidden(true) }
                Text(title).sparcFont(15, weight: .semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .foregroundStyle(.white)
            .background(LinearGradient(colors: [Color(white: 0.105), Color(white: 0.045)], startPoint: .top, endPoint: .bottom))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Color(white: 0.23), lineWidth: 1))
        }
        .buttonStyle(SPARCPressStyle())
        .accessibilityLabel(title)
        .disabled(busy)
    }
}

struct SPARCPressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.78 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.975 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: configuration.isPressed)
    }
}

struct SPARCPill: View {
    let title: String
    var symbol: String = "house"
    var body: some View {
        Label(title, systemImage: symbol)
            .sparcFont(10, weight: .medium)
            .foregroundStyle(Color(white: 0.76))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color(white: 0.055), in: Capsule())
            .overlay(Capsule().stroke(Color(white: 0.24)))
    }
}

struct SPARCNotice: View {
    let title: String
    var symbol = "info.circle"
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol).foregroundStyle(SPARCTheme.green)
                .frame(width: 18)
            Text(title).sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(white: 0.055), in: RoundedRectangle(cornerRadius: 15))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(SPARCTheme.line))
    }
}

struct SPARCDetail: View {
    let title: String
    let value: String
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(title).foregroundStyle(SPARCTheme.muted)
            Spacer(minLength: 8)
            Text(value).fontWeight(.medium).multilineTextAlignment(.trailing)
        }
        .sparcFont(13)
        .padding(.vertical, 10)
    }
}

struct SPARCPage<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) { content }
                .padding(.horizontal, 22)
                .padding(.top, 14)
                .padding(.bottom, 24)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
        }
        .background(SPARCTheme.background)
        .scrollIndicators(.hidden)
    }
}

struct SPARCPageTitle: View {
    let title: String
    var subtitle: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).sparcFont(28, weight: .semibold).tracking(-0.8)
                .fixedSize(horizontal: false, vertical: true)
            if let subtitle { Text(subtitle).sparcFont(14).foregroundStyle(SPARCTheme.muted) }
        }
        .padding(.bottom, 4)
        .accessibilityAddTraits(.isHeader)
    }
}

struct BatteryRing: View {
    let percent: Int
    var size: CGFloat = 112
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        ZStack {
            Circle().stroke(Color(white: 0.18), lineWidth: 6)
            Circle().trim(from: 0, to: min(1, max(0, Double(percent) / 100)))
                .stroke(SPARCTheme.green, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                .rotationEffect(.degrees(-90))
            VStack(spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 0) {
                    Text("\(percent)").font(.system(size: size * 0.30, weight: .semibold)).tracking(-1.5)
                    Text("%").font(.system(size: size * 0.15, weight: .medium))
                }.foregroundStyle(SPARCTheme.green)
                Text("battery").sparcFont(10).foregroundStyle(SPARCTheme.muted)
            }
        }
        .padding(4)
        .frame(width: size, height: size)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.65), value: percent)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Battery")
        .accessibilityValue("\(percent) percent")
    }
}

enum SPARCFormat {
    static let zone = TimeZone(identifier: "America/Chicago") ?? .current
    static func time(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.timeZone = zone
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: date)
    }
    static func window(_ start: Date, _ end: Date) -> String { "\(time(start)) – \(time(end))" }
    static func money(_ amount: Double) -> String { amount.formatted(.currency(code: "USD")) }
    static func decimal(_ value: Double, digits: Int = 1) -> String { value.formatted(.number.precision(.fractionLength(digits))) }
    static func cents(_ value: Double) -> String {
        guard value.isFinite else { return "—" }
        if abs(value - value.rounded()) < 0.05, let whole = Int(exactly: value.rounded()) { return "\(whole)" }
        return decimal(value, digits: 1)
    }
    static func duration(_ minutes: Int) -> String { "\(max(0, minutes) / 60)h \(max(0, minutes) % 60)m" }
    static func clockDate(_ minutes: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        return calendar.date(bySettingHour: minutes / 60, minute: minutes % 60, second: 0, of: .now) ?? .now
    }
    static func minutes(_ date: Date) -> Int {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let values = calendar.dateComponents([.hour, .minute], from: date)
        return (values.hour ?? 0) * 60 + (values.minute ?? 0)
    }
}


struct UnavailableBatteryRing: View {
    var size: CGFloat = 112
    var body: some View {
        ZStack {
            Circle().stroke(Color(white: 0.18), lineWidth: 6)
            VStack(spacing: 5) {
                Text("—").sparcFont(32, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                Text("battery").sparcFont(10).foregroundStyle(SPARCTheme.muted)
            }
        }
        .padding(4).frame(width: size, height: size)
        .accessibilityLabel("Battery data unavailable")
    }
}

extension AppStore {
    var showsTelemetry: Bool {
        if profileConnectionMode == .tesla { return hasLiveTelemetry && profileTeslaID == connections?.tesla.vehicleId && activeConnectionMode == .tesla }
        return profileConnectionMode == .virtual ? isVirtualCarActive : hasLiveTelemetry || isVirtualCarActive
    }
    var displayedTarget: String {
        if !isDemo, campaign != nil, let goal = effectiveGoal { return "\(goal.targetPercent)%" }
        if showsTelemetry { return "\(snapshot.targetPercent)%" }
        if let goal = effectiveGoal { return "\(goal.targetPercent)%" }
        return "—"
    }
    var displayedReadyTime: String {
        if !isDemo, campaign != nil { return effectiveGoal.map { SPARCFormat.time($0.departure) } ?? "Not scheduled" }
        if isDemo && effectiveGoal == nil { return "Not scheduled" }
        if showsTelemetry { return SPARCFormat.time(snapshot.readyBy) }
        if let goal = effectiveGoal { return SPARCFormat.time(goal.readyBy) }
        return "—"
    }
    var displayedWindow: String {
        if let window = campaignWindow { return SPARCFormat.window(window.start, window.end) }
        if isDemo && effectiveGoal == nil { return "No charging window scheduled" }
        if isControllerTest && !isVirtualCarActive {
            guard effectiveGoal != nil else { return "No charging window scheduled" }
            return weekly.mode == .fixed ? SPARCFormat.window(snapshot.windowStart, snapshot.windowEnd) : "Use a fixed start time to schedule charging"
        }
        return showsTelemetry ? SPARCFormat.window(snapshot.windowStart, snapshot.windowEnd) : "Awaiting vehicle data"
    }
}


/// Match the approved font sizes at the default setting and honor Dynamic Type.
private struct SPARCScaledFont: ViewModifier {
    @ScaledMetric private var scaledSize: CGFloat
    private let weight: Font.Weight
    private let design: Font.Design

    init(size: CGFloat, weight: Font.Weight, design: Font.Design) {
        _scaledSize = ScaledMetric(wrappedValue: size, relativeTo: .body)
        self.weight = weight
        self.design = design
    }
    func body(content: Content) -> some View {
        content.font(.system(size: scaledSize, weight: weight, design: design))
    }
}

extension View {
    func sparcFont(_ size: CGFloat, weight: Font.Weight = .regular, design: Font.Design = .default) -> some View {
        modifier(SPARCScaledFont(size: size, weight: weight, design: design))
    }
}

// MARK: - Facelift kit
// Calm motion rule: animate only on a user action or while work is actually happening
// (charging, searching, connecting, syncing). No confetti, count-ups, shimmer or loops for decoration.

extension SPARCTheme {
    /// Brighter green for live states, primary actions and data.
    static let spark = Color(red: 82 / 255, green: 227 / 255, blue: 155 / 255)
    static let onSpark = Color(red: 3 / 255, green: 20 / 255, blue: 11 / 255)
    static let sparkText = Color(red: 221 / 255, green: 251 / 255, blue: 234 / 255)
    static let mint = Color(red: 155 / 255, green: 233 / 255, blue: 193 / 255)
    static let card = Color(white: 0.043)
    static let control = Color(white: 0.083)
    static let cardLine = Color(white: 0.133)
    static let controlLine = Color(white: 0.173)
    static let tertiary = Color(white: 0.54)
    static let danger = Color(red: 1, green: 107 / 255, blue: 107 / 255)
    static let dangerText = Color(red: 1, green: 154 / 255, blue: 154 / 255)
    static let warning = Color(red: 1, green: 196 / 255, blue: 138 / 255)
    static let heroGradient = LinearGradient(colors: [Color(white: 0.09), Color(white: 0.031), .black],
                                             startPoint: .topLeading, endPoint: .bottomTrailing)
    static let fillGradient = LinearGradient(colors: [green, spark], startPoint: .leading, endPoint: .trailing)
}

enum SPARCMotion {
    static let quick = Animation.easeOut(duration: 0.25)
    /// Values that change while charging glide instead of jumping.
    static let glide = Animation.easeInOut(duration: 1.2)
    static func calm(_ animation: Animation, _ reduceMotion: Bool) -> Animation? { reduceMotion ? nil : animation }
}

extension View {
    /// The facelift card: near-black fill, hairline border, continuous corners. `tinted` marks savings and good news.
    func sparcSurface(radius: CGFloat = 24, tinted: Bool = false) -> some View {
        background(tinted ? SPARCTheme.spark.opacity(0.07) : SPARCTheme.card,
                   in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous)
                .stroke(tinted ? SPARCTheme.spark.opacity(0.28) : SPARCTheme.cardLine, lineWidth: 1))
    }

    /// Hosts the top toast for everything inside, including views pushed in a NavigationStack.
    func sparcToastHost() -> some View { modifier(SPARCToastHost()) }
}

enum SPARCButtonKind { case primary, secondary, tinted, danger, dangerSolid, plain }

/// Capsule action button. Primary is the bright green; use one per screen.
struct SPARCActionButton: View {
    let title: String
    var symbol: String? = nil
    var kind: SPARCButtonKind = .primary
    var height: CGFloat = 56
    var busy = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                if busy { ProgressView().tint(foreground) }
                else if let symbol { Image(systemName: symbol).font(.system(size: 15, weight: .semibold)).accessibilityHidden(true) }
                Text(title).sparcFont(16, weight: .semibold)
            }
            .frame(maxWidth: .infinity, minHeight: height)
            .foregroundStyle(foreground)
            .background(background, in: Capsule())
            .overlay(Capsule().stroke(border, lineWidth: 1))
        }
        .buttonStyle(SPARCPressStyle())
        .disabled(busy)
        .accessibilityLabel(title)
    }

    private var foreground: Color {
        switch kind {
        case .primary: SPARCTheme.onSpark
        case .tinted: SPARCTheme.sparkText
        case .danger: SPARCTheme.dangerText
        case .dangerSolid: Color(red: 30 / 255, green: 3 / 255, blue: 3 / 255)
        case .secondary, .plain: .white
        }
    }
    private var background: Color {
        switch kind {
        case .primary: SPARCTheme.spark
        case .secondary: SPARCTheme.control
        case .tinted: SPARCTheme.spark.opacity(0.12)
        case .danger: SPARCTheme.danger.opacity(0.08)
        case .dangerSolid: SPARCTheme.danger
        case .plain: .clear
        }
    }
    private var border: Color {
        switch kind {
        case .secondary: SPARCTheme.controlLine
        case .tinted: SPARCTheme.spark.opacity(0.4)
        case .danger: SPARCTheme.danger.opacity(0.35)
        case .primary, .dangerSolid, .plain: .clear
        }
    }
}

/// A still status dot. Status never pings or blinks.
struct SPARCStatusDot: View {
    var color: Color = SPARCTheme.spark
    var size: CGFloat = 8
    var body: some View { Circle().fill(color).frame(width: size, height: size).accessibilityHidden(true) }
}

/// Small capsule label, e.g. "Charging", "Plugged in", "Smart".
struct SPARCChip: View {
    let title: String
    var symbol: String? = nil
    var live = false
    var dot = false
    var body: some View {
        HStack(spacing: 7) {
            if dot { SPARCStatusDot(color: live ? SPARCTheme.spark : Color(white: 0.6), size: 7) }
            if let symbol { Image(systemName: symbol).font(.system(size: 12, weight: .semibold)).accessibilityHidden(true) }
            Text(title).sparcFont(12.5, weight: .bold)
        }
        .padding(.horizontal, 11)
        .frame(minHeight: 30)
        .foregroundStyle(live ? SPARCTheme.spark : Color(white: 0.82))
        .background(live ? SPARCTheme.spark.opacity(0.14) : Color.white.opacity(0.07), in: Capsule())
    }
}

/// Small labeled number used in rows of two or three.
struct SPARCStatTile: View {
    let title: String
    let value: String
    var unit: String? = nil
    var caption: String? = nil
    var tinted = false
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).sparcFont(11.5, weight: .semibold).foregroundStyle(tinted ? SPARCTheme.mint : SPARCTheme.muted)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value).sparcFont(20, weight: .bold).tracking(-0.4).monospacedDigit()
                    .foregroundStyle(tinted ? SPARCTheme.spark : .white)
                if let unit { Text(unit).sparcFont(12).foregroundStyle(SPARCTheme.muted) }
            }
            .lineLimit(1).minimumScaleFactor(0.7)
            if let caption { Text(caption).sparcFont(11.5).foregroundStyle(SPARCTheme.tertiary) }
        }
        .padding(.horizontal, 12).padding(.vertical, 13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 20, tinted: tinted)
        .accessibilityElement(children: .combine)
    }
}

/// Section heading with an optional quiet trailing note.
struct SPARCSectionTitle: View {
    let title: String
    var trailing: String? = nil
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).sparcFont(17, weight: .semibold).tracking(-0.2).accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            if let trailing { Text(trailing).sparcFont(12.5).foregroundStyle(SPARCTheme.tertiary) }
        }
        .padding(.horizontal, 2)
    }
}

struct SPARCSegment<Value: Hashable>: Identifiable {
    let value: Value
    let title: String
    var symbol: String? = nil
    var id: Value { value }
}

/// Segmented control whose highlight slides to the chosen option.
struct SPARCSegmented<Value: Hashable>: View {
    let segments: [SPARCSegment<Value>]
    @Binding var selection: Value
    var height: CGFloat = 40
    @Namespace private var namespace
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 0) {
            ForEach(segments) { segment in
                let selected = segment.value == selection
                Button {
                    withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { selection = segment.value }
                } label: {
                    HStack(spacing: 7) {
                        if let symbol = segment.symbol { Image(systemName: symbol).font(.system(size: 13, weight: .semibold)).accessibilityHidden(true) }
                        Text(segment.title).sparcFont(14, weight: .semibold)
                    }
                    .frame(maxWidth: .infinity, minHeight: height)
                    .foregroundStyle(selected ? .white : SPARCTheme.tertiary)
                    .background {
                        if selected {
                            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color(white: 0.165))
                                .matchedGeometryEffect(id: "selection", in: namespace)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .padding(4)
        .background(Color(white: 0.067), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(SPARCTheme.cardLine))
    }
}

/// Battery bar with a white target notch. The fill glides when a new reading arrives.
struct SPARCBatteryBar: View {
    let percent: Int
    let target: Int
    var height: CGFloat = 12
    var showsTargetLabel = false
    var leadingCaption: String? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .leading) {
                    Capsule().fill(Color(white: 0.114))
                    Capsule().fill(SPARCTheme.fillGradient)
                        .frame(width: max(height, width * CGFloat(min(100, max(0, percent))) / 100))
                        .shadow(color: SPARCTheme.spark.opacity(0.4), radius: 8)
                        .opacity(percent > 0 ? 1 : 0)
                    RoundedRectangle(cornerRadius: 1).fill(.white)
                        .frame(width: 2, height: height + 8)
                        .offset(x: width * CGFloat(target) / 100 - 1)
                }
                .frame(height: height)
                .animation(SPARCMotion.calm(SPARCMotion.glide, reduceMotion), value: percent)
            }
            .frame(height: height + 8)
            if showsTargetLabel || leadingCaption != nil {
                GeometryReader { proxy in
                    ZStack(alignment: .leading) {
                        if let leadingCaption { Text(leadingCaption).sparcFont(11.5).foregroundStyle(SPARCTheme.tertiary) }
                        if showsTargetLabel {
                            Text("\(target)%").sparcFont(11.5).foregroundStyle(Color(white: 0.79))
                                .fixedSize()
                                .position(x: proxy.size.width * CGFloat(target) / 100, y: 8)
                        }
                    }
                }
                .frame(height: 16)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Battery")
        .accessibilityValue("\(percent) percent, target \(target) percent")
    }
}

struct SPARCToastAction {
    fileprivate let show: (String) -> Void
    func callAsFunction(_ message: String) { show(message) }
}

private struct SPARCTabBarTopKey: EnvironmentKey {
    static let defaultValue: CGFloat? = nil
}

extension EnvironmentValues {
    /// Window y of the floating tab bar's top edge for pages shown in a tab; nil in sheets.
    /// A page pushed inside a tab's NavigationStack does not get the bar in its bottom safe area,
    /// so a pinned bottom control uses this to stay above it.
    var sparcTabBarTop: CGFloat? {
        get { self[SPARCTabBarTopKey.self] }
        set { self[SPARCTabBarTopKey.self] = newValue }
    }
}

private struct SPARCToastKey: EnvironmentKey {
    static let defaultValue = SPARCToastAction(show: { _ in })
}

extension EnvironmentValues {
    /// Call `toast("Paused · 0 kW")` after an action succeeds. A no-op outside a toast host.
    var sparcToast: SPARCToastAction {
        get { self[SPARCToastKey.self] }
        set { self[SPARCToastKey.self] = newValue }
    }
}

private struct SPARCToastHost: ViewModifier {
    @State private var message: String?
    @State private var generation = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        content
            .environment(\.sparcToast, SPARCToastAction { text in
                generation += 1
                let shown = generation
                withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { message = text }
                UIAccessibility.post(notification: .announcement, argument: text)
                Task { @MainActor in
                    try? await Task.sleep(for: .seconds(2.6))
                    guard generation == shown else { return }
                    withAnimation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion)) { message = nil }
                }
            })
            .overlay(alignment: .top) {
                if let message {
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark").font(.system(size: 12, weight: .heavy))
                            .foregroundStyle(SPARCTheme.onSpark)
                            .frame(width: 24, height: 24).background(SPARCTheme.spark, in: Circle())
                            .accessibilityHidden(true)
                        Text(message).sparcFont(14, weight: .semibold).foregroundStyle(.white)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 16).padding(.vertical, 13)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                    .background(Color(white: 0.1).opacity(0.85), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(.white.opacity(0.12)))
                    .shadow(color: .black.opacity(0.6), radius: 20, y: 10)
                    .padding(.horizontal, 20).padding(.top, 6)
                    .transition(.opacity.combined(with: .offset(y: -8)))
                    .accessibilityHidden(true)
                    .allowsHitTesting(false)
                }
            }
    }
}

extension SPARCFormat {
    /// "Just now" under a minute, then minutes, then hours. Never ticks by the second.
    static func ago(_ date: Date, now: Date = .now) -> String {
        let seconds = max(0, now.timeIntervalSince(date))
        guard seconds.isFinite else { return "Earlier" }
        if seconds < 60 { return "Just now" }
        if seconds < 3600 { return "\(Int(seconds / 60)) min ago" }
        guard let hours = Int(exactly: floor(seconds / 3600)) else { return "Earlier" }
        return "\(hours) h ago"
    }
}

struct SPARCNight: Identifiable {
    let date: Date
    let charged: Bool
    let isTonight: Bool
    var id: Date { date }
    /// One-letter weekday, e.g. "W".
    var letter: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.timeZone = SPARCFormat.zone
        formatter.dateFormat = "EEEEE"
        return formatter.string(from: date)
    }
}

struct SPARCMilestone: Identifiable {
    let id: String
    let title: String
    let symbol: String
    /// 0...1; 1 means unlocked.
    let progress: Double
    /// Unlock date like "Oct 24", or what is left like "$8.92 to go".
    let detail: String
    var unlocked: Bool { progress >= 1 }
}

extension AppStore {
    var displayVehicleName: String {
        if profileConnectionMode == .tesla { return chargingCarName }
        if let connections, connections.activeMode == .virtual || isVirtualCarActive {
            return connections.virtual.model.title
        }
        if snapshot.controlOnly == true { return snapshot.vehicleName }
        return isDemo || isVirtualCarActive ? "Cybertruck" : snapshot.vehicleName
    }
    var usesCybertruckArtwork: Bool {
        if profileConnectionMode == .tesla { return false }
        if let connections, connections.activeMode == .virtual || isVirtualCarActive {
            return connections.virtual.model == .cybertruck
        }
        return isDemo || snapshot.vehicleName.localizedCaseInsensitiveContains("cybertruck")
    }

    private var chargedNights: Set<Date> {
        let calendar = ScheduleEngine.chicagoCalendar
        return Set(records.map { calendar.startOfDay(for: $0.date) })
    }

    /// Consecutive nights with a recorded charge, ending last night or tonight. 0 when the run is broken.
    var smartNightStreak: Int {
        let calendar = ScheduleEngine.chicagoCalendar
        let nights = chargedNights
        let today = calendar.startOfDay(for: .now)
        guard let yesterday = calendar.date(byAdding: .day, value: -1, to: today) else { return 0 }
        var day = nights.contains(today) ? today : yesterday
        var count = 0
        while nights.contains(day) {
            count += 1
            guard let previous = calendar.date(byAdding: .day, value: -1, to: day) else { break }
            day = previous
        }
        return count
    }

    /// The last `count` nights, oldest first; the final entry is tonight.
    func recentNights(_ count: Int = 7) -> [SPARCNight] {
        let calendar = ScheduleEngine.chicagoCalendar
        let nights = chargedNights
        let today = calendar.startOfDay(for: .now)
        return (0..<count).reversed().compactMap { offset in
            calendar.date(byAdding: .day, value: -offset, to: today).map {
                SPARCNight(date: $0, charged: nights.contains($0), isTonight: offset == 0)
            }
        }
    }

    /// Milestones earned from the charging records, oldest record first.
    var milestones: [SPARCMilestone] {
        let calendar = ScheduleEngine.chicagoCalendar
        let ordered = records.sorted { $0.date < $1.date }
        let short = DateFormatter()
        short.locale = Locale(identifier: "en_US")
        short.timeZone = SPARCFormat.zone
        short.dateFormat = "MMM d"
        var savings = 0.0, energy = 0.0, run = 0, bestRun = 0
        var previous: Date?
        var reached: [String: Date] = [:]
        for record in ordered {
            savings += record.savings
            energy += record.energyKWh
            let night = calendar.startOfDay(for: record.date)
            if let previous, previous == night {
                // Same night: the run is unchanged.
            } else if let previous, calendar.date(byAdding: .day, value: 1, to: previous) == night {
                run += 1
            } else {
                run = 1
            }
            previous = night
            bestRun = max(bestRun, run)
            if reached["first"] == nil { reached["first"] = record.date }
            if savings >= 10, reached["save10"] == nil { reached["save10"] = record.date }
            if savings >= 25, reached["save25"] == nil { reached["save25"] = record.date }
            if energy >= 100, reached["kwh100"] == nil { reached["kwh100"] = record.date }
            if bestRun >= 5, reached["run5"] == nil { reached["run5"] = record.date }
            if bestRun >= 10, reached["run10"] == nil { reached["run10"] = record.date }
        }
        func item(_ id: String, _ title: String, _ symbol: String, _ progress: Double, left: String) -> SPARCMilestone {
            if let date = reached[id] { return SPARCMilestone(id: id, title: title, symbol: symbol, progress: 1, detail: short.string(from: date)) }
            return SPARCMilestone(id: id, title: title, symbol: symbol, progress: min(0.99, max(0, progress)), detail: left)
        }
        return [
            item("first", "First smart charge", "sparkles", ordered.isEmpty ? 0 : 1, left: "Tonight's plan"),
            item("run5", "5 nights in a row", "flame", Double(bestRun) / 5, left: "\(max(0, 5 - bestRun)) to go"),
            item("save10", "$10 saved", "leaf", savings / 10, left: "\(SPARCFormat.money(max(0, 10 - savings))) to go"),
            item("kwh100", "100 kWh shifted", "bolt", energy / 100, left: "\(Int(max(0, 100 - energy).rounded(.up))) kWh to go"),
            item("save25", "$25 saved", "leaf", savings / 25, left: "\(SPARCFormat.money(max(0, 25 - savings))) to go"),
            item("run10", "10 nights in a row", "flame", Double(bestRun) / 10, left: "\(max(0, 10 - bestRun)) to go")
        ]
    }
}

/// A small ⓘ that keeps explanations off the screen until someone asks for them.
struct SPARCInfoButton<Content: View>: View {
    let title: String
    let content: Content
    @State private var shown = false
    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }
    var body: some View {
        Button { shown = true } label: {
            Image(systemName: "info.circle")
                .font(.system(size: 17, weight: .regular))
                .foregroundStyle(SPARCTheme.muted)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .sheet(isPresented: $shown) {
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) { content }
                        .padding(20)
                        .frame(maxWidth: 640, alignment: .leading)
                        .frame(maxWidth: .infinity)
                }
                .background(SPARCTheme.background)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { shown = false } } }
            }
            .presentationDetents([.medium, .large])
            .preferredColorScheme(.dark)
            .tint(.white)
        }
    }
}

/// One label/value line inside an info sheet.
struct SPARCInfoRow: View {
    let label: String
    let value: String
    var emphasized = false
    init(_ label: String, _ value: String, emphasized: Bool = false) {
        self.label = label; self.value = value; self.emphasized = emphasized
    }
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(emphasized ? .white : SPARCTheme.muted)
            Spacer(minLength: 8)
            Text(value).monospacedDigit().foregroundStyle(emphasized ? SPARCTheme.spark : .white)
        }
        .sparcFont(15, weight: emphasized ? .semibold : .regular)
        .padding(.vertical, 11)
        .accessibilityElement(children: .combine)
    }
}
