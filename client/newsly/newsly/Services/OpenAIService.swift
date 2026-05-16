import Foundation
import os.log

private let openAIServiceLogger = Logger(subsystem: "com.newsly", category: "OpenAIService")

private func openAIElapsedMilliseconds(since start: Date) -> Int {
    Int(Date().timeIntervalSince(start) * 1000)
}

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
        let startedAt = Date()
        do {
            _ = try await fetchAccessToken()
            await MainActor.run {
                AppSettings.shared.setBackendTranscriptionAvailable(true)
            }
            openAIServiceLogger.info(
                "Transcription availability refreshed | available=true elapsedMs=\(openAIElapsedMilliseconds(since: startedAt))"
            )
            return true
        } catch {
            await MainActor.run {
                AppSettings.shared.setBackendTranscriptionAvailable(false)
            }
            openAIServiceLogger.error(
                "Transcription availability refresh failed | elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            return false
        }
    }

    func transcribeAudio(
        fileURL: URL,
        filename: String = "audio.m4a"
    ) async throws -> AudioTranscriptionResponse {
        let startedAt = Date()
        let audioData = try Data(contentsOf: fileURL)
        openAIServiceLogger.info(
            "Audio transcription file loaded | filename=\(filename, privacy: .public) bytes=\(audioData.count) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt))"
        )
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
        let startedAt = Date()
        let accessToken = try await fetchAccessToken()
        let request = try buildTranscriptionRequest(
            accessToken: accessToken,
            audioData: audioData,
            filename: filename
        )
        openAIServiceLogger.info(
            "Audio transcription upload started | filename=\(filename, privacy: .public) bytes=\(audioData.count) allowRefresh=\(allowRefresh)"
        )
        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            openAIServiceLogger.error(
                "Audio transcription upload missing HTTP response | filename=\(filename, privacy: .public) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt))"
            )
            throw OpenAIServiceError.invalidResponse
        }
        openAIServiceLogger.info(
            "Audio transcription upload response | filename=\(filename, privacy: .public) status=\(httpResponse.statusCode) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) responseBytes=\(data.count)"
        )

        if httpResponse.statusCode == 401 || httpResponse.statusCode == 403 {
            guard allowRefresh else {
                throw OpenAIServiceError.notAuthenticated
            }

            do {
                _ = try await AuthenticationService.shared.refreshAccessToken()
            } catch {
                openAIServiceLogger.error(
                    "Audio transcription token refresh failed | filename=\(filename, privacy: .public) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
                )
                throw OpenAIServiceError.notAuthenticated
            }
            openAIServiceLogger.info(
                "Audio transcription retrying after token refresh | filename=\(filename, privacy: .public)"
            )
            return try await uploadAudioTranscription(
                audioData: audioData,
                filename: filename,
                allowRefresh: false
            )
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8)
            openAIServiceLogger.error(
                "Audio transcription upload HTTP error | filename=\(filename, privacy: .public) status=\(httpResponse.statusCode) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) detail=\(message ?? "nil", privacy: .public)"
            )
            throw OpenAIServiceError.serverError(
                statusCode: httpResponse.statusCode,
                message: message
            )
        }

        do {
            let decoded = try JSONDecoder().decode(AudioTranscriptionResponse.self, from: data)
            openAIServiceLogger.info(
                "Audio transcription decoded | filename=\(filename, privacy: .public) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) transcriptChars=\(decoded.text.count)"
            )
            return decoded
        } catch {
            openAIServiceLogger.error(
                "Audio transcription decode failed | filename=\(filename, privacy: .public) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
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
