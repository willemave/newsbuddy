//
//  ErrorView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

struct ErrorView: View {
    let message: String
    let retryAction: (() -> Void)?

    var body: some View {
        StateView(
            role: .error(message: message),
            actionTitle: retryAction == nil ? nil : "Retry",
            action: retryAction
        )
    }
}
