import Foundation
import Combine
import EventKit
import CryptoKit

struct SleepCalendarStatus: Codable, Equatable, Sendable {
    var enabled: Bool
    var updatedAt: Date?
    var windowCount: Int
    var nextStart: Date?
    var nextWake: Date?
    var source: String
    var automaticTesla: Bool
    func validate() throws {
        guard source == "Apple Calendar", !automaticTesla, (0...14).contains(windowCount),
              updatedAt?.timeIntervalSince1970.isFinite ?? true,
              nextStart?.timeIntervalSince1970.isFinite ?? true,
              nextWake?.timeIntervalSince1970.isFinite ?? true,
              (nextStart == nil && nextWake == nil) || (nextStart != nil && nextWake != nil && nextWake! > nextStart!)
        else { throw ControllerConnectionError.invalidMessage }
    }
}

struct SleepWindow: Codable, Equatable, Sendable {
    var id: String
    var start: Date
    var departure: Date
    func validate(now: Date) throws {
        guard !id.isEmpty, id.count <= 96, id.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }),
              start.timeIntervalSince1970.isFinite, departure.timeIntervalSince1970.isFinite,
              (1800...86400).contains(departure.timeIntervalSince(start)), departure > now,
              start >= now.addingTimeInterval(-86400), departure <= now.addingTimeInterval(32*86400)
        else { throw ControllerConnectionError.invalidMessage }
    }
}
struct SleepWindowUpdate: Codable, Equatable, Sendable {
    var enabled: Bool
    var windows: [SleepWindow]
    var targetPercent: Int
    func validate(now: Date = Date()) throws {
        guard windows.count <= 14, (50...100).contains(targetPercent), Set(windows.map(\.id)).count == windows.count else { throw ControllerConnectionError.invalidMessage }
        for w in windows { try w.validate(now: now) }
        for pair in zip(windows, windows.dropFirst()) where pair.0.departure > pair.1.start { throw ControllerConnectionError.invalidMessage }
    }
}

