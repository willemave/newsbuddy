//
//  RelevantLinksSection.swift
//  newsly
//

import SwiftUI

struct RelevantLinksSection: View {
    let links: [RelevantLink]
    let stateForLink: (String) -> LinkReadLaterState
    let onOpenURL: (URL) -> Void
    let onAddToReadLater: (RelevantLink) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ReaderSectionHeader("Links") {
                Spacer(minLength: 10)
                Text("\(links.count)")
                    .font(.appCaption.monospacedDigit().weight(.semibold))
                    .foregroundColor(Color.brandPrimary)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.brandPrimary.opacity(0.12), in: Capsule())
            }

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(links.enumerated()), id: \.element.id) { index, link in
                    linkRow(link)
                    if index < links.count - 1 {
                        Divider()
                            .overlay(Color.outlineVariant.opacity(0.35))
                            .padding(.vertical, 12)
                    }
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Links, \(links.count)")
    }

    @ViewBuilder
    private func linkRow(_ link: RelevantLink) -> some View {
        if let url = URL(string: link.url) {
            let state = stateForLink(link.id)
            HStack(alignment: .top, spacing: 10) {
                Button {
                    onOpenURL(url)
                } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(link.title ?? link.url)
                            .font(.appCallout.weight(.semibold))
                            .foregroundColor(Color.onSurface)
                            .multilineTextAlignment(.leading)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)

                        Text(link.reason)
                            .font(.appFootnote)
                            .foregroundColor(Color.onSurfaceSecondary)
                            .multilineTextAlignment(.leading)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)

                        HStack(spacing: 6) {
                            if let source = sourceLabel(link.source) {
                                Text(source)
                                    .font(.appCaption2)
                                    .fontWeight(.semibold)
                                    .foregroundColor(Color.onSurfaceTertiary)
                                    .textCase(.uppercase)
                                    .tracking(0.4)
                            }

                            Text(link.url)
                                .font(.appCaption2)
                                .foregroundColor(Color.onSurfaceTertiary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("content.relevant_link.\(link.id)")

                Spacer(minLength: 0)

                Button {
                    onAddToReadLater(link)
                } label: {
                    Group {
                        if state == .adding {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Image(systemName: readLaterIcon(for: state))
                        }
                    }
                    .font(.appSubheadline.weight(.medium))
                    .foregroundColor(state == .added ? .brandPrimary : .onSurfaceTertiary)
                    .frame(width: 40, height: 40)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isLinkActionDisabled(state))
                .accessibilityLabel(readLaterTitle(for: state))
                .accessibilityIdentifier("content.relevant_link.read_later.\(link.id)")
            }
        }
    }

    private func sourceLabel(_ source: String?) -> String? {
        switch source?.lowercased() {
        case "article":
            return "Article"
        case "community":
            return "Community"
        default:
            return nil
        }
    }

    private func isLinkActionDisabled(_ state: LinkReadLaterState) -> Bool {
        state == .adding || state == .added
    }

    private func readLaterTitle(for state: LinkReadLaterState) -> String {
        switch state {
        case .idle:
            return "Read Later"
        case .adding:
            return "Adding"
        case .added:
            return "Saved"
        case .failed:
            return "Retry"
        }
    }

    private func readLaterIcon(for state: LinkReadLaterState) -> String {
        switch state {
        case .idle:
            return "bookmark"
        case .adding:
            return "bookmark"
        case .added:
            return "checkmark"
        case .failed:
            return "arrow.clockwise"
        }
    }
}
