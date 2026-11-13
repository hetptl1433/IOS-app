import Foundation

/// A display estimate of ComEd's time-varying supply + adjusted distribution charge.
/// Other supply riders, capacity, IEDT, taxes and fixed charges are excluded.
enum ComEdDeliveryClass: String, CaseIterable, Identifiable, Sendable {
    case singleFamilyElectric, singleFamilyNonElectric, multiFamilyElectric, multiFamilyNonElectric
    var id: String { rawValue }
    var title: String {
        switch self {
        case .singleFamilyElectric: return "Single-family · electric heat"
        case .singleFamilyNonElectric: return "Single-family · non-electric heat"
        case .multiFamilyElectric: return "Multi-family · electric heat"
        case .multiFamilyNonElectric: return "Multi-family · non-electric heat"
        }
    }
    static var calendar: Calendar {
        var value = Calendar(identifier: .gregorian); value.timeZone = TimeZone(identifier: "America/Chicago")!; return value
    }
    static func day(_ date: Date) -> String {
        let value = calendar.dateComponents([.year,.month,.day], from: date)
        return String(format: "%04d-%02d-%02d", value.year!,value.month!,value.day!)
    }
    static let sourceURL = URL(string: "https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/blt86ebee5fe6ed02f8/6ad911e1630c34d7f475f5cd/2026_Ratebook.pdf?branch=prod_alias")!
    /// Info sheets 67/67.1, 20, 9, 18, 65, 62 and 56. November–December 2026.
    /// Each adjustment is rounded to $0.00001/kWh, following ComEd's billed-charge guide.
    /// Do not silently extend a dated tariff into an unverified billing period.
    func cents(at date: Date) -> Double? {
        let day = Self.day(date)
        guard day >= "2026-11-01", day <= "2026-12-31" else { return nil }
        let hour = Self.calendar.component(.hour, from: date)
        let band = hour < 6 || hour >= 21 ? 3 : hour < 13 ? 0 : hour < 19 ? 1 : 2
        let base: [Double], balancing: Double
        switch self {
        case .singleFamilyElectric: base = [1.999,5.329,1.890,1.550]; balancing = 0.061625
        case .singleFamilyNonElectric: base = [4.009,10.712,3.747,2.984]; balancing = 0.003745
        case .multiFamilyElectric: base = [1.925,4.975,1.823,1.512]; balancing = 0.052267
        case .multiFamilyNonElectric: base = [3.073,8.689,2.856,2.251]; balancing = 0.007750
        }
        let amount = base[band]
        let adjustment = [0.0217,0.001391,0.029432,balancing].map { (amount * $0 * 1000).rounded() / 1000 }.reduce(0,+)
        return (1000 * (amount + adjustment + 0.062)).rounded() / 1000
    }
    static func period(at date: Date) -> String {
        switch calendar.component(.hour, from: date) {
        case 6..<13: return "Morning"
        case 13..<19: return "Mid-day peak"
        case 19..<21: return "Evening"
        default: return "Overnight"
        }
    }
}

struct ControllerRatePage: Codable, Equatable, Sendable {
    var schemaVersion: Int
    var source: String
    var timezone: String
    var day: String
    var generatedAt: Date
    var currentSupplyCents: Double?
    var observedAt: Date?
    var hours: [Hour]
    struct Hour: Codable, Equatable, Identifiable, Sendable {
        var start: Date
        var supplyCents: Double?
        var status: String
        var id: Date { start }
        var statusTitle: String {
            switch status {
            case "settled": return "Settled"
            case "observed": return "Observed"
            case "provisional": return "Partial hour"
            case "current": return "Live average"
            case "dayAhead": return "Day-ahead estimate"
            default: return "Unavailable"
            }
        }
    }
    func validate(requestedDay: String) throws {
        let allowed: Set<String> = ["settled","observed","provisional","current","dayAhead","unavailable"]
        guard schemaVersion == 1, source == "ComEd", timezone == "America/Chicago", day == requestedDay,
              generatedAt.timeIntervalSince1970.isFinite, currentSupplyCents?.isFinite ?? true,
              observedAt?.timeIntervalSince1970.isFinite ?? true, (23...25).contains(hours.count),
              Set(hours.map(\.start)).count == hours.count,
              hours == hours.sorted(by: { $0.start < $1.start }),
              hours.allSatisfy({ ComEdDeliveryClass.day($0.start) == day && $0.start.timeIntervalSince1970.isFinite &&
                  $0.start.timeIntervalSince1970.truncatingRemainder(dividingBy: 3600) == 0 &&
                  ($0.supplyCents?.isFinite ?? true) && allowed.contains($0.status) &&
                  (($0.status == "unavailable") == ($0.supplyCents == nil)) })
        else { throw ControllerConnectionError.invalidMessage }
        guard let first = hours.first, let last = hours.last,
              ComEdDeliveryClass.calendar.startOfDay(for: first.start) == first.start,
              last.start.timeIntervalSince(first.start) == Double(hours.count-1)*3600,
              ComEdDeliveryClass.calendar.date(byAdding: .day, value: 1, to: first.start)?.timeIntervalSince(first.start) == Double(hours.count)*3600
        else { throw ControllerConnectionError.invalidMessage }
    }
}
