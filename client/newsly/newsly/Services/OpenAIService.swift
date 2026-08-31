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

    private let apiClient: APIClient
    private let credentialSession: any CredentialSessionProviding

    init(
        apiClient: APIClient = .shared,
        credentialSession: any CredentialSessionProviding = CredentialSession.shared
    ) {
        self.apiClient = apiClient
        self.credentialSession = credentialSession
    }

    @discardableResult
    func refreshTranscriptionAvailability() async -> Bool {
        let startedAt = Date()
        do {
            _ = try await credentialSession.accessToken(for: .required)
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
            filename: filename
        )
    }

    private func uploadAudioTranscription(
        audioData: Data,
        filename: String
    ) async throws -> AudioTranscriptionResponse {
        let startedAt = Date()
        let multipart = buildMultipartBody(
            audioData: audioData,
            filename: filename
        )
        openAIServiceLogger.info(
            "Audio transcription upload started | filename=\(filename, privacy: .public) bytes=\(audioData.count)"
        )

        let data: Data
        let response: HTTPURLResponse
        do {
            (data, response) = try await apiClient.requestHTTP(
                APIEndpoints.openaiTranscriptions,
                method: .post,
                body: multipart.body,
                headers: ["Content-Type": multipart.contentType],
                authentication: .required
            )
        } catch {
            let failure = ClientFailure.classify(error)
            switch failure {
            case .authenticationRequired, .authenticationExpired:
                throw OpenAIServiceError.notAuthenticated
            case .server(let statusCode, let error):
                openAIServiceLogger.error(
                    "Audio transcription upload HTTP error | filename=\(filename, privacy: .public) status=\(statusCode) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) code=\(error.code, privacy: .public) requestId=\(error.requestID, privacy: .public)"
                )
                throw OpenAIServiceError.serverError(
                    statusCode: statusCode,
                    message: error.message
                )
            case .http(let statusCode, let detail):
                openAIServiceLogger.error(
                    "Audio transcription upload HTTP error | filename=\(filename, privacy: .public) status=\(statusCode) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) detail=\(detail ?? "nil", privacy: .public)"
                )
                throw OpenAIServiceError.serverError(
                    statusCode: statusCode,
                    message: detail
                )
            case .invalidResponse, .unexpected:
                throw OpenAIServiceError.invalidResponse
            default:
                throw failure
            }
        }

        openAIServiceLogger.info(
            "Audio transcription upload response | filename=\(filename, privacy: .public) status=\(response.statusCode) elapsedMs=\(openAIElapsedMilliseconds(since: startedAt)) responseBytes=\(data.count)"
        )

        do {
            let apiResponse = try JSONDecoder().decode(APIAudioTranscriptionResponse.self, from: data)
            let decoded = AudioTranscriptionResponse(api: apiResponse)
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

    private func buildMultipartBody(
        audioData: Data,
        filename: String
    ) -> (body: Data, contentType: String) {
        let boundary = UUID().uuidString
        var body = Data()
        body.append(Data("--\(boundary)\r\n".utf8))
        body.append(Data(
            "Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".utf8
        ))
        body.append(Data("Content-Type: audio/m4a\r\n\r\n".utf8))
        body.append(audioData)
        body.append(Data("\r\n".utf8))
        body.append(Data("--\(boundary)--\r\n".utf8))

        return (
            body,
            "multipart/form-data; boundary=\(boundary)"
        )
    }
}
