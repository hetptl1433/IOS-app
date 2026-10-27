import SwiftUI

/// Connect a controller: search nearby, tap it, and the pairing finishes by itself when the Pi offers a pairing code.
/// A setup key or a home-network address stays available underneath for everything else.
struct ControllerSetupView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var manager: ProvisioningManager
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var setupKey = ""
    @State private var host = "sparc.local"
    @State private var pairAnotherController = false
    /// Nothing the manager is doing counts as ours until the owner starts something on this screen.
    /// A saved Bluetooth controller keeps the manager in `.ready` (or briefly `.connecting`) in the background.
    @State private var startedHere = false
    @State private var searched = false
    @State private var codeOpen = false
    @State private var code = ""
    /// A pairing code typed before connecting; it is used once the controller asks for a key.
    @State private var stashedCode = ""
    @FocusState private var focused: Field?

    private enum Field { case key, lanKey, code, host }
    private enum Phase { case search, found, linking, done }

    private var stage: ProvisioningStage { startedHere ? manager.stage : .idle }
    private var connectedName: String? { startedHere ? manager.connectedDeviceName : nil }
    private var working: Bool { startedHere && (manager.isWorking || manager.stage == .connecting) }
    private var needsKey: Bool { stage == .needsKey && !manager.isWorking }
    private var visibleDevices: [DiscoveredController] {
        startedHere && searched && (stage == .scanning || stage == .idle) ? manager.devices : []
    }
    private var visibleError: String? { startedHere ? manager.errorMessage : nil }
    private var hasSaved: Bool { manager.controllerForSetup != nil }
    private var inlinePinned: Bool { typeSize.isAccessibilitySize }
    private var editingNetworkField: Bool { focused == .host || focused == .lanKey }

    private var phase: Phase {
        if stage == .ready { return .done }
        if working { return .linking }
        if stage == .needsKey || !visibleDevices.isEmpty { return .found }
        return .search
    }

    private var noControllerFound: Bool { phase == .search && searched && stage == .idle && visibleError == nil }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                hero.padding(.top, 8)
                VStack(spacing: 14) { content }.padding(.top, 20)
                if inlinePinned { pinned.padding(.top, 20) }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
            .frame(maxWidth: 640)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .scrollDismissesKeyboard(.interactively)
        .background { ConnectionAmbientGlow(color: SPARCTheme.spark.opacity(phase == .done ? 0.22 : 0.10), top: 110) }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !inlinePinned && !editingNetworkField {
                pinned
                    .padding(.horizontal, 16).padding(.top, 10).padding(.bottom, 8)
                    .frame(maxWidth: 640)
                    .frame(maxWidth: .infinity)
                    .background(SPARCTheme.background)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(SPARCTheme.background, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .principal) { SetupStepDots(current: phase == .done ? 2 : phase == .linking ? 1 : 0) }
        }
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: phase)
        .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: codeOpen)
        .onAppear { host = manager.controllerForSetup?.host ?? "sparc.local" }
        .onChange(of: manager.controllerForSetup?.host) { _, newHost in
            if let newHost { host = newHost }
        }
        .onChange(of: manager.stage) { _, newStage in
            if newStage == .idle || newStage == .scanning { stashedCode = "" }
            submitStashedCode()
        }
        .onChange(of: manager.isWorking) { _, _ in submitStashedCode() }
        .onDisappear {
            setupKey = ""; code = ""; stashedCode = ""
            if startedHere && manager.stage != .ready { manager.cancel() }
        }
    }

    // MARK: Hero

    @ViewBuilder private var hero: some View {
        switch phase {
        case .search, .found:
            VStack(spacing: 0) {
                SetupRadar(scanning: stage == .scanning && visibleDevices.isEmpty && !reduceMotion,
                           foundDot: phase == .found)
                Text(heroTitle).sparcFont(28, weight: .bold).tracking(-0.84)
                    .multilineTextAlignment(.center)
                    .padding(.top, 18)
                    .accessibilityAddTraits(.isHeader)
                Text(heroSubtitle).sparcFont(15).foregroundStyle(SPARCTheme.muted)
                    .multilineTextAlignment(.center)
                    .lineSpacing(3)
                    .padding(.top, 8).padding(.horizontal, 16)
            }
            .frame(maxWidth: .infinity)
            .transition(.opacity)
        case .linking:
            linkingHero.transition(.opacity)
        case .done:
            doneHero.transition(.opacity)
        }
    }

    private var heroTitle: String {
        if needsKey { return "Enter the pairing code" }
        if phase == .found { return visibleDevices.count > 1 ? "Found your controllers" : "Found your controller" }
        if stage == .scanning { return "Looking nearby…" }
        if noControllerFound { return "No controller found" }
        return "Meet your home controller."
    }

    private var heroSubtitle: String {
        if needsKey { return "Enter the pairing code from your controller, or its private 64-character setup key." }
        if phase == .found { return visibleDevices.count > 1 ? "Tap the one you want to connect." : "Tap it to connect." }
        if stage == .scanning { return "Keep your phone near your Raspberry Pi." }
        if noControllerFound { return "Bring it nearby and open its setup window, then search again." }
        return "Connect nearby with Bluetooth, or use your home network."
    }

    // MARK: Linking

    private enum StepState { case done, active, pending }
    private var linkSteps: [(title: String, state: StepState)] {
        switch stage {
        case .needsKey:
            return [("Connecting over Bluetooth", .done), ("Securing the connection", .active)]
        case .connecting where connectedName != nil:
            return [("Connecting over Bluetooth", .active), ("Securing the connection", .pending)]
        default:
            return [("Connecting to your controller", .active)]
        }
    }

    private var linkingHero: some View {
        let steps = linkSteps
        let finished = steps.filter { $0.state == .done }.count
        let progress = max(0.06, Double(finished) / Double(steps.count))
        let overBluetooth = connectedName != nil || stage == .needsKey
        return VStack(spacing: 0) {
            ZStack {
                Circle().stroke(Color(white: 0.11), lineWidth: 10)
                Circle().trim(from: 0, to: progress)
                    .stroke(SPARCTheme.spark, style: StrokeStyle(lineWidth: 10, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .shadow(color: SPARCTheme.spark.opacity(0.5), radius: 5)
                    .animation(SPARCMotion.calm(SPARCMotion.quick, reduceMotion), value: progress)
                if overBluetooth { ConnectionBluetoothMark(size: 46, color: SPARCTheme.spark, weight: 2) }
                else {
                    Image(systemName: "network").font(.system(size: 40, weight: .regular))
                        .foregroundStyle(SPARCTheme.spark).accessibilityHidden(true)
                }
            }
            .padding(5)
            .frame(width: 200, height: 200)
            .accessibilityHidden(true)
            Text("Connecting securely").sparcFont(26, weight: .bold).tracking(-0.8)
                .multilineTextAlignment(.center)
                .padding(.top, 22)
                .accessibilityAddTraits(.isHeader)
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                    HStack(spacing: 12) {
                        stepMark(step.state)
                        Text(step.title).sparcFont(15, weight: .semibold)
                            .foregroundStyle(step.state == .pending ? Color(white: 0.48) : .white)
                        Spacer(minLength: 0)
                    }
                    .padding(.vertical, 9)
                    .accessibilityElement(children: .combine)
                    .accessibilityValue(step.state == .done ? "Done" : step.state == .active ? "In progress" : "Waiting")
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .sparcSurface(radius: 22)
            .padding(.top, 18)
        }
        .frame(maxWidth: .infinity)
    }

    private func stepMark(_ state: StepState) -> some View {
        ZStack {
            switch state {
            case .done:
                Circle().fill(SPARCTheme.spark)
                Image(systemName: "checkmark").font(.system(size: 12, weight: .heavy)).foregroundStyle(SPARCTheme.onSpark)
            case .active:
                ProgressView().controlSize(.small).tint(SPARCTheme.spark)
            case .pending:
                Circle().stroke(Color(white: 0.18), lineWidth: 2)
            }
        }
        .frame(width: 28, height: 28)
        .accessibilityHidden(true)
    }

    // MARK: Done

    private var doneHero: some View {
        let overBluetooth = manager.savedController?.bluetoothPeripheralID != nil
        let name = manager.savedController?.deviceName ?? "Your home controller"
        return VStack(spacing: 0) {
            ZStack {
                Circle().fill(SPARCTheme.spark)
                    .shadow(color: SPARCTheme.spark.opacity(0.25), radius: 20)
                Image(systemName: "checkmark").font(.system(size: 52, weight: .bold)).foregroundStyle(SPARCTheme.onSpark)
            }
            .frame(width: 120, height: 120)
            .padding(15)
            .padding(.top, 20)
            .accessibilityHidden(true)
            Text("You're connected.").sparcFont(32, weight: .bold).tracking(-1.1)
                .multilineTextAlignment(.center)
                .padding(.top, 20)
                .accessibilityAddTraits(.isHeader)
            Text("\(name) is ready. You can control charging and your schedule from this iPhone.")
                .sparcFont(15.5).foregroundStyle(SPARCTheme.muted)
                .multilineTextAlignment(.center)
                .lineSpacing(3)
                .padding(.top, 10).padding(.horizontal, 12)
            SetupChipRow(spacing: 8) {
                SetupChip(title: "Encrypted") { Image(systemName: "lock").font(.system(size: 12, weight: .semibold)) }
                SetupChip(title: overBluetooth ? "Bluetooth" : "Home network") {
                    if overBluetooth { ConnectionBluetoothMark(size: 13, color: SPARCTheme.spark, weight: 2.2) }
                    else { Image(systemName: "wifi").font(.system(size: 12, weight: .semibold)) }
                }
                SetupChip(title: "Reconnects by itself") { Image(systemName: "arrow.clockwise").font(.system(size: 12, weight: .semibold)) }
            }
            .padding(.top, 18)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: Below the hero

    @ViewBuilder private var content: some View {
        if let error = visibleError, phase != .done {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.circle").font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(SPARCTheme.warning)
                    .accessibilityHidden(true)
                Text(error).sparcFont(13).foregroundStyle(Color(white: 0.79))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .sparcSurface(radius: 18)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("setup.error")
        }
        if showsStatus, !manager.statusMessage.isEmpty {
            HStack(spacing: 10) {
                if working { ProgressView().controlSize(.small).tint(.white) }
                Text(manager.statusMessage).sparcFont(12.5).foregroundStyle(SPARCTheme.muted)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity)
            .accessibilityIdentifier("setup.status")
        }
        if (phase == .search || phase == .found) && stage != .needsKey && connectedName == nil {
            if !hasSaved || pairAnotherController {
                lanPairingCard
            } else {
                reconnectCard
                quietButton("Pair another controller") {
                    manager.cancel(); setupKey = ""; searched = false; pairAnotherController = true
                }
                .disabled(working)
                .accessibilityIdentifier("setup.pairAnother")
            }
        }
        if phase == .linking || manager.pendingController != nil {
            quietButton("Cancel connection") { cancelSetup() }
        }
        if phase == .done, manager.savedController?.bluetoothPeripheralID != nil {
            NavigationLink(destination: ControllerStatusView()) {
                HStack(spacing: 12) {
                    Image(systemName: "wifi").font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(SPARCTheme.spark)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Status & Wi-Fi").sparcFont(15, weight: .semibold)
                        Text("See your Pi's connection and set up its Wi-Fi")
                            .sparcFont(12.5).foregroundStyle(SPARCTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Color(white: 0.43))
                        .accessibilityHidden(true)
                }
                .padding(.horizontal, 16).padding(.vertical, 12)
                .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
                .sparcSurface(radius: 22)
                .contentShape(Rectangle())
            }
            .buttonStyle(SPARCPressStyle())
            .accessibilityIdentifier("setup.status")
        }
        if phase != .done {
            quietButton("Open iPhone permissions", size: 12.5) { openIPhoneSettings() }
        }
    }

    private var showsStatus: Bool {
        guard startedHere else { return false }
        switch phase {
        case .linking: return true
        case .found: return needsKey
        case .search: return searched && stage != .scanning && !noControllerFound
        case .done: return false
        }
    }

    // MARK: Pinned actions

    @ViewBuilder private var pinned: some View {
        switch phase {
        case .search:
            if stage == .scanning {
                quietButton("Cancel") { cancelSetup() }
            } else {
                SPARCActionButton(title: searched ? "Search again" : "Search for controllers",
                                  symbol: "magnifyingglass", kind: .primary, height: 58, busy: working) { startSearch() }
                    .shadow(color: SPARCTheme.spark.opacity(0.3), radius: 15, y: 10)
                    .accessibilityIdentifier("setup.scan")
            }
        case .found:
            if needsKey { pairingPanel } else { foundActions }
        case .linking:
            EmptyView()
        case .done:
            SPARCActionButton(title: "Start charging smarter", symbol: "bolt.fill", kind: .primary, height: 58, busy: store.busy) {
                store.setDemoMode(false)
                Task { await store.refresh(); dismiss() }
            }
            .shadow(color: SPARCTheme.spark.opacity(0.3), radius: 15, y: 10)
            .accessibilityIdentifier("setup.finish")
        }
    }

    private var foundActions: some View {
        VStack(spacing: 8) {
            if !codeOpen || visibleDevices.count > 1 {
                ForEach(visibleDevices) { device in deviceCard(device) }
            }
            if codeOpen, let first = visibleDevices.first {
                codePanel(for: first)
            } else {
                quietButton("Use a pairing code instead") { codeOpen = true; focused = .code }
                    .accessibilityIdentifier("setup.useCode")
            }
            if stage != .scanning {
                quietButton("Search again") { startSearch() }
                    .accessibilityIdentifier("setup.scan")
            }
        }
    }

    private func deviceCard(_ device: DiscoveredController) -> some View {
        Button { connect(device) } label: {
            HStack(spacing: 14) {
                ConnectionPiBoard(pcbStroke: SPARCTheme.green, pins: SPARCTheme.spark, led: SPARCTheme.spark, mini: true)
                    .frame(width: 34, height: 25)
                    .frame(width: 52, height: 52)
                    .background(Color(red: 15 / 255, green: 26 / 255, blue: 20 / 255), in: RoundedRectangle(cornerRadius: 17, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 17, style: .continuous).stroke(SPARCTheme.spark.opacity(0.4), lineWidth: 1))
                VStack(alignment: .leading, spacing: 2) {
                    Text(device.name).sparcFont(17, weight: .bold).foregroundStyle(.white)
                        .lineLimit(2)
                    Text(signalText(device)).sparcFont(13).foregroundStyle(SPARCTheme.mint)
                }
                Spacer(minLength: 8)
                Text("Connect").sparcFont(15, weight: .bold).foregroundStyle(SPARCTheme.onSpark)
                    .padding(.horizontal, 16)
                    .frame(minHeight: 40)
                    .background(SPARCTheme.spark, in: Capsule())
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                LinearGradient(stops: [.init(color: Color(red: 19 / 255, green: 38 / 255, blue: 28 / 255), location: 0),
                                       .init(color: Color(red: 10 / 255, green: 18 / 255, blue: 14 / 255), location: 0.6),
                                       .init(color: Color(red: 6 / 255, green: 8 / 255, blue: 7 / 255), location: 1)],
                               startPoint: .topLeading, endPoint: .bottomTrailing),
                in: RoundedRectangle(cornerRadius: 26, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous).stroke(SPARCTheme.spark.opacity(0.4), lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
        .disabled(working)
        .accessibilityLabel("Connect to \(device.name)")
        .accessibilityIdentifier("setup.device.\(device.id.uuidString)")
    }

    private func signalText(_ device: DiscoveredController) -> String {
        guard device.rssi < 0 else { return "Nearby · Bluetooth" }
        let strength = device.rssi >= -60 ? "Strong signal" : device.rssi >= -75 ? "Good signal" : "Weak signal"
        return "Nearby · \(strength)"
    }

    /// The code is typed before connecting and used as soon as the controller asks for a key.
    private func codePanel(for device: DiscoveredController) -> some View {
        let digits = code.trimmingCharacters(in: .whitespacesAndNewlines)
        let valid = ControllerPairingCode.setupKey(for: digits) != nil
        return VStack(alignment: .leading, spacing: 0) {
            Text(visibleDevices.count == 1 ? "Pairing code for \(device.name)" : "Pairing code")
                .sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            TextField("0000", text: $code)
                .keyboardType(.numberPad).textContentType(.oneTimeCode)
                .multilineTextAlignment(.center)
                .sparcFont(30, weight: .bold).tracking(15)
                .padding(.leading, 15)
                .frame(minHeight: 62)
                .background(.black, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Color(white: 0.2), lineWidth: 1))
                .focused($focused, equals: .code)
                .onChange(of: code) { _, value in
                    let digitsOnly = String(value.filter(\.isNumber).prefix(8))
                    if digitsOnly != value { code = digitsOnly }
                }
                .padding(.top, 10)
                .accessibilityLabel("Pairing code")
                .accessibilityIdentifier("setup.code")
            SPARCActionButton(title: "Connect", kind: .primary, height: 54) { connect(device) }
                .opacity(valid ? 1 : 0.45)
                .disabled(!valid || working)
                .padding(.top, 12)
                .accessibilityIdentifier("setup.codeConnect")
        }
        .padding(18)
        .setupPanel()
    }

    private var pairingPanel: some View {
        let entered = setupKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let canPair = entered.count == 64 || ControllerPairingCode.setupKey(for: entered) != nil
        return VStack(alignment: .leading, spacing: 0) {
            Text("Pairing code or setup key").sparcFont(13, weight: .semibold).foregroundStyle(SPARCTheme.muted)
            SecureField("Pairing code or setup key", text: $setupKey)
                .sparcFont(18, weight: .semibold, design: .monospaced)
                .multilineTextAlignment(.center)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .padding(.horizontal, 14)
                .frame(minHeight: 58)
                .background(.black, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Color(white: 0.2), lineWidth: 1))
                .focused($focused, equals: .key)
                .padding(.top, 10)
                .accessibilityIdentifier("setup.key")
            SPARCActionButton(title: "Pair securely", symbol: "lock", kind: .primary, height: 54, busy: working) {
                focused = nil
                let key = setupKey.trimmingCharacters(in: .whitespacesAndNewlines)
                setupKey = ""
                Task { await manager.pair(setupKey: key) }
            }
            .opacity(canPair ? 1 : 0.45)
            .disabled(!canPair)
            .padding(.top, 12)
            .accessibilityIdentifier("setup.pair")
            quietButton("Cancel connection") { cancelSetup() }
                .padding(.top, 2)
        }
        .padding(18)
        .setupPanel()
    }

    // MARK: Network cards

    private var lanPairingCard: some View {
        let key = setupKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let address = host.trimmingCharacters(in: .whitespacesAndNewlines)
        let ready = key.count == 64 && !address.isEmpty && !working
        return VStack(alignment: .leading, spacing: 14) {
            Label("Pair on your home network", systemImage: "network").sparcFont(15, weight: .semibold)
            Text("Connect your controller by Ethernet or Wi-Fi, then join the same network on your iPhone. Enter its .local hostname or private IPv4 address and its private setup key. Bluetooth is optional.")
                .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
            setupField("sparc.local or local IP", text: $host, field: .host)
                .keyboardType(.URL).accessibilityIdentifier("setup.host")
            SecureField("Private setup key", text: $setupKey)
                .sparcFont(14, design: .monospaced).textInputAutocapitalization(.never).autocorrectionDisabled()
                .setupInput()
                .focused($focused, equals: .lanKey).accessibilityIdentifier("setup.lanKey")
            Text("Open the setup window on your controller before pairing. Your key is saved securely only after the controller responds with proof that it knows the key.")
                .sparcFont(12).foregroundStyle(SPARCTheme.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            SPARCActionButton(title: "Pair over home network", symbol: "lock.shield", kind: .secondary, height: 52, busy: working) {
                focused = nil
                startedHere = true
                let key = setupKey.trimmingCharacters(in: .whitespacesAndNewlines)
                let address = host.trimmingCharacters(in: .whitespacesAndNewlines)
                setupKey = ""
                Task { await manager.pairOnLAN(host: address, setupKey: key) }
            }
            .opacity(ready ? 1 : 0.45)
            .disabled(!ready)
            .accessibilityIdentifier("setup.pairLAN")
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 24)
    }

    private var reconnectCard: some View {
        let address = host.trimmingCharacters(in: .whitespacesAndNewlines)
        return VStack(alignment: .leading, spacing: 14) {
            if manager.controllerForSetup?.bluetoothPeripheralID != nil {
                Label("Reconnect over Bluetooth", systemImage: "antenna.radiowaves.left.and.right").sparcFont(15, weight: .semibold)
                Text("Keep your Pi nearby and Bluetooth enabled on your iPhone.")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                SPARCActionButton(title: "Connect over Bluetooth", symbol: "arrow.clockwise", kind: .tinted, height: 52, busy: working) {
                    focused = nil
                    startedHere = true
                    Task { await manager.reconnectSavedController() }
                }
                .accessibilityIdentifier("setup.connectBluetooth")
            } else {
                Label("Reconnect your controller", systemImage: "network").sparcFont(15, weight: .semibold)
                Text("Connect to your paired controller using its .local hostname or private IPv4 address.")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                setupField("sparc.local or local IP", text: $host, field: .host)
                    .keyboardType(.URL).accessibilityIdentifier("setup.host")
                SPARCActionButton(title: "Connect over home network", symbol: "network", kind: .tinted, height: 52, busy: working) {
                    focused = nil
                    startedHere = true
                    Task { await manager.connectOnLAN(host: address) }
                }
                .opacity(address.isEmpty || working ? 0.45 : 1)
                .disabled(address.isEmpty || working)
                .accessibilityIdentifier("setup.connectLAN")
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .sparcSurface(radius: 24)
    }

    // MARK: Actions

    private func startSearch() {
        focused = nil
        setupKey = ""; code = ""; stashedCode = ""
        codeOpen = false
        pairAnotherController = false
        startedHere = true
        searched = true
        manager.startScan()
    }

    private func connect(_ device: DiscoveredController) {
        focused = nil
        startedHere = true
        searched = true
        let entered = codeOpen ? code.trimmingCharacters(in: .whitespacesAndNewlines) : ""
        stashedCode = ControllerPairingCode.setupKey(for: entered) != nil ? entered : ""
        code = ""
        manager.connect(device)
    }

    /// Hands a code typed before connecting to the controller once it asks for a key and nothing else is pairing.
    private func submitStashedCode() {
        guard startedHere, !stashedCode.isEmpty, manager.stage == .needsKey, !manager.isWorking else { return }
        let entered = stashedCode
        stashedCode = ""
        Task { await manager.pair(setupKey: entered) }
    }

    private func cancelSetup() {
        manager.cancel()
        setupKey = ""; code = ""; stashedCode = ""
        pairAnotherController = false
        codeOpen = false
        searched = false
        focused = nil
    }

    private func quietButton(_ title: String, size: CGFloat = 14, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).sparcFont(size, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                .frame(maxWidth: .infinity, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(SPARCPressStyle())
    }

    private func setupField(_ placeholder: String, text: Binding<String>, field: Field) -> some View {
        TextField(placeholder, text: text)
            .textInputAutocapitalization(.never).autocorrectionDisabled()
            .sparcFont(14)
            .setupInput()
            .focused($focused, equals: field)
    }

    private func openIPhoneSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}

// MARK: - Pieces

private extension View {
    func setupInput() -> some View {
        padding(14)
            .background(.black, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Color(white: 0.2), lineWidth: 1))
    }

    func setupPanel() -> some View {
        background(Color(white: 0.063), in: RoundedRectangle(cornerRadius: 26, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous).stroke(Color(white: 0.165), lineWidth: 1))
    }
}

/// Three still rings around the Bluetooth mark. The single slow pulse runs only while searching.
private struct SetupRadar: View {
    let scanning: Bool
    let foundDot: Bool

    var body: some View {
        ZStack {
            Circle().strokeBorder(Color(white: 0.118), lineWidth: 1).frame(width: 280, height: 280)
            Circle().strokeBorder(Color(white: 0.118), lineWidth: 1).frame(width: 196, height: 196)
            Circle().strokeBorder(Color(white: 0.137), lineWidth: 1).frame(width: 112, height: 112)
            if scanning { SetupPulse().frame(width: 280, height: 280) }
            if foundDot {
                Circle().fill(SPARCTheme.spark).frame(width: 14, height: 14)
                    .shadow(color: SPARCTheme.spark, radius: 8)
                    .offset(x: 59, y: -63)
                    .transition(.opacity)
            }
            ZStack {
                Circle().fill(Color(red: 15 / 255, green: 26 / 255, blue: 20 / 255))
                Circle().strokeBorder(SPARCTheme.spark.opacity(0.45), lineWidth: 1)
                ConnectionBluetoothMark(size: 32, color: SPARCTheme.spark, weight: 2)
            }
            .frame(width: 76, height: 76)
            .shadow(color: SPARCTheme.spark.opacity(0.25), radius: 15)
        }
        .frame(width: 280, height: 280)
        .accessibilityHidden(true)
    }
}

/// One ring that grows and fades over three seconds, easing out. Only mounted while a search is running.
private struct SetupPulse: View {
    @State private var start = Date()

    var body: some View {
        TimelineView(.animation) { context in
            let elapsed = context.date.timeIntervalSince(start)
            let t = elapsed.truncatingRemainder(dividingBy: 3) / 3
            let eased = 1 - pow(1 - t, 3)
            Circle().strokeBorder(SPARCTheme.spark.opacity(0.35), lineWidth: 1.5)
                .scaleEffect(0.45 + 0.55 * eased)
                .opacity(0.5 * (1 - eased))
        }
    }
}

private struct SetupStepDots: View {
    let current: Int

    var body: some View {
        HStack(spacing: 6) {
            ForEach(0..<3, id: \.self) { index in
                Capsule()
                    .fill(index <= current ? SPARCTheme.spark : Color(white: 0.18))
                    .frame(width: index == current ? 22 : 6, height: 6)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Step \(current + 1) of 3")
    }
}

private struct SetupChip<Icon: View>: View {
    let title: String
    @ViewBuilder var icon: Icon

    var body: some View {
        HStack(spacing: 6) {
            icon.foregroundStyle(SPARCTheme.spark).accessibilityHidden(true)
            Text(title).sparcFont(12.5, weight: .semibold).foregroundStyle(Color(white: 0.79))
        }
        .padding(.horizontal, 12)
        .frame(minHeight: 32)
        .background(Color(white: 0.067), in: Capsule())
        .overlay(Capsule().stroke(Color(white: 0.149), lineWidth: 1))
    }
}

/// Centers chips and wraps them onto new lines when they do not fit.
private struct SetupChipRow: Layout {
    var spacing: CGFloat = 8

    private struct Row { var indices: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func rows(width: CGFloat, subviews: Subviews) -> [Row] {
        var result: [Row] = [Row()]
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let gap = result[result.count - 1].indices.isEmpty ? 0 : spacing
            if result[result.count - 1].width + gap + size.width > width, !result[result.count - 1].indices.isEmpty {
                result.append(Row())
            }
            let last = result.count - 1
            result[last].width += (result[last].indices.isEmpty ? 0 : spacing) + size.width
            result[last].height = max(result[last].height, size.height)
            result[last].indices.append(index)
        }
        return result
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let limit = proposal.width ?? .infinity
        let laidOut = rows(width: limit, subviews: subviews)
        let height = laidOut.map(\.height).reduce(0, +) + CGFloat(max(0, laidOut.count - 1)) * spacing
        let widest = laidOut.map(\.width).max() ?? 0
        return CGSize(width: limit.isFinite ? limit : widest, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(width: bounds.width, subviews: subviews) {
            var x = bounds.minX + (bounds.width - row.width) / 2
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }
}
