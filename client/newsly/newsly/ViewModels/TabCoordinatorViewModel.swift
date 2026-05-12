//
//  TabCoordinatorViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import OSLog

private let rootTabFlowLogger = Logger(
    subsystem: "org.willemaw.newsly",
    category: "RootTabFlow"
)

enum RootTab: Hashable {
    case longContent
    case shortNews
    case knowledge
    case more

    var logName: String {
        switch self {
        case .longContent:
            return "long_form"
        case .shortNews:
            return "fast_news"
        case .knowledge:
            return "knowledge"
        case .more:
            return "more"
        }
    }
}

@MainActor
final class TabCoordinatorViewModel: ObservableObject {
    @Published var selectedTab: RootTab

    let shortNewsVM: ShortNewsListViewModel
    let longContentVM: LongContentListViewModel

    private var previousTab: RootTab

    init(
        shortNewsVM: ShortNewsListViewModel,
        longContentVM: LongContentListViewModel,
        initialTab: RootTab = .shortNews
    ) {
        self.shortNewsVM = shortNewsVM
        self.longContentVM = longContentVM
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
                shortNewsVM.refreshTrigger.send(())
            } else {
                rootTabFlowLogger.info("tab content refresh requested | tab=fast_news")
                shortNewsVM.refreshInBackground()
            }
        case .longContent:
            if longContentVM.currentItems().isEmpty {
                rootTabFlowLogger.info("tab content load requested | tab=long_form")
                longContentVM.refreshTrigger.send(())
            } else {
                rootTabFlowLogger.info("tab content already available | tab=long_form")
            }
        case .knowledge, .more:
            rootTabFlowLogger.info(
                "tab became active with no preload required | tab=\(tab.logName, privacy: .public)"
            )
            break
        }
    }
}
