import Foundation

enum TimelineDayLabel {
    static func text(
        for date: Date,
        now: Date = AppClock.now,
        calendar: Calendar = .current
    ) -> String {
        let today = calendar.startOfDay(for: now)
        let day = calendar.startOfDay(for: date)

        if day == today {
            return "TODAY"
        }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: today), day == yesterday {
            return "YESTERDAY"
        }
        return date.formatted(.dateTime.month(.abbreviated).day()).uppercased()
    }
}
