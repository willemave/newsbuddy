//
//  SettingsBrandHeader.swift
//  newsly
//

import SwiftUI

struct SettingsBrandHeader: View {
    var body: some View {
        VStack(spacing: 10) {
            // The app icon itself, in the rounded-rect mask iOS gives it on the home screen.
            Image("AppMark")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 96, height: 96)
                .clipShape(RoundedRectangle(cornerRadius: 21.5, style: .continuous))
                // The dark icon tile nearly matches the sheet surface; a hairline keeps its edge legible.
                .overlay {
                    RoundedRectangle(cornerRadius: 21.5, style: .continuous)
                        .strokeBorder(Color.borderSubtle, lineWidth: 1)
                }
                .accessibilityLabel("Newsbuddy app icon")

            VStack(spacing: 2) {
                Text("Newsbuddy")
                    .font(.modalTitle.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                Text(appVersionLabel)
                    .font(.modalCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 8)
        .padding(.bottom, 4)
        .background { accentGlow }
    }

    /// A soft wash of the single brand accent behind the icon. It gives the
    /// glass cards something to refract as they scroll up past the header.
    private var accentGlow: some View {
        Circle()
            .fill(Color.brandPrimary.opacity(0.22))
            .frame(width: 280, height: 280)
            .blur(radius: 72)
            .offset(y: 40)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    private var appVersionLabel: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "—"
        let build = info?["CFBundleVersion"] as? String ?? "—"
        return "Version \(version) (\(build))"
    }
}
