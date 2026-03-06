//
//  AppSettings.swift
//  newsly
//
//  Created by Assistant on 7/9/25.
//

import Combine
import Foundation
import SwiftUI

enum FastNewsMode: String, CaseIterable {
    case newsList = "news_list"
    case dailyDigest = "daily_digest"

    var title: String {
        switch self {
        case .newsList:
            return "News List"
        case .dailyDigest:
            return "Daily Roll-Up"
        }
    }
}

class AppSettings: ObservableObject {
    static let shared = AppSettings()
    
    @AppStorage("serverHost", store: SharedContainer.userDefaults) var serverHost: String = "localhost"
    @AppStorage("serverPort", store: SharedContainer.userDefaults) var serverPort: String = "8000"
    @AppStorage("useHTTPS", store: SharedContainer.userDefaults) var useHTTPS: Bool = false
    @AppStorage("appTextSizeIndex", store: SharedContainer.userDefaults) var appTextSizeIndex: Int = 1
    @AppStorage("contentTextSizeIndex", store: SharedContainer.userDefaults) var contentTextSizeIndex: Int = 2
    @AppStorage("fastNewsMode", store: SharedContainer.userDefaults) var fastNewsMode: String = FastNewsMode.newsList.rawValue
    @AppStorage("useLongFormCardStack", store: SharedContainer.userDefaults) var useLongFormCardStack: Bool = true
    @AppStorage("showLiveVoiceDebugText", store: SharedContainer.userDefaults) var showLiveVoiceDebugText: Bool = false
    private var normalizedHost: String {
#if targetEnvironment(simulator)
        if serverHost.caseInsensitiveCompare("localhost") == .orderedSame {
            return "127.0.0.1"
        }
#endif
        return serverHost
    }

    var baseURL: String {
        let scheme = useHTTPS ? "https" : "http"
        return "\(scheme)://\(normalizedHost):\(serverPort)"
    }
    
    private init() {}
}
