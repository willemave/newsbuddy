import Foundation

enum OpenAIServiceError: LocalizedError {
    case notAuthenticated
    case invalidResponse
    case serverError(statusCode: Int, message: String?)

    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "You must be signed in to use voice dictation."
        case .invalidResponse:
            return "Invalid response from transcription service."
        case .serverError(let statusCode, let message):
            return "Transcription failed (\(statusCode)): \(message ?? "Unknown error")"
        }
    }
}

final class OpenAIService {
    static let shared = OpenAIService()
    private let client = APIClient.shared

    private init() {}

    @discardableResult
    func refreshTranscriptionAvailability() async -> Bool {
        do {
            _ = try await fetchRealtimeToken()
            await MainActor.run {
                AppSettings.shared.setBackendTranscriptionAvailable(true)
            }
            return true
        } catch {
            await MainActor.run {
                AppSettings.shared.setBackendTranscriptionAvailable(false)
            }
            return false
        }
    }

    func fetchRealtimeToken() async throws -> RealtimeTokenResponse {
        try await client.request(
            APIEndpoints.openaiRealtimeToken,
            method: "POST"
        )
    }

    func transcribeAudio(
        fileURL: URL,
        filename: String = "audio.m4a"
    ) async throws -> AudioTranscriptionResponse {
        let audioData = try Data(contentsOf: fileURL)
        return try await uploadAudioTranscription(
            audioData: audioData,
            filename: filename,
            allowRefresh: true
        )
    }

    private func uploadAudioTranscription(
        audioData: Data,
        filename: String,
        allowRefresh: Bool
    ) async throws -> AudioTranscriptionResponse {
        let accessToken = try await fetchAccessToken()
        let request = try buildTranscriptionRequest(
            accessToken: accessToken,
            audioData: audioData,
            filename: filename
        )
        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw OpenAIServiceError.invalidResponse
        }

        if httpResponse.statusCode == 401 || httpResponse.statusCode == 403 {
            guard allowRefresh else {
                throw OpenAIServiceError.notAuthenticated
            }

            do {
                _ = try await AuthenticationService.shared.refreshAccessToken()
            } catch {
                throw OpenAIServiceError.notAuthenticated
            }
            return try await uploadAudioTranscription(
                audioData: audioData,
                filename: filename,
                allowRefresh: false
            )
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8)
            throw OpenAIServiceError.serverError(
                statusCode: httpResponse.statusCode,
                message: message
            )
        }

        do {
            return try JSONDecoder().decode(AudioTranscriptionResponse.self, from: data)
        } catch {
            throw OpenAIServiceError.invalidResponse
        }
    }

    private func fetchAccessToken() async throws -> String {
        if let existing = KeychainManager.shared.getToken(key: .accessToken),
           !existing.isEmpty {
            return existing
        }

        guard KeychainManager.shared.getToken(key: .refreshToken) != nil else {
            throw OpenAIServiceError.notAuthenticated
        }

        let refreshed: String
        do {
            refreshed = try await AuthenticationService.shared.refreshAccessToken()
        } catch {
            throw OpenAIServiceError.notAuthenticated
        }
        guard !refreshed.isEmpty else {
            throw OpenAIServiceError.notAuthenticated
        }
        return refreshed
    }

    private func buildTranscriptionRequest(
        accessToken: String,
        audioData: Data,
        filename: String
    ) throws -> URLRequest {
        guard let url = URL(string: AppSettings.shared.baseURL + APIEndpoints.openaiTranscriptions) else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

        let boundary = UUID().uuidString
        request.setValue(
            "multipart/form-data; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append(
            "Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(
                using: .utf8
            )!
        )
        body.append("Content-Type: audio/m4a\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n".data(using: .utf8)!)
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body

        return request
    }
}
