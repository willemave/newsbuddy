//
//  AppChrome.swift
//  newsly
//
//  Created by Assistant on 3/20/26.
//

import SwiftUI
import UIKit

enum AppChrome {
    static func configure() {
        let accent = UIColor.appAccent
        let unselected = UIColor.appOnSurfaceSecondary
        let surface = UIColor.appSurfacePrimary

        let itemAppearance = UITabBarItemAppearance()
        itemAppearance.selected.iconColor = accent
        itemAppearance.selected.titleTextAttributes = [.foregroundColor: accent]
        itemAppearance.normal.iconColor = unselected
        itemAppearance.normal.titleTextAttributes = [.foregroundColor: unselected]

        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithTransparentBackground()
        tabAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        tabAppearance.shadowColor = UIColor.appAccent.withAlphaComponent(0.18)
        tabAppearance.stackedLayoutAppearance = itemAppearance
        tabAppearance.inlineLayoutAppearance = itemAppearance
        tabAppearance.compactInlineLayoutAppearance = itemAppearance
        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance

        let navigationAppearance = UINavigationBarAppearance()
        navigationAppearance.configureWithTransparentBackground()
        navigationAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        navigationAppearance.shadowColor = UIColor.appAccent.withAlphaComponent(0.12)
        UINavigationBar.appearance().standardAppearance = navigationAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navigationAppearance
        UINavigationBar.appearance().tintColor = accent
    }
}

@MainActor
enum RootDependencyFactory {
    static func makeTabCoordinator() -> TabCoordinatorViewModel {
        let shortFeedRepository = ContentRepository(includeAvailableDates: false)
        let longFeedRepository = ContentRepository(includeAvailableDates: false)
        let readRepository = ReadStatusRepository()
        let newsReadRepository = ReadStatusRepository(endpoint: .newsItems)
        let unreadService = UnreadCountService.shared

        let shortNewsViewModel = ShortNewsListViewModel(
            repository: shortFeedRepository,
            readRepository: newsReadRepository,
            unreadCountService: unreadService
        )
        let longContentViewModel = LongContentListViewModel(
            repository: longFeedRepository,
            readRepository: readRepository,
            unreadCountService: unreadService
        )

        return TabCoordinatorViewModel(
            shortNewsVM: shortNewsViewModel,
            longContentVM: longContentViewModel
        )
    }
}
