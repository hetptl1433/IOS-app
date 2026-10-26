import SwiftUI

@main
struct SPARCApp: App {
    @StateObject private var store = AppStore()
    @StateObject private var provisioning = ProvisioningManager()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            SPARCRootView()
                .environmentObject(store)
                .environmentObject(provisioning)
                .preferredColorScheme(.dark)
                .task(id: scenePhase) {
                    guard scenePhase == .active else { return }
                    while !Task.isCancelled {
                        await store.refresh()
                        do { try await Task.sleep(for: .seconds(15)) }
                        catch { return }
                    }
                }
        }
    }
}
