import SwiftUI
import EventKit
import EventKitUI

struct AppleSleepCalendarView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View { AppleSleepCalendarContent(link: store.sleepCalendar).environmentObject(store) }
}
private struct AppleSleepCalendarContent: View {
    @EnvironmentObject private var store: AppStore
    @ObservedObject var link: AppleSleepCalendar
    @State private var calendarID = ""
    @State private var eventToConfirm: EKEvent?
    @State private var creating = false
    var body: some View {
        Form {
            if !link.hasAccess {
                Section { Button("Connect Apple Calendar") { Task { await link.requestAccess() } } }
            } else {
                Section("Designated sleep event") {
                    Picker("Calendar", selection: $calendarID) {
                        Text("Choose a calendar").tag("")
                        ForEach(link.calendars, id: \.calendarIdentifier) { calendar in Text(calendar.title).tag(calendar.calendarIdentifier) }
                    }
                    if !calendarID.isEmpty {
                        if link.events.isEmpty { Text("No timed events in the next 30 days.").foregroundStyle(.secondary) }
                        ForEach(link.events, id: \.calendarItemIdentifier) { event in
                            Button { eventToConfirm = event } label: {
                                VStack(alignment: .leading) {
                                    Text(event.title ?? "Sleep event")
                                    Text(event.startDate.formatted(date: .abbreviated, time: .shortened) + " – " + event.endDate.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                                    if event.hasRecurrenceRules { Text("Includes upcoming repeats").font(.caption).foregroundStyle(.secondary) }
                                }
                            }
                        }
                    }
                    Button("Create a sleep event…") { creating = true }
                }
            }
            Section("Sync status") {
                Text(link.message)
                if let window = link.nextWindow {
                    LabeledContent("Bedtime", value: window.start.formatted(date: .abbreviated, time: .shortened))
                    LabeledContent("Wake time", value: window.departure.formatted(date: .abbreviated, time: .shortened))
                }
                if let date = link.lastSynced { LabeledContent("Last synced", value: date.formatted(date: .omitted, time: .shortened)) }
                if link.selection != nil {
                    Button("Sync now") { Task { await link.refresh(store: store, force: true) } }.disabled(link.working)
                    Button("Disconnect sleep event", role: .destructive) { Task { await link.disconnect(store: store) } }.disabled(link.working)
                }
            }
        }
        .navigationTitle("Sleep calendar event")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                SPARCInfoButton("About sleep calendar") {
                    Text("A sleep event’s start is bedtime and its end is wake time. SPARC aims to finish 30 minutes before you wake.")
                    Text("Edits sync while SPARC is open or when you return to it. Your Pi keeps the last synced windows. This uses Calendar events, separate from Health’s Sleep Schedule.")
                    Text("Only event times and anonymous window IDs go to the Pi. Event names and calendar contents stay on your phone.")
                    Text("Currently schedules the virtual car. No automatic Tesla commands.")
                }
            }
        }
        .task { link.loadCalendars(); calendarID = link.selection?.calendarID ?? ""; link.loadEvents(calendarID: calendarID); await link.refresh(store: store) }
        .onChange(of: calendarID) { _, id in link.loadEvents(calendarID: id) }
        .confirmationDialog("Use this sleep event for charging?", isPresented: Binding(get: { eventToConfirm != nil }, set: { if !$0 { eventToConfirm = nil } }), titleVisibility: .visible) {
            if let event = eventToConfirm { Button("Use for charging") { Task { await link.choose(event, store: store) }; eventToConfirm = nil } }
        } message: { Text("This replaces the Pi’s current schedule with this event’s times.") }
        .sheet(isPresented: $creating, onDismiss: { link.loadCalendars(); link.loadEvents(calendarID: calendarID) }) { SleepEventEditor(store: link.eventStore) }
    }
}
private struct SleepEventEditor: UIViewControllerRepresentable {
    let store: EKEventStore
    @Environment(\.dismiss) private var dismiss
    func makeCoordinator() -> Coordinator { Coordinator { dismiss() } }
    func makeUIViewController(context: Context) -> EKEventEditViewController {
        let editor = EKEventEditViewController(); editor.eventStore = store; editor.editViewDelegate = context.coordinator
        let event = EKEvent(eventStore: store); event.title = "Sleep"
        let calendar = Calendar.current
        let start = calendar.nextDate(after: Date(), matching: DateComponents(hour: 22), matchingPolicy: .nextTime) ?? Date()
        event.startDate = start; event.endDate = calendar.date(byAdding: .hour, value: 9, to: start)
        event.recurrenceRules = [EKRecurrenceRule(recurrenceWith: .daily, interval: 1, end: nil)]
        editor.event = event
        return editor
    }
    func updateUIViewController(_ uiViewController: EKEventEditViewController, context: Context) {}
    final class Coordinator: NSObject, EKEventEditViewDelegate {
        let finish: () -> Void
        init(finish: @escaping () -> Void) { self.finish = finish }
        func eventEditViewController(_ controller: EKEventEditViewController, didCompleteWith action: EKEventEditViewAction) { finish() }
    }
}

