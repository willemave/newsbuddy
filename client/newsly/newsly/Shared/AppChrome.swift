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
        let chromeAccent = UIColor.appChromeAccent
        let unselected = UIColor.tertiaryLabel
        let surface = UIColor.appSurfacePrimary

        let itemAppearance = UITabBarItemAppearance()
        itemAppearance.selected.iconColor = chromeAccent
        itemAppearance.selected.titleTextAttributes = [
            .foregroundColor: chromeAccent,
            .font: UIFont.appSans(size: 10, weight: .medium)
        ]
        itemAppearance.normal.iconColor = unselected
        itemAppearance.normal.titleTextAttributes = [
            .foregroundColor: unselected,
            .font: UIFont.appSans(size: 10, weight: .medium)
        ]

        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithTransparentBackground()
        tabAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        tabAppearance.shadowColor = UIColor.separator
        tabAppearance.stackedLayoutAppearance = itemAppearance
        tabAppearance.inlineLayoutAppearance = itemAppearance
        tabAppearance.compactInlineLayoutAppearance = itemAppearance
        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance
        UITabBar.appearance().tintColor = chromeAccent

        let navigationAppearance = UINavigationBarAppearance()
        navigationAppearance.configureWithTransparentBackground()
        navigationAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        navigationAppearance.shadowColor = UIColor.separator
        navigationAppearance.titleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFont.appSans(size: 17, weight: .semibold)
        ]
        navigationAppearance.largeTitleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFont.appSans(size: 34, weight: .semibold)
        ]
        UINavigationBar.appearance().standardAppearance = navigationAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navigationAppearance
        UINavigationBar.appearance().tintColor = chromeAccent
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
