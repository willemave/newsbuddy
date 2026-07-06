//
//  RootTab+Availability.swift
//  newsly
//

extension RootTab {
    func available(isBriefingExperience: Bool) -> RootTab {
        guard isBriefingExperience else {
            return self == .briefing ? .shortNews : self
        }

        switch self {
        case .longContent, .shortNews:
            return .briefing
        case .more:
            return .knowledge
        case .briefing, .knowledge:
            return self
        }
    }
}
