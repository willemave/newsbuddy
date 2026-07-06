//
//  ContentZoomTransition.swift
//  newsly
//

import SwiftUI

extension View {
    @ViewBuilder
    func matchedContentZoomSource<ID: Hashable>(id: ID, namespace: Namespace.ID?) -> some View {
        if let namespace {
            matchedTransitionSource(id: id, in: namespace)
        } else {
            self
        }
    }

    @ViewBuilder
    func contentZoomNavigationTransition<ID: Hashable>(id: ID, namespace: Namespace.ID?) -> some View {
        if let namespace {
            navigationTransition(.zoom(sourceID: id, in: namespace))
        } else {
            self
        }
    }
}
