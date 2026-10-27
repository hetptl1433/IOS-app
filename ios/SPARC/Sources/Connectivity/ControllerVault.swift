import Foundation
import Security

struct PairedController: Codable, Equatable, Sendable {
    let deviceID: String
    let deviceName: String
    var host: String
    var port: Int = 8766
    let setupKey: String
    /// Missing in existing saved pairings: those continue to use the LAN transport.
    var bluetoothPeripheralID: String? = nil
}

protocol ControllerCredentialStore: Sendable {
    func load() throws -> PairedController?
    func save(_ controller: PairedController) throws
    func clear() throws
}

/// The pairing credential stays on this device and is unavailable while it is locked.
final class ControllerVault: ControllerCredentialStore, @unchecked Sendable {
    static let shared = ControllerVault()
    private let lock = NSLock()
    private let service = "app.sparc.controller"
    private var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "paired-controller-v1"] }
    func load() throws -> PairedController? {
        lock.lock(); defer { lock.unlock() }
        var q = query; q[kSecReturnData as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw ControllerConnectionError.unavailable("Unlock your iPhone to access the paired controller.") }
        return try JSONDecoder().decode(PairedController.self, from: data)
    }
    func save(_ controller: PairedController) throws {
        lock.lock(); defer { lock.unlock() }
        let data = try JSONEncoder().encode(controller)
        let attributes: [String: Any] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound { status = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil) }
        guard status == errSecSuccess else { throw ControllerConnectionError.unavailable("The pairing could not be saved securely. Unlock your iPhone and retry.") }
    }
    func clear() throws {
        lock.lock(); defer { lock.unlock() }
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw ControllerConnectionError.unavailable("The saved pairing could not be removed.") }
    }
}
