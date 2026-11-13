import Foundation

enum ScheduleEngine {
    static var chicagoCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Chicago")!
        calendar.locale = Locale(identifier: "en_US_POSIX")
        return calendar
    }

    /// The first selected departure strictly after now, including daylight-saving transitions.
    /// In a spring gap, use the next valid clock time. In a repeated hour, use the first occurrence.
    static func nextDeparture(for schedule: WeeklySchedule, after now: Date,
                              calendar: Calendar = chicagoCalendar) -> Date? {
        guard (try? schedule.validate()) != nil, schedule.enabled else { return nil }
        let start = calendar.startOfDay(for: now)
        for offset in 0...7 {
            guard let day = calendar.date(byAdding: .day, value: offset, to: start) else { continue }
            let weekday = (calendar.component(.weekday, from: day) + 5) % 7 + 1
            guard schedule.days.contains(weekday),
                  let departure = calendar.date(bySettingHour: schedule.departureMinutes / 60,
                                                minute: schedule.departureMinutes % 60, second: 0,
                                                of: day, matchingPolicy: .nextTime,
                                                repeatedTimePolicy: .first, direction: .forward),
                  departure > now else { continue }
            return departure
        }
        return nil
    }

    static func effectiveGoal(weekly: WeeklySchedule, today: TodayOverride?,
                              now: Date = Date()) -> ScheduleGoal? {
        if let today, (try? today.validate(now: now)) != nil {
            return ScheduleGoal(targetPercent: today.targetPercent, departure: today.departure,
                                readyBy: today.departure.addingTimeInterval(-30 * 60), isOverride: true)
        }
        guard let departure = nextDeparture(for: weekly, after: now) else { return nil }
        return ScheduleGoal(targetPercent: weekly.targetPercent, departure: departure,
                            readyBy: departure.addingTimeInterval(-30 * 60), isOverride: false)
    }

    /// A design-demo estimate, not a controller plan. Anchor overnight starts to the
    /// ready deadline so a late-evening start belongs to the preceding calendar day.
    static func demoWindow(for goal: ScheduleGoal, weekly: WeeklySchedule,
                           remainingMinutes: Int, now: Date) -> DateInterval {
        let duration = Double(max(0, remainingMinutes)) * 60
        let latestStart = goal.readyBy.addingTimeInterval(-duration)
        let minutes = weekly.mode == .fixed ? weekly.fixedStartMinutes : 60
        let calendar = chicagoCalendar
        var preferred = calendar.date(bySettingHour: minutes / 60, minute: minutes % 60,
                                      second: 0, of: goal.readyBy,
                                      matchingPolicy: .nextTime, repeatedTimePolicy: .first,
                                      direction: .forward) ?? latestStart
        if weekly.mode == .fixed {
            if preferred > latestStart,
               let previousDay = calendar.date(byAdding: .day, value: -1, to: goal.readyBy) {
                preferred = calendar.date(bySettingHour: minutes / 60, minute: minutes % 60,
                                          second: 0, of: previousDay,
                                          matchingPolicy: .nextTime, repeatedTimePolicy: .first,
                                          direction: .forward) ?? latestStart
            }
        } else {
            preferred = min(preferred, latestStart)
        }
        // A newly edited demo goal never advertises a charging window in the past.
        // If there is too little time, the estimate can finish after the ready buffer.
        let start = max(now, preferred)
        return DateInterval(start: start, duration: duration)
    }

    /// The virtual example plan respects the chosen fixed start and the ready
    /// deadline even when there is not enough time to reach the battery target.
    static func virtualWindow(for goal: ScheduleGoal, weekly: WeeklySchedule,
                              remainingMinutes: Int, now: Date) -> DateInterval {
        let calendar = chicagoCalendar
        let minutes = weekly.mode == .fixed ? weekly.fixedStartMinutes : 60
        var preferred = calendar.date(bySettingHour: minutes / 60, minute: minutes % 60,
                                      second: 0, of: goal.readyBy, matchingPolicy: .nextTime,
                                      repeatedTimePolicy: .first, direction: .forward) ?? goal.readyBy
        if weekly.mode == .fixed, preferred > goal.readyBy,
           let previousDay = calendar.date(byAdding: .day, value: -1, to: goal.readyBy) {
            preferred = calendar.date(bySettingHour: minutes / 60, minute: minutes % 60,
                                      second: 0, of: previousDay, matchingPolicy: .nextTime,
                                      repeatedTimePolicy: .first, direction: .forward) ?? goal.readyBy
        } else if weekly.mode == .smart {
            preferred = min(preferred, goal.readyBy.addingTimeInterval(-Double(max(0, remainingMinutes)) * 60))
        }
        let start = max(now, preferred)
        return DateInterval(start: start, end: max(start, goal.readyBy))
    }

    /// Used by the quick 'today' editor. It means the next occurrence of the selected local time.
    static func nextOccurrence(minutes: Int, after now: Date,
                               calendar: Calendar = chicagoCalendar) -> Date? {
        guard (0..<1440).contains(minutes) else { return nil }
        var components = DateComponents()
        components.hour = minutes / 60
        components.minute = minutes % 60
        components.second = 0
        return calendar.nextDate(after: now, matching: components, matchingPolicy: .nextTime,
                                 repeatedTimePolicy: .first, direction: .forward)
    }
}
