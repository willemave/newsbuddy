//
//  ChatSessionRoute.swift
//  newsly
//
//  Created by Assistant on 12/6/25.
//

import Foundation

struct ChatSessionRoute: Hashable {
    let sessionId: Int
    let contentId: Int?

    init(sessionId: Int, contentId: Int? = nil) {
        self.sessionId = sessionId
        self.contentId = contentId
    }
}
