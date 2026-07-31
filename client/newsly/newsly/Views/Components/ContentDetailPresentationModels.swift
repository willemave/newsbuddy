//
//  ContentDetailPresentationModels.swift
//  newsly
//

import SwiftUI

enum DetailSheetDestination: String, Identifiable {
    case share
    case download
    case tweet
    case chat
    case learningDeckCreate

    var id: String { rawValue }
}

struct BrowserDestination: Identifiable {
    let url: URL

    var id: String { url.absoluteString }
}

struct ViewAlert: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

enum DetailDesign {
    static let horizontalPadding: CGFloat = Spacing.appHorizontalMargin
    static let sectionSpacing: CGFloat = 20
    static let floatingBackButtonSize: CGFloat = 44
    static let textOnlyBackButtonTopPadding: CGFloat = 8
    static let floatingBackFadeStartOffset: CGFloat = 64
    static let floatingBackFadeEndOffset: CGFloat = 220
    static let floatingBackMinimumOpacity: CGFloat = 0.62
    static let floatingBackMinimumScale: CGFloat = 0.9
    // Bracket the point where a 260pt hero clears the status bar, so the fade
    // arrives exactly as body text would start colliding with the clock.
    static let topEdgeFadeStartOffset: CGFloat = 200
    static let topEdgeFadeEndOffset: CGFloat = 260
    static let edgeNavigationSwipeWidth: CGFloat = 44
    static let edgeNavigationTopExclusionHeight: CGFloat = 120
}
