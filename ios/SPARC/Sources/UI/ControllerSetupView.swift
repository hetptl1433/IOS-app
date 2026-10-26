import SwiftUI

struct ControllerSetupView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var manager: ProvisioningManager
    @Environment(\.dismiss) private var dismiss
    @State private var setupKey = ""
    @State private var ssid = ""
    @State private var password = ""
    @State private var host = "sparc.local"
    @FocusState private var focused: Field?

    private enum Field { case key, ssid, password, host }
    private var working: Bool { manager.isWorking || manager.stage == .connecting || manager.stage == .configuringWiFi }
    private var paired: Bool { manager.stage == .paired || manager.stage == .configuringWiFi || manager.stage == .ready || ((manager.stage == .idle || manager.stage == .scanning) && manager.controllerForSetup != nil) }

    var body: some View {
        SPARCPage {
            SPARCPageTitle(title: "Meet your home controller.", subtitle: "Bluetooth to set up. Wi-Fi to stay connected.")
            stepIndicator
            if let error = manager.errorMessage {
                SPARCNotice(title: error, symbol: "exclamationmark.circle")
                    .accessibilityIdentifier("setup.error")
            }
            if manager.stage == .ready {
                readyCard
            } else {
                discoveryCard
                if manager.stage == .needsKey { pairingCard }
                if (manager.stage == .paired || manager.stage == .configuringWiFi) && manager.connectedDeviceName != nil { wifiCard }
                if manager.controllerForSetup != nil && manager.stage != .connecting && manager.stage != .needsKey { localNetworkCard }
                if !paired && manager.stage != .needsKey {
                    SPARCNotice(title: "Power on your Raspberry Pi 4 with the SPARC controller service running, then keep your iPhone nearby.", symbol: "power")
                }
            }
            if !manager.statusMessage.isEmpty {
                HStack(spacing: 10) {
                    if working { ProgressView().tint(.white) }
                    Text(manager.statusMessage).sparcFont(12).foregroundStyle(SPARCTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }.accessibilityIdentifier("setup.status")
            }
            if manager.stage == .scanning || working || manager.stage == .needsKey || manager.pendingController != nil {
                Button("Cancel connection") { manager.cancel(); setupKey = ""; password = "" }
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                    .frame(maxWidth: .infinity).padding(.vertical, 8)
            }
            Button("Open iPhone permissions") { openSystemSettings() }
                .sparcFont(12).foregroundStyle(SPARCTheme.muted)
                .frame(maxWidth: .infinity).padding(.vertical, 8)
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { host = manager.controllerForSetup?.host ?? "sparc.local" }
        .onChange(of: manager.controllerForSetup?.host) { _, newHost in
            if let newHost { host = newHost }
        }
        .onDisappear {
            setupKey = ""; password = ""
            if manager.stage != .ready { manager.cancel() }
        }
    }

    private var stepIndicator: some View {
        HStack(spacing: 10) {
            setupStep("1", "Find", complete: manager.stage != .idle && manager.stage != .scanning)
            Rectangle().fill(SPARCTheme.line).frame(height: 1)
            setupStep("2", "Pair", complete: paired)
            Rectangle().fill(SPARCTheme.line).frame(height: 1)
            setupStep("3", "Wi-Fi", complete: manager.stage == .ready)
        }
        .accessibilityElement(children: .combine)
    }
    private func setupStep(_ number: String, _ title: String, complete: Bool) -> some View {
        VStack(spacing: 6) {
            Text(complete ? "✓" : number).sparcFont(14, weight: .semibold)
                .foregroundStyle(complete ? SPARCTheme.green : .white)
                .frame(width: 32, height: 32).background(Color(white: 0.08), in: Circle())
                .overlay(Circle().stroke(SPARCTheme.line))
            Text(title).sparcFont(11).foregroundStyle(SPARCTheme.muted)
        }.frame(minWidth: 42)
    }
    private var discoveryCard: some View {
        SPARCCard {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Image(systemName: "antenna.radiowaves.left.and.right").foregroundStyle(SPARCTheme.green)
                    Text("Nearby controllers").sparcFont(16, weight: .semibold)
                    Spacer()
                    if manager.stage == .scanning { ProgressView().tint(.white) }
                }
                if let name = manager.connectedDeviceName {
                    HStack { Text(name).sparcFont(14); Spacer(); Image(systemName: "checkmark.circle").foregroundStyle(SPARCTheme.green) }
                }
                ForEach(manager.devices) { device in
                    Button { manager.connect(device) } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "desktopcomputer").foregroundStyle(SPARCTheme.green)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(device.name).sparcFont(14, weight: .medium)
                                Text("Nearby · Bluetooth").sparcFont(11).foregroundStyle(SPARCTheme.muted)
                            }
                            Spacer()
                            Image(systemName: "chevron.right").sparcFont(11).foregroundStyle(SPARCTheme.muted)
                        }.padding(.vertical, 10).foregroundStyle(.white)
                    }
                    .disabled(working)
                    .accessibilityIdentifier("setup.device.\(device.id.uuidString)")
                }
                if manager.stage == .scanning && manager.devices.isEmpty {
                    Text("Searching nearby… If your controller doesn't appear, check that its Bluetooth setup service is running.")
                        .sparcFont(12).foregroundStyle(SPARCTheme.muted)
                }
                if !paired || manager.devices.isEmpty {
                    SPARCButton(title: manager.stage == .scanning ? "Search again" : "Search for controllers", symbol: "antenna.radiowaves.left.and.right", busy: working) {
                        focused = nil; manager.startScan()
                    }.accessibilityIdentifier("setup.scan")
                }
            }
        }
    }
    private var pairingCard: some View {
        SPARCCard {
            VStack(alignment: .leading, spacing: 16) {
                Label("Trust your controller", systemImage: "lock.shield").sparcFont(16, weight: .semibold)
                Text("Paste the private 64-character setup key from your Raspberry Pi's setup screen or label. This verifies your controller and protects your Wi-Fi details.")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                SecureField("Private setup key", text: $setupKey)
                    .sparcFont(14, design: .monospaced).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .padding(14).background(.black, in: RoundedRectangle(cornerRadius: 12)).overlay(RoundedRectangle(cornerRadius: 12).stroke(SPARCTheme.line))
                    .focused($focused, equals: .key)
                    .accessibilityIdentifier("setup.key")
                SPARCButton(title: "Pair securely", symbol: "lock", busy: working) {
                    focused = nil
                    let key = setupKey.trimmingCharacters(in: .whitespacesAndNewlines)
                    setupKey = ""
                    Task { await manager.pair(setupKey: key) }
                }
                .disabled(setupKey.trimmingCharacters(in: .whitespacesAndNewlines).count != 64)
                .accessibilityIdentifier("setup.pair")
            }
        }
    }
    private var wifiCard: some View {
        SPARCCard {
            VStack(alignment: .leading, spacing: 16) {
                Label("Connect your Pi to Wi-Fi", systemImage: "wifi").sparcFont(16, weight: .semibold)
                Text("Use your home network. Your iPhone should join the same network to reach your controller afterward.")
                    .sparcFont(13).foregroundStyle(SPARCTheme.muted)
                setupField("Network name (SSID)", text: $ssid, field: .ssid)
                    .accessibilityIdentifier("setup.ssid")
                SecureField("Wi-Fi password", text: $password)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .padding(14).background(.black, in: RoundedRectangle(cornerRadius: 12)).overlay(RoundedRectangle(cornerRadius: 12).stroke(SPARCTheme.line))
                    .focused($focused, equals: .password).accessibilityIdentifier("setup.password")
                Text("Use a WPA2/WPA3 personal network password.").sparcFont(11).foregroundStyle(SPARCTheme.muted)
                SPARCButton(title: "Set up Wi-Fi", symbol: "wifi", busy: working) {
                    focused = nil
                    let network = ssid
                    let credential = password
                    password = ""
                    Task { await manager.configureWiFi(ssid: network, password: credential) }
                }
                .disabled(ssid.isEmpty || working)
                .accessibilityIdentifier("setup.wifi")
            }
        }
    }
    private var localNetworkCard: some View {
        SPARCCard {
            VStack(alignment: .leading, spacing: 16) {
                Label("Already on Wi-Fi?", systemImage: "network").sparcFont(16, weight: .semibold)
                Text("Connect to your paired Pi using its local hostname or IP address.").sparcFont(13).foregroundStyle(SPARCTheme.muted)
                setupField("sparc.local or local IP", text: $host, field: .host).keyboardType(.URL).accessibilityIdentifier("setup.host")
                SPARCButton(title: "Connect over Wi-Fi", symbol: "network", busy: working) {
                    focused = nil
                    Task {
                        await manager.connectOnLAN(host: host.trimmingCharacters(in: .whitespacesAndNewlines))
                    }
                }
                .disabled(host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || working)
                .accessibilityIdentifier("setup.connectLAN")
            }
        }
    }
    private var readyCard: some View {
        SPARCCard(glossy: true) {
            VStack(alignment: .leading, spacing: 18) {
                Image(systemName: "checkmark.circle").sparcFont(42, weight: .light).foregroundStyle(SPARCTheme.green)
                Text("Your controller is connected.").sparcFont(26, weight: .semibold).tracking(-0.6)
                Text(manager.savedController?.deviceName ?? "Your Raspberry Pi").sparcFont(14).foregroundStyle(SPARCTheme.muted)
                Text("Your iPhone can now reach your paired home controller. Tesla authorization and live vehicle control come next.")
                    .sparcFont(14).foregroundStyle(SPARCTheme.muted)
                SPARCButton(title: "Use home controller", symbol: "house", busy: store.busy) {
                    store.setDemoMode(false)
                    Task { await store.refresh(); dismiss() }
                }.accessibilityIdentifier("setup.finish")
            }
        }
    }
    private func setupField(_ placeholder: String, text: Binding<String>, field: Field) -> some View {
        TextField(placeholder, text: text)
            .textInputAutocapitalization(.never).autocorrectionDisabled()
            .sparcFont(14)
            .padding(14).background(.black, in: RoundedRectangle(cornerRadius: 12)).overlay(RoundedRectangle(cornerRadius: 12).stroke(SPARCTheme.line))
            .focused($focused, equals: field)
    }
}
