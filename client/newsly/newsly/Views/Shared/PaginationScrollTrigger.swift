//
//  PaginationScrollTrigger.swift
//  newsly
//

import SwiftUI

private let paginationTriggerDepth: CGFloat = 0.8

extension View {
    func onPaginationThresholdReached(
        perform action: @escaping @MainActor () async -> Void
    ) -> some View {
        onScrollGeometryChange(for: Bool.self) { geometry in
            guard geometry.contentSize.height > 0 else { return false }
            return geometry.visibleRect.maxY + geometry.contentInsets.bottom
                >= geometry.contentSize.height * paginationTriggerDepth
        } action: { _, shouldLoadNextPage in
            guard shouldLoadNextPage else { return }
            Task { await action() }
        }
    }
}
