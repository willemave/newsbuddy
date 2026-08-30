//
//  FeedbackService.swift
//  newsly
//

import Foundation
import UIKit

private struct SubmitFeedbackRequest: Encodable {
    let message: String
    let source: String
    let appVersion: String?
    let buildNumber: String?
    let platform: String
    let osVersion: String
    let deviceModel: String

    enum CodingKeys: String, CodingKey {
        case message
        case source
        case appVersion = "app_version"
        case buildNumber = "build_number"
        case platform
        case osVersion = "os_version"
        case deviceModel = "device_model"
    }
}

final class FeedbackService {
    static let shared = FeedbackService()

    private let client: APIClient

    init(client: APIClient = .shared) {
        self.client = client
    }

    func submit(message: String) async throws {
        let request = await makeRequest(message: message)
        let body = try JSONEncoder().encode(request)
        try await client.requestVoid(
            APIEndpoints.feedback,
            method: .post,
            body: body
        )
    }

    @MainActor
    private func makeRequest(message: String) -> SubmitFeedbackRequest {
        let info = Bundle.main.infoDictionary
        return SubmitFeedbackRequest(
            message: message,
            source: "ios_settings",
            appVersion: info?["CFBundleShortVersionString"] as? String,
            buildNumber: info?["CFBundleVersion"] as? String,
            platform: "ios",
            osVersion: UIDevice.current.systemVersion,
            deviceModel: UIDevice.current.model
        )
    }
}
