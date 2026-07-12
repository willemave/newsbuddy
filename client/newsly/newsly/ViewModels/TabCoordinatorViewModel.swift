//
//  TabCoordinatorViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import Observation
import OSLog

private let rootTabFlowLogger = Logger(
    subsystem: "org.willemaw.newsly",
    category: "RootTabFlow"
)

enum RootTab: Hashable {
    case longContent
    case shortNews
    case briefing
    case knowledge
    case learning
    case more

    var logName: String {
        switch self {
        case .longContent:
            return "long_form"
        case .shortNews:
            return "fast_news"
        case .briefing:
            return "briefing"
        case .knowledge:
            return "knowledge"
        case .learning:
            return "learning"
        case .more:
            return "more"
        }
    }
}

@MainActor
@Observable
final class TabCoordinatorViewModel {
    var selectedTab: RootTab

    @ObservationIgnored
    let shortNewsVM: ShortNewsListViewModel
    @ObservationIgnored
    let longContentVM: LongContentListViewModel
    @ObservationIgnored
    let briefingVM: BriefingViewModel

    @ObservationIgnored
    private var previousTab: RootTab

    init(
        shortNewsVM: ShortNewsListViewModel,
        longContentVM: LongContentListViewModel,
        briefingVM: BriefingViewModel,
        initialTab: RootTab = .briefing
    ) {
        self.shortNewsVM = shortNewsVM
        self.longContentVM = longContentVM
        self.briefingVM = briefingVM
        self.selectedTab = initialTab
        self.previousTab = initialTab
    }

    func handleTabChange(to newTab: RootTab) {
        guard newTab != previousTab else { return }
        rootTabFlowLogger.info(
            "tab selection changed | from=\(self.previousTab.logName, privacy: .public) to=\(newTab.logName, privacy: .public)"
        )
        // Keep the outgoing tab stable during the system tab selection transition.
        // Clearing/reloading it here causes visible flashes when switching between
        // the long-form and fast-news roots.
        previousTab = newTab
        ensureTabLoaded(newTab)
    }

    func ensureInitialLoads() {
        rootTabFlowLogger.info(
            "root tab flow started | initialTab=\(self.selectedTab.logName, privacy: .public)"
        )
        ensureTabLoaded(selectedTab)
    }

    private func ensureTabLoaded(_ tab: RootTab) {
        switch tab {
        case .shortNews:
            if shortNewsVM.currentItems().isEmpty {
                rootTabFlowLogger.info("tab content load requested | tab=fast_news")
                Task { await shortNewsVM.refresh() }
            } else {
                rootTabFlowLogger.info("tab content refresh requested | tab=fast_news")
                Task { await shortNewsVM.refreshInBackgroundAndWait() }
            }
        case .longContent:
            if longContentVM.currentItems().isEmpty {
                rootTabFlowLogger.info("tab content load requested | tab=long_form")
                Task { await longContentVM.refresh() }
            } else {
                // Intentional asymmetry vs Fast News: long-form is not time-sensitive,
                // so re-entering the tab keeps the loaded list (and the reader's
                // scroll position) rather than background-refreshing. Enforced by
                // TabCoordinatorViewModelTests.testHandleTabChangeKeepsIncomingLongFormStableWhenAlreadyLoaded.
                rootTabFlowLogger.info("tab content already available | tab=long_form")
            }
        case .briefing:
            // ContentView owns the single Briefing active/inactive signal so
            // tab selection and scene phase cannot start competing loads.
            rootTabFlowLogger.info("tab became active | tab=briefing")
        case .knowledge, .learning, .more:
            rootTabFlowLogger.info(
                "tab became active with no preload required | tab=\(tab.logName, privacy: .public)"
            )
            break
        }
    }
}
