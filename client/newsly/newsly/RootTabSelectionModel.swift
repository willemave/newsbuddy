//
//  RootTabSelectionModel.swift
//  newsly
//

import SwiftUI

@MainActor
struct RootTabSelectionModel {
    let tabCoordinator: TabCoordinatorViewModel
    let isBriefingExperience: Bool
    let longFormPathIsEmpty: Bool
    let shortFormPathIsEmpty: Bool
    let onLongFormRetap: () -> Void
    let onShortFormRetap: () -> Void

    var binding: Binding<RootTab> {
        Binding(
            get: { @MainActor in tabCoordinator.selectedTab },
            set: { @MainActor requestedTab in select(requestedTab) }
        )
    }

    func select(_ requestedTab: RootTab) {
        let availableTab = requestedTab.available(isBriefingExperience: isBriefingExperience)
        guard tabCoordinator.selectedTab != availableTab else {
            requestScrollToTop(for: availableTab)
            return
        }
        tabCoordinator.selectedTab = availableTab
    }

    func reconcile() {
        let availableTab = tabCoordinator.selectedTab.available(isBriefingExperience: isBriefingExperience)
        if availableTab != tabCoordinator.selectedTab {
            tabCoordinator.selectedTab = availableTab
        }
    }

    private func requestScrollToTop(for tab: RootTab) {
        switch tab {
        case .longContent where longFormPathIsEmpty:
            onLongFormRetap()
        case .shortNews where shortFormPathIsEmpty:
            onShortFormRetap()
        default:
            break
        }
    }
}
