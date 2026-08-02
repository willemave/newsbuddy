import SwiftUI

struct SettingsLegalSection: View {
    private let links: [(title: String, icon: String, url: URL)] = [
        ("Privacy Policy", "hand.raised", URL(string: "https://news.willemsavenue.com/privacy")!),
        ("Support", "questionmark.circle", URL(string: "https://news.willemsavenue.com/support")!),
        ("Terms of Use", "doc.text", URL(string: "https://news.willemsavenue.com/terms")!),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Legal & Support")
            VStack(spacing: 0) {
                ForEach(Array(links.enumerated()), id: \.element.title) { index, link in
                    if index > 0 { RowDivider(leadingInset: Spacing.rowHorizontal) }
                    Link(destination: link.url) {
                        SettingsRow(icon: link.icon, title: link.title) { NavigationChevron() }
                    }
                    .buttonStyle(.plain)
                }
            }
            .settingsCard()
        }
    }
}