/// Calendar identifiers stay on this phone. Only opaque window IDs and times reach its paired Pi.
@MainActor final class AppleSleepCalendar: ObservableObject {
    struct Selection: Codable { var calendarID: String; var itemID: String; var externalID: String?; var controllerID: String }
    @Published private(set) var calendars: [EKCalendar] = []
    @Published private(set) var events: [EKEvent] = []
    @Published private(set) var selection: Selection?
    @Published private(set) var message = "Choose the Calendar event that marks your sleep time."
    @Published private(set) var lastSynced: Date?
    @Published private(set) var nextWindow: SleepWindow?
    @Published private(set) var working = false
    let eventStore = EKEventStore()
    private let defaults: UserDefaults
    private var observer: NSObjectProtocol?
    private var lastAttempt = Date.distantPast
    private var dirty = true
    private var lastSent: SleepWindowUpdate?
    private let key = "sparc.appleSleepCalendar.v1"
    init(defaults: UserDefaults) {
        self.defaults = defaults
        if let data = defaults.data(forKey: key) { selection = try? JSONDecoder().decode(Selection.self, from: data) }
        observer = NotificationCenter.default.addObserver(forName: .EKEventStoreChanged, object: eventStore, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.dirty = true }
        }
    }
    var hasAccess: Bool { EKEventStore.authorizationStatus(for: .event) == .fullAccess }
    func requestAccess() async {
        do {
            guard try await eventStore.requestFullAccessToEvents() else { message = "Calendar access is off. You can allow it in iPhone Settings."; return }
            loadCalendars()
        } catch { message = error.localizedDescription }
    }
    func loadCalendars() {
        guard hasAccess else { return }
        calendars = eventStore.calendars(for: .event).sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
        dirty = true
    }
    func loadEvents(calendarID: String, now: Date = Date()) {
        guard hasAccess, let calendar = eventStore.calendar(withIdentifier: calendarID) else { events = []; return }
        let predicate = eventStore.predicateForEvents(withStart: now.addingTimeInterval(-86400), end: now.addingTimeInterval(30*86400), calendars: [calendar])
        let found = eventStore.events(matching: predicate).filter { !$0.isAllDay && $0.endDate > now && (1800...86400).contains($0.endDate.timeIntervalSince($0.startDate)) }.sorted { $0.startDate < $1.startDate }
        var seen = Set<String>()
        events = Array(found.filter { seen.insert($0.calendarItemIdentifier).inserted }.prefix(100))
    }
    func choose(_ event: EKEvent, store: AppStore) async {
        guard let controllerID = store.calendarControllerID, !store.isDemo else { message = "Connect to your Pi first."; return }
        selection = Selection(calendarID: event.calendar.calendarIdentifier, itemID: event.calendarItemIdentifier, externalID: event.calendarItemExternalIdentifier, controllerID: controllerID)
        defaults.set(try? JSONEncoder().encode(selection), forKey: key)
        lastSent = nil; dirty = true
        await refresh(store: store, force: true)
    }
    @discardableResult
    func disconnect(store: AppStore) async -> Bool {
        guard !working else { return false }
        if let selection, selection.controllerID != store.calendarControllerID {
            message = "Reconnect to the Pi linked here before disconnecting Calendar."; return false
        }
        working = true
        defer { working = false }
        do {
            try await store.sendSleepWindows(SleepWindowUpdate(enabled: false, windows: [], targetPercent: max(50, store.weekly.targetPercent)))
        } catch { message = error.localizedDescription; return false }
        // The Pi acknowledged disabling sync. Forget the link before read-back, so
        // an unavailable read cannot silently enable Calendar again on foreground.
        selection = nil; defaults.removeObject(forKey: key); lastSent = nil; nextWindow = nil
        do {
            try await store.refreshScheduleState()
            guard store.sleepCalendarStatus?.enabled == false else {
                message = "The Pi has not confirmed manual scheduling. Reconnect and try again."; return false
            }
            message = "Manual scheduling is ready. Edit and save your schedule."
            return true
        } catch {
            message = "Calendar is off on the Pi. Reconnect to load your manual schedule."
            return false
        }
    }
    func refresh(store: AppStore, force: Bool = false) async {
        guard !working, let selected = selection, selected.controllerID == store.calendarControllerID, !store.isDemo,
              force || dirty || Date().timeIntervalSince(lastAttempt) >= 30 else { return }
        working = true; lastAttempt = Date(); dirty = false
        defer { working = false }
        do {
            let now = Date()
            var windows: [SleepWindow] = []
            if hasAccess, let calendar = eventStore.calendar(withIdentifier: selected.calendarID) {
                let predicate = eventStore.predicateForEvents(withStart: now.addingTimeInterval(-86400), end: now.addingTimeInterval(30*86400), calendars: [calendar])
                let found = eventStore.events(matching: predicate).filter {
                    $0.calendarItemIdentifier == selected.itemID || (selected.externalID != nil && $0.calendarItemExternalIdentifier == selected.externalID)
                }.sorted { $0.startDate < $1.startDate }
                windows = Array(found.filter { !$0.isAllDay && $0.endDate > now && (1800...86400).contains($0.endDate.timeIntervalSince($0.startDate)) }.prefix(14)).map { event in
                    let occurrence = event.hasRecurrenceRules || event.isDetached ? String((event.occurrenceDate ?? event.startDate).timeIntervalSince1970) : "single"
                    let identity = selected.itemID + "|" + occurrence
                    let id = SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x", $0) }.joined()
                    return SleepWindow(id: id, start: event.startDate, departure: event.endDate)
                }
            }
            let update = SleepWindowUpdate(enabled: true, windows: windows, targetPercent: max(50, store.weekly.targetPercent))
            try update.validate(now: now)
            if update != lastSent || force {
                try await store.sendSleepWindows(update)
                guard selection?.controllerID == selected.controllerID, store.calendarControllerID == selected.controllerID else { return }
                lastSent = update; lastSynced = Date()
            }
            nextWindow = windows.first
            message = windows.isEmpty ? "No upcoming sleep event is available. The Pi is holding this Calendar schedule without a charging window." : "Synced \(windows.count) upcoming sleep event\(windows.count == 1 ? "" : "s") to your Pi."
        } catch { dirty = true; message = error.localizedDescription }
    }
}
