//
//  AuthState+Settings.swift
//  newsly
//

extension AuthState {
    var authenticatedUser: User? {
        guard case .authenticated(let user) = self else {
            return nil
        }
        return user
    }
}
