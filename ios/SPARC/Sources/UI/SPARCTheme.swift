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
    var showsTelemetry: Bool { isDemo || hasLiveTelemetry }
    var displayedTarget: String {
        if showsTelemetry { return "\(snapshot.targetPercent)%" }
        if let goal = effectiveGoal { return "\(goal.targetPercent)%" }
        return "—"
    }
    var displayedReadyTime: String {
        if isDemo && effectiveGoal == nil { return "Not scheduled" }
        if showsTelemetry { return SPARCFormat.time(snapshot.readyBy) }
        if let goal = effectiveGoal { return SPARCFormat.time(goal.readyBy) }
        return "—"
    }
    var displayedWindow: String {
        if isDemo && effectiveGoal == nil { return "No charging window scheduled" }
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
