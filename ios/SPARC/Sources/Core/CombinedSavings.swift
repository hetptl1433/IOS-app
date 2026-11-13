import Foundation

struct CombinedSavingsReport: Codable, Equatable, Sendable {
    var schemaVersion: Int
    var kind: String
    var benchmark: String
    var costScope: String
    var generatedAt: Date
    var start: String
    var end: String
    var deliveryClass: String
    var fixedSupplyCents: Double
    var source: String
    var summary: Totals
    var nights: [Night]
    var limitations: [String]
    struct Totals: Codable, Equatable, Sendable {
        var energyKWh: Double?
        var baselineCostUSD: Double?
        var systemCostUSD: Double?
        var savingsUSD: Double?
        var savingsPercent: Double?
        var hourlySavingsUSD: Double?
        var deliverySavingsUSD: Double?
        var schedulingSavingsUSD: Double?
        var nightCount: Int
        var pricedNightCount: Int
        var unpricedNightCount: Int
    }
    struct Night: Codable, Equatable, Identifiable, Sendable {
        var id: String
        var date: String
        var energyKWh: Double
        var baselineCostUSD: Double?
        var systemCostUSD: Double?
        var savingsUSD: Double?
        var savingsPercent: Double?
        var hourlySavingsUSD: Double?
        var deliverySavingsUSD: Double?
        var schedulingSavingsUSD: Double?
        var priced: Bool
        var simulated: Bool
        var ready: Bool?
        var note: String?
        var hourlyStandardCostUSD: Double?
        var hourlyTODCostUSD: Double?
        /// Hourly pricing, charging right away from 6 PM; added on the Pi from ComEd's billing prices.
        var at6PM: Immediate?
    }
    struct Immediate: Codable, Equatable, Sendable {
        var hourlyStandardCostUSD: Double
        var hourlyTODCostUSD: Double
    }
    func validate(kind expected: String) throws {
        let t=summary
        guard schemaVersion==1,kind==expected,["simulation","recorded"].contains(kind),benchmark=="fixed_supply_standard_delivery",
              costScope=="electricity_supply_distribution_estimate",source=="ComEd",deliveryClass=="singleFamilyElectric",
              fixedSupplyCents.isFinite,generatedAt.timeIntervalSince1970.isFinite,nights.count<=15,
              Set(nights.map(\.id)).count==nights.count,t.nightCount==nights.count,
              t.pricedNightCount==nights.filter(\.priced).count,t.unpricedNightCount==t.nightCount-t.pricedNightCount
        else { throw ControllerConnectionError.invalidMessage }
        func check(_ b:Double?,_ c:Double?,_ gain:Double?,_ a:Double?,_ d:Double?,_ s:Double?) -> Bool {
            let values=[b,c,gain,a,d,s].compactMap{$0}
            guard values.allSatisfy(\.isFinite) else{return false}
            if let b,let c,let gain,let a,let d,let s {return abs((b-c)-gain)<0.0001 && abs(a+d+s-gain)<0.0001}
            return gain==nil
        }
        guard check(t.baselineCostUSD,t.systemCostUSD,t.savingsUSD,t.hourlySavingsUSD,t.deliverySavingsUSD,t.schedulingSavingsUSD),
              t.savingsPercent?.isFinite ?? true,
              nights.allSatisfy({$0.energyKWh.isFinite && $0.energyKWh>=0 && ($0.savingsPercent?.isFinite ?? true) &&
                  check($0.baselineCostUSD,$0.systemCostUSD,$0.savingsUSD,$0.hourlySavingsUSD,$0.deliverySavingsUSD,$0.schedulingSavingsUSD) &&
                  (!$0.priced || $0.savingsUSD != nil) &&
                  [$0.hourlyStandardCostUSD, $0.hourlyTODCostUSD, $0.at6PM?.hourlyStandardCostUSD, $0.at6PM?.hourlyTODCostUSD]
                      .allSatisfy { $0?.isFinite ?? true }})
        else{throw ControllerConnectionError.invalidMessage}
        let priced = nights.filter(\.priced)
        guard nights.allSatisfy({ kind != "simulation" || $0.simulated }),
              t.energyKWh?.isFinite ?? true,
              priced.isEmpty ? t.energyKWh == nil : abs((t.energyKWh ?? -1) - priced.reduce(0) { $0 + $1.energyKWh }) < 0.0001
        else { throw ControllerConnectionError.invalidMessage }
        let comparisons: [(Double?, (Night) -> Double?)] = [
            (t.baselineCostUSD, { $0.baselineCostUSD }), (t.systemCostUSD, { $0.systemCostUSD }),
            (t.savingsUSD, { $0.savingsUSD }), (t.hourlySavingsUSD, { $0.hourlySavingsUSD }),
            (t.deliverySavingsUSD, { $0.deliverySavingsUSD }), (t.schedulingSavingsUSD, { $0.schedulingSavingsUSD })]
        for (total, field) in comparisons {
            if priced.isEmpty { guard total == nil else { throw ControllerConnectionError.invalidMessage } }
            else { guard let total, abs(total - priced.compactMap(field).reduce(0,+)) < 0.0001 else { throw ControllerConnectionError.invalidMessage } }
        }
        if let base=t.baselineCostUSD,let gain=t.savingsUSD,abs(base)>1e-9 {
            guard let percent=t.savingsPercent,abs(percent-100*gain/abs(base))<0.0001 else{throw ControllerConnectionError.invalidMessage}
        }
    }
}

