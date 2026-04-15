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

struct CLILinkApproveRequest: Encodable {
    let approveToken: String
    let deviceName: String?

    enum CodingKeys: String, CodingKey {
        case approveToken = "approve_token"
        case deviceName = "device_name"
    }
}

struct CLILinkApproveResponse: Decodable {
    let keyPrefix: String

    enum CodingKeys: String, CodingKey {
        case keyPrefix = "key_prefix"
    }
}

final class CLILinkService {
    private let client: APIClient

    init(client: APIClient = .shared) {
        self.client = client
    }

    func approve(scannedCode: String, deviceName: String? = nil) async throws -> CLILinkApproveResponse {
        let payload = try CLILinkScanPayload.parse(from: scannedCode)
        let body = try JSONEncoder().encode(
            CLILinkApproveRequest(
                approveToken: payload.approveToken,
                deviceName: deviceName
            )
        )
        return try await client.request(
            APIRequestDescriptor(
                path: APIEndpoints.cliLinkApprove(sessionID: payload.sessionID),
                method: "POST",
                body: body
            )
        )
    }
}
