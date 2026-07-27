//
//  TabCoordinatorViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Observation

enum RootTab: Hashable, CaseIterable {
    case briefing
    case knowledge
    case learning

    var logName: String {
        switch self {
        case .briefing:
            return "briefing"
        case .knowledge:
            return "knowledge"
        case .learning:
            return "learning"
        }
    }
}

@MainActor
@Observable
final class TabCoordinatorViewModel {
    var selectedTab: RootTab

    @ObservationIgnored
    let briefingVM: BriefingViewModel

    init(
        briefingVM: BriefingViewModel,
        initialTab: RootTab = .briefing
    ) {
        self.briefingVM = briefingVM
        self.selectedTab = initialTab
    }
}
