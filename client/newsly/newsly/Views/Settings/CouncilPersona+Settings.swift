//
//  CouncilPersona+Settings.swift
//  newsly
//

import Foundation

extension Array where Element == CouncilPersona {
    func normalizedForSettings() -> [CouncilPersona] {
        enumerated().map { index, persona in
            CouncilPersona(
                id: persona.id,
                displayName: persona.displayName.trimmingCharacters(in: .whitespacesAndNewlines),
                sortOrder: index
            )
        }
    }
}
