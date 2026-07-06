//
//  SettingsBrandHeader.swift
//  newsly
//

import SwiftUI

struct SettingsBrandHeader: View {
    var body: some View {
        VStack(spacing: 10) {
            Image("Mascot")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 96, height: 96)
                .accessibilityLabel("Newsbuddy mascot")

            VStack(spacing: 2) {
                Text("Newsbuddy")
                    .font(.appTitle2.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                Text(appVersionLabel)
                    .font(.appFootnote)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 8)
        .padding(.bottom, 4)
    }

    private var appVersionLabel: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "—"
        let build = info?["CFBundleVersion"] as? String ?? "—"
        return "Version \(version) (\(build))"
    }
}
