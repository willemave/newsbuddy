//
//  ActivityViewPresenter.swift
//  newsly
//

import UIKit

@MainActor
enum ActivityViewPresenter {
    static func presentWhenReady(
        _ activityViewController: UIActivityViewController,
        attempt: Int = 0
    ) {
        let maxAttempts = 8

        guard let rootViewController = activeRootViewController() else { return }

        let topViewController = topVisibleViewController(from: rootViewController)
            ?? rootViewController

        if rootViewController.presentedViewController?.isBeingDismissed == true
            || topViewController.isBeingPresented
            || topViewController.isBeingDismissed {
            guard attempt < maxAttempts else { return }

            let transitionCoordinator = topViewController.transitionCoordinator
                ?? rootViewController.transitionCoordinator
            if let transitionCoordinator {
                transitionCoordinator.animate(alongsideTransition: nil) { _ in
                    Task { @MainActor in
                        presentWhenReady(activityViewController, attempt: attempt + 1)
                    }
                }
            } else {
                Task { @MainActor in
                    await Task.yield()
                    presentWhenReady(activityViewController, attempt: attempt + 1)
                }
            }
            return
        }

        topViewController.present(activityViewController, animated: true)
    }

    private static func activeRootViewController() -> UIViewController? {
        let activeWindowScenes = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }

        let activeWindow = activeWindowScenes
            .flatMap(\.windows)
            .first(where: { $0.isKeyWindow })
            ?? activeWindowScenes
                .flatMap(\.windows)
                .first(where: { !$0.isHidden })

        return activeWindow?.rootViewController
    }

    private static func topVisibleViewController(from root: UIViewController?) -> UIViewController? {
        guard let root else { return nil }

        if let navigationController = root as? UINavigationController {
            return topVisibleViewController(from: navigationController.visibleViewController)
        }

        if let tabBarController = root as? UITabBarController {
            return topVisibleViewController(from: tabBarController.selectedViewController)
        }

        if let presentedViewController = root.presentedViewController,
           !presentedViewController.isBeingDismissed {
            return topVisibleViewController(from: presentedViewController)
        }

        return root
    }
}