struct AppleCalendarPlanView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        NavigationStack {
            Form {
                Section("Calendar event schedule") {
                    Text(store.chargingCarName)
                    if let value = store.sleepCalendarStatus, let start = value.nextStart, let wake = value.nextWake {
                        LabeledContent("Bedtime", value: start.formatted(date: .abbreviated, time: .shortened))
                        LabeledContent("Wake time", value: wake.formatted(date: .abbreviated, time: .shortened))
                        LabeledContent("Ready by", value: wake.addingTimeInterval(-1800).formatted(date: .abbreviated, time: .shortened))
                        LabeledContent("Target", value: "\(store.weekly.targetPercent)%")
                        LabeledContent("Upcoming events", value: "\(value.windowCount)")
                    } else { Text("No upcoming sleep event.") }
                    NavigationLink("Manage sleep event") { AppleSleepCalendarView() }
                }
            }.navigationTitle("Charging plan")
        }
    }
}


/// Scheduling source stays visible even when Calendar owns the current plan.
struct ScheduleSourceControl: View {
    @EnvironmentObject private var store: AppStore
    @ObservedObject var link: AppleSleepCalendar
    @State private var confirmManual = false
    @State private var showCalendar = false
    @State private var switchError: String?
    private var calendarActive: Bool { store.sleepCalendarStatus?.enabled == true }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Schedule source").sparcFont(12, weight: .semibold).foregroundStyle(SPARCTheme.muted)
                Spacer()
                SPARCInfoButton("Apple Sleep and scheduling") {
                    Text("Manual lets you edit charging times in SPARC. Calendar event follows the sleep event you choose.")
                    Text("SPARC does not mirror the built-in Health Sleep Schedule, Wind Down or Clock wake alarm. Calendar is a separate event, and its edits sync while SPARC is open or when you return to it.")
                    Link("Apple Sleep automation options", destination: URL(string: "https://support.apple.com/guide/shortcuts/apd932ff833f/ios")!)
                }
            }
            Picker("Schedule source", selection: Binding(get: { calendarActive }, set: { useCalendar in
                if useCalendar { showCalendar = true }
                else if calendarActive { confirmManual = true }
            })) {
                Text("Manual").tag(false)
                Text("Calendar event").tag(true)
            }.pickerStyle(.segmented).accessibilityIdentifier("plan.scheduleSource")
                .disabled(store.busy || link.working || store.connectionsIsStale)
        }
        .padding(.horizontal, 16).padding(.bottom, 8).background(.black)
        .confirmationDialog("Use manual scheduling?", isPresented: $confirmManual, titleVisibility: .visible) {
            Button("Use manual scheduling") {
                Task { if !(await link.disconnect(store: store)) { switchError = link.message } }
            }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Calendar will stop controlling charging. You can then edit and save your schedule in SPARC.") }
        .alert("Couldn't switch scheduling", isPresented: Binding(get: { switchError != nil }, set: { if !$0 { switchError = nil } })) {
            Button("OK") { switchError = nil }
        } message: { Text(switchError ?? "Reconnect to your Pi and try again.") }
        .sheet(isPresented: $showCalendar) {
            NavigationStack {
                AppleSleepCalendarView()
                    .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Done") { showCalendar = false } } }
            }.preferredColorScheme(.dark).tint(SPARCTheme.spark)
        }
    }
}
