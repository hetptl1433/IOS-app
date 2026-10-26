import XCTest
@testable import SPARC

final class ScheduleEngineTests: XCTestCase {
    private func date(_ string: String) -> Date {
        ISO8601DateFormatter().date(from: string)!
    }

    func testFridayAfterDepartureSkipsWeekendAndUsesChicagoFallTime() throws {
        let departure = try XCTUnwrap(ScheduleEngine.nextDeparture(
            for: .default, after: date("2026-10-30T13:00:00Z")))
        XCTAssertEqual(departure, date("2026-11-02T13:00:00Z"))
    }

    func testExactDepartureMovesToNextSelectedDay() throws {
        let departure = try XCTUnwrap(ScheduleEngine.nextDeparture(
            for: .default, after: date("2026-10-26T12:00:00Z")))
        XCTAssertEqual(departure, date("2026-10-27T12:00:00Z"))
    }

    func testSpringGapUsesNextValidTime() throws {
        var schedule = WeeklySchedule.default
        schedule.days = [7]
        schedule.departureMinutes = 150 // 02:30 does not exist on this Sunday.
        let result = try XCTUnwrap(ScheduleEngine.nextDeparture(
            for: schedule, after: date("2026-03-08T06:00:00Z")))
        XCTAssertEqual(result, date("2026-03-08T08:00:00Z")) // 03:00 CDT.
    }

    func testFallRepeatUsesFirstOccurrenceAndDoesNotRepeatCharge() throws {
        var schedule = WeeklySchedule.default
        schedule.days = [7]
        schedule.departureMinutes = 90
        XCTAssertEqual(ScheduleEngine.nextDeparture(for: schedule,
            after: date("2026-11-01T05:00:00Z")), date("2026-11-01T06:30:00Z"))
        XCTAssertEqual(ScheduleEngine.nextDeparture(for: schedule,
            after: date("2026-11-01T06:45:00Z")), date("2026-11-08T07:30:00Z"))
    }

    func testWeeklyRoutineCrossesYearBoundary() {
        XCTAssertEqual(ScheduleEngine.nextDeparture(for: .default,
            after: date("2026-12-31T14:00:00Z")), date("2027-01-01T13:00:00Z"))
    }

    func testOneTimeGoalExpiresExactlyAtDepartureWithoutChangingWeeklyRoutine() throws {
        let weekly = WeeklySchedule.default
        let departure = date("2026-10-27T10:30:00Z")
        let today = TodayOverride(targetPercent: 95, departure: departure)
        let active = try XCTUnwrap(ScheduleEngine.effectiveGoal(
            weekly: weekly, today: today, now: departure.addingTimeInterval(-1)))
        XCTAssertEqual(active.targetPercent, 95)
        XCTAssertEqual(active.departure, departure)
        XCTAssertEqual(active.readyBy, departure.addingTimeInterval(-1800))
        XCTAssertTrue(active.isOverride)
        let expired = try XCTUnwrap(ScheduleEngine.effectiveGoal(
            weekly: weekly, today: today, now: departure))
        XCTAssertFalse(expired.isOverride)
        XCTAssertEqual(expired.targetPercent, 80)
        XCTAssertEqual(weekly, .default)
    }

    func testDisabledRoutineCanStillHaveOneTimeGoal() throws {
        var weekly = WeeklySchedule.default
        weekly.enabled = false
        weekly.days = []
        let now = date("2026-10-26T23:00:00Z")
        XCTAssertNil(ScheduleEngine.effectiveGoal(weekly: weekly, today: nil, now: now))
        XCTAssertNotNil(ScheduleEngine.effectiveGoal(weekly: weekly,
            today: TodayOverride(targetPercent: 70, departure: now.addingTimeInterval(3600)), now: now))
    }

