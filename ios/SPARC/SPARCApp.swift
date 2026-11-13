import SwiftUI

@main
struct SPARCApp: App {
    @StateObject private var store: AppStore
    @StateObject private var provisioning: ProvisioningManager

    init() {
        let provisioning = ProvisioningManager()
        _provisioning = StateObject(wrappedValue: provisioning)
        #if DEBUG
        if let store = Self.historyInterfaceTestStore() {
            _store = StateObject(wrappedValue: store)
            return
        }
        #endif
        _store = StateObject(wrappedValue: AppStore(client: ControllerClientRouter(bluetooth: provisioning),
                                                   controllerID: { provisioning.savedController?.deviceID },
                                                   controllerNameProvider: { provisioning.savedController?.displayName }))
    }

    #if DEBUG
    /// `--uitesting --history-interface-test` (debug builds only): a live, non-demo store fed by the
    /// fixed controller results in `HistoryInterfaceFixture`, with its own throwaway defaults, so the
    /// Savings, Records and nightly-schedule screens can be checked without a Pi or the Keychain.
    private static func historyInterfaceTestStore() -> AppStore? {
        let arguments = ProcessInfo.processInfo.arguments
        guard arguments.contains("--uitesting"), arguments.contains("--history-interface-test") else { return nil }
        let suite = "sparc.history-interface-test"
        UserDefaults.standard.removePersistentDomain(forName: suite)
        guard let defaults = UserDefaults(suiteName: suite) else { return nil }
        return AppStore(defaults: defaults, client: HistoryInterfaceFixture(), now: { HistoryInterfaceFixture.now },
                        arguments: [], controllerID: { "interface-test" }, controllerNameProvider: { nil })
    }
    #endif
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            SPARCRootView()
                .environmentObject(store)
                .environmentObject(provisioning)
                .preferredColorScheme(.dark)
                .onOpenURL { url in Task { await store.handleTeslaCallback(url) } }
                .task(id: scenePhase) {
                    guard scenePhase == .active else { return }
                    while !Task.isCancelled {
                        await store.refresh()
                        do { try await Task.sleep(for: .seconds(4)) }
                        catch { return }
                    }
                }
        }
    }
}
