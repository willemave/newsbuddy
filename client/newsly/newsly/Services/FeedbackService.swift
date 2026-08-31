//
//  FeedbackService.swift
//  newsly
//

import Foundation
import UIKit

final class FeedbackService {
    static let shared = FeedbackService()

    private let client: APIClient

    init(client: APIClient = .shared) {
        self.client = client
    }

    func submit(message: String) async throws {
        let request = await makeRequest(message: message)
        let body = try JSONEncoder().encode(request)
        let _: APISubmitFeedbackResponse = try await client.request(
            APIEndpoints.feedback,
            method: .post,
            body: body
        )
    }

    @MainActor
    private func makeRequest(message: String) -> APISubmitFeedbackRequest {
        let info = Bundle.main.infoDictionary
        return APISubmitFeedbackRequest(
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