    func testInvalidSavedScheduleIsRejectedInsteadOfProducingDeparture() throws {
        let malformed = Data("""
        {"targetPercent":101,"departureMinutes":1440,"days":[0,8],"enabled":true,"mode":"smart","fixedStartMinutes":60}
        """.utf8)
        let decoded = try JSONDecoder().decode(WeeklySchedule.self, from: malformed)
        XCTAssertThrowsError(try decoded.validate())
        XCTAssertNil(ScheduleEngine.nextDeparture(for: decoded, after: date("2026-10-26T00:00:00Z")))
    }

    func testWeeklyValidationRejectsMissingDaysAndInvalidTimes() {
        var schedule = WeeklySchedule.default
        schedule.days = []
        XCTAssertThrowsError(try schedule.validate())
        schedule = .default
        schedule.departureMinutes = -1
        XCTAssertThrowsError(try schedule.validate())
        schedule = .default
        schedule.fixedStartMinutes = 1440
        XCTAssertThrowsError(try schedule.validate())
        schedule = .default
        schedule.targetPercent = 49
        XCTAssertThrowsError(try schedule.validate())
    }

    func testInvalidOneTimeGoalFallsBackToRoutine() throws {
        let now = date("2026-10-26T00:00:00Z")
        let result = try XCTUnwrap(ScheduleEngine.effectiveGoal(weekly: .default,
            today: TodayOverride(targetPercent: 101, departure: now.addingTimeInterval(3600)), now: now))
        XCTAssertFalse(result.isOverride)
        XCTAssertEqual(result.targetPercent, 80)
    }

    func testNextOccurrenceRejectsInvalidTimesAndRollsPastTimeToTomorrow() {
        let now = date("2026-10-26T13:00:00Z") // 08:00 Chicago.
        XCTAssertNil(ScheduleEngine.nextOccurrence(minutes: -1, after: now))
        XCTAssertNil(ScheduleEngine.nextOccurrence(minutes: 1440, after: now))
        XCTAssertEqual(ScheduleEngine.nextOccurrence(minutes: 420, after: now),
                       date("2026-10-27T12:00:00Z"))
    }

    func testReadyBufferCrossesMidnight() throws {
        var schedule = WeeklySchedule.default
        schedule.departureMinutes = 15
        let goal = try XCTUnwrap(ScheduleEngine.effectiveGoal(weekly: schedule,
            today: nil, now: date("2026-10-26T03:00:00Z")))
        XCTAssertEqual(goal.departure, date("2026-10-26T05:15:00Z"))
        XCTAssertEqual(goal.readyBy, date("2026-10-26T04:45:00Z"))
    }

    func testFixedDemoWindowUsesPreviousCalendarDayAcrossFallTransition() throws {
        var schedule = WeeklySchedule.default
        schedule.days = [7]
        schedule.mode = .fixed
        schedule.fixedStartMinutes = 23 * 60
        let now = date("2026-11-01T00:00:00Z")
        let goal = try XCTUnwrap(ScheduleEngine.effectiveGoal(weekly: schedule, today: nil, now: now))
        let window = ScheduleEngine.demoWindow(for: goal, weekly: schedule,
                                               remainingMinutes: 129, now: now)
        XCTAssertEqual(window.start, date("2026-11-01T04:00:00Z")) // 23:00 CDT, not 23:00 CST.
        XCTAssertEqual(window.end, date("2026-11-01T06:09:00Z"))
        XCTAssertLessThanOrEqual(window.end, goal.readyBy)
    }

    func testTooSoonDemoGoalKeepsDurationInsteadOfInventingInstantCharge() throws {
        let now = date("2026-10-26T23:00:00Z")
        let goal = try XCTUnwrap(ScheduleEngine.effectiveGoal(weekly: .default,
            today: TodayOverride(targetPercent: 90, departure: now.addingTimeInterval(600)), now: now))
        let window = ScheduleEngine.demoWindow(for: goal, weekly: .default,
                                               remainingMinutes: 129, now: now)
        XCTAssertEqual(window.start, now)
        XCTAssertEqual(window.duration, 129 * 60)
        XCTAssertGreaterThan(window.end, goal.readyBy)
    }
}
