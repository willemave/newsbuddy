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
        tabAppearance.configureWithDefaultBackground()
        tabAppearance.backgroundColor = surface.withAlphaComponent(0.9)
        tabAppearance.stackedLayoutAppearance = itemAppearance
        tabAppearance.inlineLayoutAppearance = itemAppearance
        tabAppearance.compactInlineLayoutAppearance = itemAppearance
        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance

        let navigationAppearance = UINavigationBarAppearance()
        navigationAppearance.configureWithDefaultBackground()
        navigationAppearance.backgroundColor = surface.withAlphaComponent(0.9)
        UINavigationBar.appearance().standardAppearance = navigationAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navigationAppearance
        UINavigationBar.appearance().tintColor = accent
    }
}

@MainActor
enum RootDependencyFactory {
    static func makeTabCoordinator() -> TabCoordinatorViewModel {
        let contentRepository = ContentRepository()
        let readRepository = ReadStatusRepository()
        let unreadService = UnreadCountService.shared

        let shortNewsViewModel = ShortNewsListViewModel(
            repository: contentRepository,
            readRepository: readRepository,
            unreadCountService: unreadService
        )
        let dailyDigestViewModel = DailyDigestListViewModel(
            repository: DailyNewsDigestRepository(),
            unreadCountService: unreadService
        )
        let longContentViewModel = LongContentListViewModel(
            repository: contentRepository,
            readRepository: readRepository,
            unreadCountService: unreadService
        )

        return TabCoordinatorViewModel(
            shortNewsVM: shortNewsViewModel,
            dailyDigestVM: dailyDigestViewModel,
            longContentVM: longContentViewModel
        )
    }
}
