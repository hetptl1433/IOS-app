import SwiftUI

// MARK: - Shared drawing (also used by the pairing screen)

/// The Bluetooth rune, drawn as a stroke so it scales with Dynamic Type.
struct ConnectionBluetoothGlyph: Shape {
    func path(in rect: CGRect) -> Path {
        let s = min(rect.width, rect.height) / 24
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + x * s, y: rect.minY + y * s) }
        var path = Path()
        path.move(to: p(7, 7))
        path.addLine(to: p(17, 17))
        path.addLine(to: p(12, 21))
        path.addLine(to: p(12, 3))
        path.addLine(to: p(17, 7))
        path.addLine(to: p(7, 17))
        return path
    }
}

struct ConnectionBluetoothMark: View {
    var size: CGFloat = 18
    var color: Color = SPARCTheme.spark
    var weight: CGFloat = 2.3
    var body: some View {
        ConnectionBluetoothGlyph()
            .stroke(color, style: StrokeStyle(lineWidth: size / 24 * weight, lineCap: .round, lineJoin: .round))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

/// A Raspberry Pi board drawn from shapes. `mini` is the small icon used on device cards.
struct ConnectionPiBoard: View {
    var pcbStroke: Color = SPARCTheme.green
    var pins: Color = SPARCTheme.spark
    var led: Color = SPARCTheme.spark
    var mini = false

    var body: some View {
        Canvas { context, size in
            let scale = min(size.width / 132, size.height / 96)
            func rect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat) -> Path {
                Path(roundedRect: CGRect(x: x * scale, y: y * scale, width: w * scale, height: h * scale),
                     cornerRadius: r * scale, style: .continuous)
            }
            func dot(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat) -> Path {
                Path(ellipseIn: CGRect(x: (x - r) * scale, y: (y - r) * scale, width: r * 2 * scale, height: r * 2 * scale))
            }
            let pcb = Color(red: 15 / 255, green: 26 / 255, blue: 20 / 255)
            let chip = Color(white: 0.102)
            let edge = Color(white: 0.227)
            if mini {
                context.fill(rect(2.5, 2.5, 127, 91, 12), with: .color(pcb))
                context.stroke(rect(2.5, 2.5, 127, 91, 12), with: .color(pcbStroke), lineWidth: 5 * scale)
                context.fill(rect(44, 34, 38, 38, 6), with: .color(chip))
                context.stroke(rect(44, 34, 38, 38, 6), with: .color(pins), lineWidth: 3 * scale)
                context.fill(dot(18, 80, 6), with: .color(led))
            } else {
                context.fill(rect(2, 2, 128, 92, 12), with: .color(pcb))
                context.stroke(rect(2, 2, 128, 92, 12), with: .color(pcbStroke), lineWidth: 2 * scale)
                for row in [CGFloat(11), 17] {
                    for column in 0..<14 { context.fill(dot(16 + CGFloat(column) * 6, row, 1.7), with: .color(pins)) }
                }
                context.fill(rect(44, 36, 36, 36, 6), with: .color(chip))
                context.stroke(rect(44, 36, 36, 36, 6), with: .color(edge), lineWidth: 1.5 * scale)
                context.fill(rect(52, 44, 20, 20, 3), with: .color(Color(white: 0.137)))
                context.fill(rect(88, 42, 18, 14, 3), with: .color(chip))
                context.stroke(rect(88, 42, 18, 14, 3), with: .color(edge), lineWidth: 1.2 * scale)
                for y in [CGFloat(24), 46, 68] { context.fill(rect(114, y, 18, 16, 2), with: .color(Color(white: 0.165))) }
                context.fill(dot(16, 82, 3.4), with: .color(led))
            }
        }
        .aspectRatio(132.0 / 96.0, contentMode: .fit)
        .accessibilityHidden(true)
    }
}

/// Soft static glow behind a screen. It never moves.
struct ConnectionAmbientGlow: View {
    var color: Color
    var top: CGFloat = 30
    var body: some View {
        ZStack(alignment: .top) {
            SPARCTheme.background
            RadialGradient(colors: [color, .clear], center: .center, startRadius: 0, endRadius: 184)
                .frame(width: 368, height: 368)
                .offset(y: top - 60)
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

// MARK: - Connection screen

private enum ConnectionLook: Equatable { case connected, reconnecting, offline, none }

/// Home controller connection: status, sync, reconnect and forget.
struct ConnectionView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var manager: ProvisioningManager
    @Environment(\.sparcToast) private var toast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    /// A sync or reconnect started from this screen is running.
    @State private var working = false
    @State private var showSetup = false
    @State private var confirmForget = false
    @State private var actionError: String?

    private var controller: PairedController? { manager.savedController }

    private var look: ConnectionLook {
        if store.isDemo { return .connected }
        guard controller != nil else { return .none }
        if store.snapshot.state != .offline { return .connected }
        return working ? .reconnecting : .offline
    }

    private var overBluetooth: Bool { store.isDemo || controller?.bluetoothPeripheralID != nil }
    private var transport: String { overBluetooth ? "Bluetooth" : "Home network" }
    /// Pi status and Wi-Fi come over the saved Bluetooth pairing only.
    private var hasBluetoothController: Bool { controller?.bluetoothPeripheralID != nil }

    private var title: String { look == .none ? "No controller yet" : "Home controller" }
    private var subtitle: String {
        if look == .none { return "Connect your Raspberry Pi to charge on your schedule." }
        guard !store.isDemo, let controller else { return "Bluetooth" }
        return "\(controller.deviceName) · Raspberry Pi 4 · \(transport)"
    }
    private var statusText: String {
        switch look {
        case .connected: "Connected"
        case .reconnecting: "Reconnecting…"
        case .offline, .none: "Not connected"
        }
    }
    private var statusColor: Color {
        switch look {
        case .connected: SPARCTheme.spark
        case .reconnecting: SPARCTheme.mint
        case .offline, .none: Color(white: 0.79)
        }
    }
    private var ambient: Color {
        switch look {
        case .connected: SPARCTheme.spark.opacity(0.16)
        case .reconnecting: SPARCTheme.spark.opacity(0.08)
        case .offline, .none: Color(white: 0.47).opacity(0.06)
        }
    }

    /// Facts the Pi itself reported over Bluetooth, e.g. "Wi-Fi: Home · Running for 3h 12m · 42°C".
    private var piFacts: [String] {
        guard !store.isDemo, hasBluetoothController, let status = manager.controllerStatus else { return [] }
        var items: [String] = []
        if let wifi = status.wifi {
            items.append(wifi.connected ? "Wi-Fi: \(wifi.ssid ?? "connected")" : "Wi-Fi: not connected")
        }
        items.append("Running for \(SPARCFormat.duration(status.uptimeSeconds / 60))")
        if let celsius = status.temperatureC { items.append("\(Int(celsius.rounded()))°C") }
        return items
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                hero
                switch look {
                case .connected:
                    facts.padding(.top, 20)
                    if !piFacts.isEmpty { piFactsRow.padding(.top, 10) }
                    syncButton.padding(.top, 12)
                case .reconnecting:
                    reconnectingCard.padding(.top, 20)
                case .offline:
                    offlineActions.padding(.top, 20)
                case .none:
                    SPARCActionButton(title: "Connect a controller", symbol: "plus", kind: .primary, height: 58) { showSetup = true }
                        .accessibilityIdentifier("connection.connect")
                        .padding(.top, 24)
                }
                if look != .none {
                    nightNote.padding(.top, 14)
                    options.padding(.top, 12)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 32)
            .frame(maxWidth: 640)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .background { ConnectionAmbientGlow(color: ambient) }
        .navigationTitle("Connection")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(SPARCTheme.background, for: .navigationBar)
        .navigationDestination(isPresented: $showSetup) { ControllerSetupView() }
        .confirmationDialog("Forget this controller?", isPresented: $confirmForget, titleVisibility: .visible) {
            Button("Forget controller", role: .destructive) { forget() }
            Button("Keep it", role: .cancel) {}
        } message: {
            Text("This phone stops controlling Home controller. The Pi keeps its schedule, and you can connect again any time.")
        }
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: look)
        .task {
            // Fresh Pi facts each time the screen opens, but only while the controller is reachable.
            if !store.isDemo, hasBluetoothController, look == .connected { await manager.refreshStatus() }
        }
    }

    // MARK: Hero

    private var hero: some View {
        VStack(spacing: 0) {
            ConnectionHero(look: look)
            HStack(spacing: 8) {
                if look == .connected { SPARCStatusDot() }
                Text(statusText).sparcFont(14, weight: .bold).tracking(0.14)
                    .foregroundStyle(statusColor)
            }
            .padding(.top, 4)
            .accessibilityIdentifier("connection.status")
            Text(title).sparcFont(28, weight: .bold).tracking(-0.84)
                .multilineTextAlignment(.center)
                .padding(.top, 8)
                .accessibilityAddTraits(.isHeader)
            Text(subtitle).sparcFont(14).foregroundStyle(SPARCTheme.muted)
                .multilineTextAlignment(.center)
                .padding(.top, 4)
                .padding(.horizontal, 12)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
    }

    // MARK: Connected

    private var facts: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(spacing: 8))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 8))
        return layout {
            ConnectionFactTile(title: "Connection") {
                Group {
                    if overBluetooth { ConnectionBluetoothMark(size: 20) }
                    else { Image(systemName: "wifi").font(.system(size: 18, weight: .semibold)).foregroundStyle(SPARCTheme.spark).accessibilityHidden(true) }
                }
                .frame(height: 22, alignment: .leading)
                Text(transport).sparcFont(14, weight: .semibold)
                    .lineLimit(2).minimumScaleFactor(0.85)
            }
            ConnectionFactTile(title: "Last sync") {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    Text(working ? "Syncing" : SPARCFormat.ago(store.snapshot.updatedAt, now: context.date))
                        .sparcFont(20, weight: .bold).tracking(-0.4).monospacedDigit()
                        .foregroundStyle(working ? SPARCTheme.spark : .white)
                        .lineLimit(1).minimumScaleFactor(0.7)
                }
                Text("Automatic").sparcFont(11.5).foregroundStyle(SPARCTheme.tertiary)
            }
            ConnectionFactTile(title: "Security") {
                Image(systemName: "lock").font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(SPARCTheme.spark)
                    .frame(height: 22, alignment: .leading)
                    .accessibilityHidden(true)
                Text("Encrypted").sparcFont(14, weight: .semibold)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    /// One quiet line of real facts from the Pi.
    private var piFactsRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: "cpu").font(.system(size: 12, weight: .semibold))
                .foregroundStyle(SPARCTheme.tertiary)
                .accessibilityHidden(true)
            Text(piFacts.joined(separator: " · ")).sparcFont(12.5).monospacedDigit()
                .foregroundStyle(SPARCTheme.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 6)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("connection.piFacts")
    }

    private var syncButton: some View {
        SPARCActionButton(title: working ? "Syncing…" : "Sync now", symbol: "arrow.clockwise", kind: .tinted, height: 54, busy: working) {
            Task { await sync(announce: "Synced · schedule and charge are up to date") }
        }
        .accessibilityIdentifier("connection.syncNow")
    }

    // MARK: Reconnecting and offline

    private var reconnectingCard: some View {
        HStack(spacing: 12) {
            ProgressView().controlSize(.small).tint(SPARCTheme.spark)
                .frame(width: 30, height: 30)
            Text("Reconnecting to your controller").sparcFont(15, weight: .semibold)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .sparcSurface(radius: 24)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("connection.reconnecting")
    }

    private var offlineActions: some View {
        VStack(spacing: 0) {
            SPARCActionButton(title: "Reconnect", symbol: "arrow.clockwise", kind: .primary, height: 58) {
                Task { await sync(announce: "Connected to Home controller") }
            }
            .shadow(color: SPARCTheme.spark.opacity(0.3), radius: 15, y: 10)
            .accessibilityIdentifier("connection.reconnect")
            Text("Your iPhone tries again on its own every 15 seconds")
                .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .multilineTextAlignment(.center)
                .padding(.top, 10)
            if let message = store.connectionMessage {
                Text(message).sparcFont(12.5).foregroundStyle(SPARCTheme.tertiary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 6)
                    .accessibilityIdentifier("connection.message")
            }
            tips.padding(.top, 14)
        }
    }

    private var tips: some View {
        let items = overBluetooth
            ? ["Bring your phone within about 10 meters of the Pi.",
               "Check that the Pi has power and its light is on.",
               "Turn Bluetooth on in Control Center."]
            : ["Join the same Wi-Fi network as your Pi.",
               "Check that the Pi has power and its light is on.",
               "Allow Local Network access for SPARC in iPhone Settings."]
        return VStack(alignment: .leading, spacing: 0) {
            Text("If it doesn't connect").sparcFont(13, weight: .semibold)
                .foregroundStyle(Color(white: 0.79))
                .padding(.top, 12).padding(.bottom, 4)
                .accessibilityAddTraits(.isHeader)
            ForEach(Array(items.enumerated()), id: \.offset) { index, text in
                if index > 0 { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(index + 1)").sparcFont(13.5, weight: .bold).foregroundStyle(SPARCTheme.spark)
                    Text(text).sparcFont(13.5).foregroundStyle(SPARCTheme.muted)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 9).padding(.bottom, index == items.count - 1 ? 13 : 9)
                .accessibilityElement(children: .combine)
            }
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 22)
    }

    // MARK: Night note and options

    private var nightNote: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "moon").font(.system(size: 15, weight: .semibold))
                .foregroundStyle(SPARCTheme.spark)
                .padding(.top, 1)
                .accessibilityHidden(true)
            Text("Your Pi keeps tonight's plan running even when your phone is away.")
                .sparcFont(13.5).foregroundStyle(Color(white: 0.79))
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 20)
        .accessibilityElement(children: .combine)
    }

    private var options: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let actionError {
                Text(actionError).sparcFont(13).foregroundStyle(SPARCTheme.dangerText)
                    .padding(.horizontal, 4)
                    .accessibilityIdentifier("connection.error")
            }
            VStack(spacing: 0) {
                if hasBluetoothController {
                    NavigationLink { ControllerStatusView() } label: {
                        optionRow("Status & Wi-Fi", chevron: true)
                    }
                    .buttonStyle(SPARCPressStyle())
                    .accessibilityIdentifier("connection.statusWiFi")
                    optionDivider
                }
                Button { showSetup = true } label: {
                    optionRow(controller == nil ? "Connect a controller" : "Connect another controller", chevron: true)
                }
                .buttonStyle(SPARCPressStyle())
                .accessibilityIdentifier("connection.connectAnother")
                if controller != nil {
                    optionDivider
                    Button { actionError = nil; confirmForget = true } label: {
                        optionRow("Forget this controller", color: SPARCTheme.dangerText)
                    }
                    .buttonStyle(SPARCPressStyle())
                    .disabled(store.busy)
                    .accessibilityIdentifier("connection.forget")
                }
            }
            .sparcSurface(radius: 22)
        }
    }

    private var optionDivider: some View { Rectangle().fill(Color(white: 0.11)).frame(height: 1) }

    private func optionRow(_ title: String, color: Color = .white, chevron: Bool = false) -> some View {
        HStack(spacing: 12) {
            Text(title).sparcFont(15, weight: .semibold).foregroundStyle(color)
                .multilineTextAlignment(.leading)
            Spacer(minLength: 8)
            if chevron {
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color(white: 0.43))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        .contentShape(Rectangle())
    }

    // MARK: Actions

    /// Refresh from the controller. A poll that is already running makes `refresh()` return at once,
    /// so wait for the radio to settle and keep the spinner up long enough to read.
    private func sync(announce message: String) async {
        guard !working else { return }
        working = true
        let started = ContinuousClock.now
        await store.refresh()
        var waited = 0
        while (manager.isWorking || manager.stage == .connecting) && !store.isDemo && waited < 60 {
            try? await Task.sleep(for: .milliseconds(400))
            waited += 1
        }
        // Pi facts ride the same Bluetooth link, so ask only once the controller has answered.
        if !store.isDemo, store.snapshot.state != .offline { await manager.refreshStatus() }
        let minimum = Duration.milliseconds(700)
        let elapsed = started.duration(to: .now)
        if elapsed < minimum { try? await Task.sleep(for: minimum - elapsed) }
        working = false
        if store.isDemo || store.snapshot.state != .offline { toast(message) }
    }

    private func forget() {
        store.forgetController()
        manager.forget()
        if manager.savedController == nil {
            actionError = nil
            DispatchQueue.main.async { showSetup = true }
        } else {
            actionError = manager.errorMessage ?? store.errorMessage ?? "Couldn't remove the controller. Try again."
        }
    }
}

