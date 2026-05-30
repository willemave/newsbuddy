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
        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithDefaultBackground()
        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance

        let navigationAppearance = UINavigationBarAppearance()
        navigationAppearance.configureWithDefaultBackground()
        UINavigationBar.appearance().standardAppearance = navigationAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navigationAppearance
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
