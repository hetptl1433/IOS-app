#if DEBUG
import Foundation

/// Deterministic interface-test data. Never used by a normal launch or a release build.
actor HistoryInterfaceFixture: ControllerClient {
    static let now = ISO8601DateFormatter().date(from: "2026-11-06T15:00:00Z")!
    private var campaign: CampaignSchedule
    init() {
        func date(_ value: String) -> Date { ISO8601DateFormatter().date(from: value)! }
        campaign = CampaignSchedule(campaignId: "interface-test", startDate: "2026-11-05", nights: 7,
                                    startMinutes: 1320, departureMinutes: 420, initialBatteryPercent: 20,
                                    targetBatteryPercent: 80, screenSleepMinutes: 5, strategy: "day_ahead_live",
                                    timezone: "America/Chicago", enabled: true, revision: 1,
                                    firstStart: date("2026-11-06T04:00:00Z"), finalDeparture: date("2026-11-12T13:00:00Z"),
                                    currentSessionId: "interface-test:2026-11-05", phase: "betweenNights",
                                    nextStart: date("2026-11-07T04:00:00Z"),
                                    current: CampaignCurrentSession(start: date("2026-11-06T04:00:00Z"),
                                                                    departure: date("2026-11-06T13:00:00Z"),
                                                                    initialBatteryPercent: 20, targetBatteryPercent: 80,
                                                                    finalizedAt: date("2026-11-06T13:06:00Z")))
    }
    func fetchSettings() async throws -> ControllerSettings {
        var weekly = WeeklySchedule.default; weekly.enabled = false
        return ControllerSettings(weekly: weekly, today: nil, virtualCarEnabled: true, campaign: campaign)
    }
    func fetchSnapshot() async throws -> ChargeSnapshot {
        var value = ChargeSnapshot.demo(now: Self.now)
        value.state = .complete; value.batteryPercent = 80; value.powerKW = 0
        value.energyKWh = 60 / 0.9; value.estimatedCost = 1.42; value.currentRateCents = -0.2
        value.realPricing = true; value.rateAvailable = true; value.costAvailable = true
        return value
    }
    func fetchHistory(cursor: Int?) async throws -> ControllerHistoryPage {
        let first = HistorySession(id: UUID(uuidString: "00000000-0000-4000-8000-000000000001")!,
                                   date: campaign.firstStart, start: campaign.firstStart,
                                   departure: campaign.firstStart.addingTimeInterval(9 * 3600),
                                   nightDate: "2026-11-05", status: "complete", initialBatteryPercent: 20,
                                   targetBatteryPercent: 80, batteryPercent: 80, energyKWh: 60 / 0.9,
                                   costUSD: 1.42, baselineCostUSD: 1.12, savingsUSD: -0.30,
                                   savingsPercent: -26.7857, costFinal: true, simulated: true, source: "ComEd",
                                   targetReachedByDeadline: true)
        var missing = first
        missing.id = UUID(uuidString: "00000000-0000-4000-8000-000000000002")!
        missing.date = first.date.addingTimeInterval(-86400); missing.start = missing.date
        missing.departure = first.departure?.addingTimeInterval(-86400); missing.nightDate = "2026-11-04"
        missing.status = "incomplete"; missing.costUSD = nil; missing.baselineCostUSD = nil
        missing.savingsUSD = nil; missing.savingsPercent = nil; missing.costFinal = false
        missing.targetReachedByDeadline = false; missing.provisional = true
        let days = (2...8).map { value in
            HistoryDay(date: "2026-11-0\(value)", energyKWh: value == 5 ? 60 / 0.9 : 0,
                       costUSD: value == 5 ? 1.42 : 0, baselineCostUSD: value == 5 ? 1.12 : 0,
                       savingsUSD: value == 5 ? -0.30 : 0, sessionCount: value == 5 ? 1 : 0)
        }
        return ControllerHistoryPage(schemaVersion: 1, generatedAt: Self.now, timezone: "America/Chicago",
                                     costScope: "electricity_supply", benchmark: "immediate_same_energy",
                                     week: HistoryWeek(start: "2026-11-02", end: "2026-11-09", energyKWh: 60 / 0.9,
                                                       costUSD: 1.42, baselineCostUSD: 1.12, savingsUSD: -0.30,
                                                       savingsPercent: -26.7857, pricedSessions: 1, unpricedSessions: 0,
                                                       provisional: false, days: days), sessions: [first, missing])
    }
    func saveCampaign(_ value: CampaignUpdate) async throws {
        try value.validate()
        campaign.revision += 1; campaign.targetBatteryPercent = value.targetBatteryPercent
        campaign.enabled = value.enabled; campaign.departureMinutes = value.departureMinutes
    }
    func saveWeekly(_ value: WeeklySchedule) async throws {}
    func saveToday(_ value: TodayOverride?) async throws {}
    func command(_ command: String) async throws {}
}
#endif