// MARK: - Pieces

private struct ConnectionHero: View {
    let look: ConnectionLook

    var body: some View {
        let connected = look == .connected
        let dimmed = look == .offline || look == .none
        let shape = RoundedRectangle(cornerRadius: 30, style: .continuous)
        ZStack {
            if look == .connected {
                Circle().strokeBorder(SPARCTheme.spark.opacity(0.22), lineWidth: 1).frame(width: 200, height: 200)
            }
            if look == .reconnecting {
                Circle().strokeBorder(Color(white: 0.137), lineWidth: 1).frame(width: 200, height: 200)
            }
            shape
                .fill(connected ? Color(red: 13 / 255, green: 23 / 255, blue: 18 / 255) : Color(white: 0.055))
                .overlay(shape.strokeBorder(connected ? SPARCTheme.spark.opacity(0.45) : Color(white: 0.165), lineWidth: 1))
                .shadow(color: connected ? SPARCTheme.spark.opacity(0.25) : .clear, radius: 20)
                .frame(width: 150, height: 118)
                .overlay {
                    ConnectionPiBoard(pcbStroke: connected ? SPARCTheme.green : Color(white: 0.227),
                                      pins: connected ? SPARCTheme.spark : Color(white: 0.29),
                                      led: dimmed ? Color(white: 0.29) : SPARCTheme.spark)
                        .frame(width: 118, height: 86)
                        .opacity(dimmed ? 0.45 : 1)
                }
                .overlay(alignment: .bottomTrailing) { badge.offset(x: 12, y: 12) }
        }
        .frame(width: 240, height: 220)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Raspberry Pi controller")
    }

    private var badge: some View {
        let fill: Color = switch look {
        case .connected: SPARCTheme.spark
        case .reconnecting: Color(red: 30 / 255, green: 58 / 255, blue: 44 / 255)
        case .offline, .none: Color(white: 0.165)
        }
        let glyph: Color = switch look {
        case .connected: SPARCTheme.onSpark
        case .reconnecting: SPARCTheme.spark
        case .offline, .none: SPARCTheme.tertiary
        }
        return ZStack {
            Circle().fill(.black).frame(width: 46, height: 46)
            Circle().fill(fill).frame(width: 40, height: 40)
            ConnectionBluetoothMark(size: 18, color: glyph)
        }
    }
}

private struct ConnectionFactTile<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).sparcFont(11.5, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            content
        }
        .padding(.horizontal, 12).padding(.vertical, 13)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .sparcSurface(radius: 20)
        .accessibilityElement(children: .combine)
    }
}