/// What "saved" is measured against. Chosen in Settings → Savings comparison.
enum SavingsComparison: String, CaseIterable, Identifiable, Sendable {
    case fixedPlan, hourlyAt6PM, hourlyAtPlugIn
    static let storageKey = "sparc.savingsComparison.v1"
    var id: String { rawValue }
    var isHourly: Bool { self != .fixedPlan }
    /// The plan without SPARC.
    var baselineTitle: String {
        switch self {
        case .fixedPlan: "Fixed-price plan"
        case .hourlyAt6PM: "Hourly, charging at 6 PM"
        case .hourlyAtPlugIn: "Hourly, charging at plug-in"
        }
    }
    var settingsSubtitle: String {
        switch self {
        case .fixedPlan: "Fixed-price plan"
        case .hourlyAt6PM: "Hourly pricing · 6 PM"
        case .hourlyAtPlugIn: "Hourly pricing · at plug-in"
        }
    }
    /// The gains that add up to the saving, in order.
    var partTitles: [String] {
        isHourly ? ["Time-of-Day delivery", "Smart timing"] : ["Hourly pricing", "Time-of-Day delivery", "Smart timing"]
    }
}

extension CombinedSavingsReport.Night {
    /// Cost without SPARC, cost with SPARC and the gains between them; nil when this night isn't priced.
    func costs(_ comparison: SavingsComparison) -> (baseline: Double, system: Double, parts: [Double])? {
        guard priced, let system = systemCostUSD else { return nil }
        switch comparison {
        case .fixedPlan:
            guard let base = baselineCostUSD, let hourly = hourlySavingsUSD, let delivery = deliverySavingsUSD,
                  let timing = schedulingSavingsUSD else { return nil }
            return (base, system, [hourly, delivery, timing])
        case .hourlyAtPlugIn:
            guard let base = hourlyStandardCostUSD, let tod = hourlyTODCostUSD else { return nil }
            return (base, system, [base - tod, tod - system])
        case .hourlyAt6PM:
            guard let six = at6PM else { return nil }
            return (six.hourlyStandardCostUSD, system,
                    [six.hourlyStandardCostUSD - six.hourlyTODCostUSD, six.hourlyTODCostUSD - system])
        }
    }
    func saved(_ comparison: SavingsComparison) -> Double? { costs(comparison).map { $0.baseline - $0.system } }
    func savedPercent(_ comparison: SavingsComparison) -> Double? {
        guard let cost = costs(comparison), abs(cost.baseline) > 1e-9 else { return nil }
        return 100 * (cost.baseline - cost.system) / abs(cost.baseline)
    }
}

extension CombinedSavingsReport {
    struct Comparison: Equatable, Sendable {
        var baselineUSD: Double
        var systemUSD: Double
        var parts: [Double]
        var energyKWh: Double
        var nightCount: Int
        var unpricedNightCount: Int
        var savedUSD: Double { baselineUSD - systemUSD }
        var savedPercent: Double? { abs(baselineUSD) > 1e-9 ? 100 * savedUSD / abs(baselineUSD) : nil }
    }
    /// Totals over the nights priced for this comparison; nil when none are.
    func totals(_ comparison: SavingsComparison) -> Comparison? {
        let rows = nights.compactMap { night in night.costs(comparison).map { (night.energyKWh, $0) } }
        guard let first = rows.first else { return nil }
        var parts = Array(repeating: 0.0, count: first.1.parts.count)
        for (_, cost) in rows { for index in parts.indices { parts[index] += cost.parts[index] } }
        return Comparison(baselineUSD: rows.reduce(0) { $0 + $1.1.baseline }, systemUSD: rows.reduce(0) { $0 + $1.1.system },
                          parts: parts, energyKWh: rows.reduce(0) { $0 + $1.0 },
                          nightCount: rows.count, unpricedNightCount: nights.count - rows.count)
    }
}
