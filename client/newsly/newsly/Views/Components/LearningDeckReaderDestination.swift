//
//  LearningDeckReaderDestination.swift
//  newsly
//

import Foundation

struct LearningDeckReaderDestination: Identifiable {
    let deck: LearningDeck
    let url: URL?

    var id: String { "\(deck.id)-\(url?.absoluteString ?? "generating")" }
}
