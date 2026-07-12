import Foundation

struct DebugLoginLink: Equatable {
    let userID: Int
    let serverHost: String
    let serverPort: String
    let useHTTPS: Bool

    init?(url: URL) {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == "newsly",
              components.host == "debug-login" else {
            return nil
        }

        let values = components.queryItems ?? []
        func value(named name: String) -> String? {
            values.first(where: { $0.name == name })?.value
        }
        guard let userIDValue = value(named: "user_id"),
              let userID = Int(userIDValue),
              userID > 0,
              let serverHost = value(named: "host")?.trimmingCharacters(in: .whitespacesAndNewlines),
              !serverHost.isEmpty,
              let serverPortValue = value(named: "port"),
              let serverPort = Int(serverPortValue),
              (1...65_535).contains(serverPort),
              let useHTTPS = Self.parseBoolean(value(named: "https")) else {
            return nil
        }

        self.userID = userID
        self.serverHost = serverHost
        self.serverPort = String(serverPort)
        self.useHTTPS = useHTTPS
    }

    private static func parseBoolean(_ value: String?) -> Bool? {
        switch value?.lowercased() {
        case "true", "1": return true
        case "false", "0": return false
        default: return nil
        }
    }
}
