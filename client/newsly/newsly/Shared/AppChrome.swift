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
        let terracotta = UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor(red: 0.831, green: 0.514, blue: 0.416, alpha: 1.0)
                : UIColor(red: 0.439, green: 0.169, blue: 0.098, alpha: 1.0)
        }
        let unselected = UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor(red: 0.639, green: 0.616, blue: 0.588, alpha: 1.0)
                : UIColor(red: 0.396, green: 0.365, blue: 0.337, alpha: 1.0)
        }
        let surface = UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor(red: 0.102, green: 0.094, blue: 0.082, alpha: 1.0)
                : UIColor(red: 0.992, green: 0.976, blue: 0.957, alpha: 1.0)
        }

        let itemAppearance = UITabBarItemAppearance()
        itemAppearance.selected.iconColor = terracotta
        itemAppearance.selected.titleTextAttributes = [.foregroundColor: terracotta]
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
        UINavigationBar.appearance().tintColor = terracotta
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
