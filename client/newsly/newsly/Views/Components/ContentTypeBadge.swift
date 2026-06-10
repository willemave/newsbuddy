//
//  ContentTypeBadge.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

struct ContentTypeBadge: View {
    let contentType: APIContentType

    var body: some View {
        Text(contentType.displayName)
            .font(.terracottaCategoryPill)
            .tracking(0.5)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(Color.terracottaPrimary.opacity(0.1))
            .foregroundColor(Color.terracottaPrimary)
            .clipShape(Capsule())
    }
}
