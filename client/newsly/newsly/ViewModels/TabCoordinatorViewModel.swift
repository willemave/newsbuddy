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

    private(set) var scrollToTopRequests: [RootTab: Int] = [:]

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
            .flatMap { $0 == "learning" ? RootTab.knowledge : RootTab(rawValue: $0) }
        self.selectedTab = initialTab ?? restoredTab ?? .briefing
    }

    func select(_ tab: RootTab) {
        guard selectedTab == tab else {
            selectedTab = tab
            return
        }
        scrollToTopRequests[tab, default: 0] += 1
    }

    func scrollToTopRequest(for tab: RootTab) -> Int {
        scrollToTopRequests[tab, default: 0]
    }
}
