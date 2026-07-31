//
//  TabCoordinatorViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import Observation

enum RootTab: String, Hashable, CaseIterable {
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
    var selectedTab: RootTab {
        didSet {
            guard let storageKey else { return }
            defaults.set(selectedTab.rawValue, forKey: storageKey)
        }
    }

    @ObservationIgnored
    let briefingVM: BriefingViewModel

    @ObservationIgnored
    private let defaults: UserDefaults

    @ObservationIgnored
    private let storageKey: String?

    init(
        briefingVM: BriefingViewModel,
        userID: Int? = nil,
        defaults: UserDefaults = SharedContainer.userDefaults,
        initialTab: RootTab? = nil
    ) {
        self.briefingVM = briefingVM
        self.defaults = defaults
        self.storageKey = userID.map { "root.selectedTab.user.\($0)" }

        let restoredTab = self.storageKey
            .flatMap { defaults.string(forKey: $0) }
            .flatMap(RootTab.init(rawValue:))
        self.selectedTab = initialTab ?? restoredTab ?? .briefing
    }
}
