//
//  NavigationRestorationModel.swift
//  newsly
//

import os.log
import SwiftUI

private let navigationRestoreLogger = Logger(subsystem: "com.newsly", category: "ContentView")

enum NavigationRestorationModel {
    @MainActor
    static func restoreIfNeeded(
        isBriefingExperience: Bool,
        isRestoringPath: Binding<Bool>,
        readingStateStore: ReadingStateStore,
        tabCoordinator: TabCoordinatorViewModel,
        shortFormPath: Binding<NavigationPath>,
        longFormPath: Binding<NavigationPath>
    ) {
        guard !isBriefingExperience else { return }
        let isNews = readingStateStore.current?.contentType == .news
        let targetPathIsEmpty = isNews ? shortFormPath.wrappedValue.isEmpty : longFormPath.wrappedValue.isEmpty
        guard !isRestoringPath.wrappedValue, targetPathIsEmpty, let state = readingStateStore.current else { return }

        isRestoringPath.wrappedValue = true
        navigationRestoreLogger.info(
            "[NavigationRestore] contentId=\(state.contentId, privacy: .public) contentType=\(state.contentType.rawValue, privacy: .public)"
        )
        let targetTab: RootTab = isNews ? .shortNews : .longContent
        if tabCoordinator.selectedTab != targetTab {
            tabCoordinator.selectedTab = targetTab
        }

        Task { @MainActor in
            await Task.yield()
            defer { isRestoringPath.wrappedValue = false }

            let currentIds: [Int]
            if isNews {
                guard shortFormPath.wrappedValue.isEmpty else { return }
                let ids = tabCoordinator.shortNewsVM.currentItems().map(\.id)
                currentIds = ids.isEmpty ? [state.contentId] : ids
            } else {
                guard longFormPath.wrappedValue.isEmpty else { return }
                let ids = tabCoordinator.longContentVM.currentItems().map(\.id)
                currentIds = ids.isEmpty ? [state.contentId] : ids
            }

            let route = ContentDetailRoute(
                contentId: state.contentId,
                contentType: state.contentType,
                allContentIds: currentIds,
                navigationSurface: isNews ? .fastNews : .longForm
            )

            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                if isNews {
                    shortFormPath.wrappedValue.append(route)
                } else {
                    longFormPath.wrappedValue.append(route)
                }
            }
            navigationRestoreLogger.info("[NavigationRestore] pathRestored idsCount=\(currentIds.count, privacy: .public)")
        }
    }
}
