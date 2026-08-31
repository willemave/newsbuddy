import Foundation

enum CLILinkError: LocalizedError {
    case invalidScannedCode
    case missingSessionID
    case missingApproveToken

    var errorDescription: String? {
        switch self {
        case .invalidScannedCode:
            return "The scanned QR code is not a valid Newsbuddy CLI link."
        case .missingSessionID:
            return "The scanned QR code is missing a session ID."
        case .missingApproveToken:
            return "The scanned QR code is missing an approval token."
        }
    }
}

struct CLILinkScanPayload: Equatable {
    let sessionID: String
    let approveToken: String

    static func parse(from scannedCode: String) throws -> CLILinkScanPayload {
        guard let url = URL(string: scannedCode) else {
            throw CLILinkError.invalidScannedCode
        }
        return try parse(from: url)
    }

    static func parse(from url: URL) throws -> CLILinkScanPayload {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == "newsly",
              components.host == "cli-link"
        else {
            throw CLILinkError.invalidScannedCode
        }

        let queryItems = components.queryItems ?? []
        guard let sessionID = queryItems.first(where: { $0.name == "session_id" })?.value,
              !sessionID.isEmpty
        else {
            throw CLILinkError.missingSessionID
        }
        guard let approveToken = queryItems.first(where: { $0.name == "approve_token" })?.value,
              !approveToken.isEmpty
        else {
            throw CLILinkError.missingApproveToken
        }
        return CLILinkScanPayload(sessionID: sessionID, approveToken: approveToken)
    }

    static func canHandle(_ url: URL) -> Bool {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return false
        }
        return components.scheme == "newsly" && components.host == "cli-link"
    }
}

typealias CLILinkApproveResponse = APICliLinkApproveResponse

final class CLILinkService {
    private let client: APIClient

    init(client: APIClient = .shared) {
        self.client = client
    }

    func approve(scannedCode: String, deviceName: String? = nil) async throws -> CLILinkApproveResponse {
        let payload = try CLILinkScanPayload.parse(from: scannedCode)
        let body = try JSONEncoder().encode(
            APICliLinkApproveRequest(
                approveToken: payload.approveToken,
                deviceName: deviceName
            )
        )
        let response: APICliLinkApproveResponse = try await client.request(
            APIEndpoints.cliLinkApprove(sessionID: payload.sessionID),
            method: .post,
            body: body
        )
        return response
    }
}
