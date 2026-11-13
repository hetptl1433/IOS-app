# Apple sleep calendar

## Manual editing and source selection — November 12 follow-up

Plan and the schedule page now expose **Manual / Calendar event**. Selecting
Manual asks to stop Calendar control, waits for the Pi acknowledgment and
canonical read-back, then opens the existing manual editor. The phone clears the
link immediately after a successful disable acknowledgment so a failed read-back
cannot silently turn Calendar on again. Ownership rejection retains Calendar
mode and displays the reason.

A live target edit 80% → 81% reached the Pi while the routine was paused; it was
restored to 80%, then the original Calendar event was re-linked with 14 windows.
The three new transition tests passed in the full **152-test real-iPhone suite**.
No Calendar event was changed for QA.

**This is a Calendar-event connection, not Health Sleep Schedule / Wind Down or
Clock wake-alarm mirroring.** Apple documents Sleep triggers in Shortcuts, past
sleep samples in HealthKit, and app-created alarms in AlarmKit; no supported
forthcoming Health/Clock timetable feed was established for this implementation.
Native automatic mirroring remains unimplemented. See the current QA report for
sources and limitations.


SPARC reads a designated Calendar event: its start is bedtime, its end is wake time. It aims to finish 30 minutes before waking. This uses Calendar events, separate from Health's Sleep Schedule.

Settings → Apple sleep calendar requests Calendar access, lists calendars and upcoming timed events, and asks which event to use. Recurring events include upcoming occurrences. A native event editor can prepare a daily sleep event; the owner must choose/save it. Event names and Calendar identifiers remain on the phone. Only opaque window IDs, absolute start/end times, enabled state and target percentage go to the paired Pi.

The iPhone checks event-store changes while SPARC is open and when the app returns to the foreground. It does not promise continuous background sync. Up to 14 future windows are persisted on the Pi; the controller advances them without the phone. Deleting an event, removing access or exhausting stored windows leaves no invented charging window. Reconnect to sync further events.

A Calendar link belongs to its phone installation and paired controller. Another phone cannot replace an active link. Different-Pi selection cannot send the first Pi's sleep windows to it. Disconnect requires the original Pi and restores its previous weekly schedule. An older finite nightly campaign stays paused.

Calendar scheduling currently controls the virtual car only. It never starts/stops a real Tesla and never resets the car's battery. Editing the current event preserves its energy/progress, archives before a new window, invalidates the old price plan and replans with TOD distribution. Overlaps, all-day/short/oversized/expired/naive timestamps and extra private fields are rejected.

## Interface

`set_sleep_windows` requires encrypted paired LAN/Bluetooth metadata:

```json
{"enabled":true,"windows":[{"id":"opaque-occurrence-id","start":"2026-11-12T22:00:00-06:00","departure":"2026-11-13T07:00:00-06:00"}],"targetPercent":80}
```

`get_settings` and the loopback display include `sleepCalendar` with enabled, last update, remaining count, next bedtime/wake time, source and `automaticTesla:false`. No event title is returned. Canonical Calendar state takes precedence over the paused finite campaign in the phone's Plan tab.

Apple's public APIs: [Calendar access](https://developer.apple.com/documentation/eventkit/accessing-calendar-using-eventkit-and-eventkitui), [change notifications](https://developer.apple.com/documentation/foundation/nsnotification/name-swift.struct/ekeventstorechanged). iOS requires full Calendar permission to read events. Identifiers can change on a full Calendar resync; missing selections hold rather than linking to an unrelated event.

## Verification status

Python tests cover validation, owner isolation, persistence/restart, window transitions, deleted/exhausted events, progress preservation and disconnect. iPhone tests cover the virtual-car connection guard, canonical wake/ready-time precedence and private-field-free payloads. The owner granted Calendar permission and approved saving a daily Sleep event in Home. Linking it through SPARC produced live Pi acknowledgement: enabled=true, windowCount=14, nextStart=2026-11-13T04:00:00Z, nextWake=2026-11-13T13:00:00Z and readyBy=2026-11-13T12:30:00Z (10pm, 7am and 6:30am Chicago). Battery remained 80%. The event title stayed off the Pi. A later manual Calendar edit/foreground round trip remains to be checked.
