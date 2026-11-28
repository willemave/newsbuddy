//
//  LazyView.swift
//  newsly
//

import SwiftUI

/// Defers destination view and view-model construction until SwiftUI evaluates
/// the pushed view's `body` (avoiding eager allocations when rendering lists
/// of `NavigationLink`s).
struct LazyView<Content: View>: View {
    private let build: () -> Content

    init(@ViewBuilder _ build: @escaping () -> Content) {
        self.build = build
    }

    var body: some View {
        build()
    }
}
