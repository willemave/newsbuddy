//
//  ArticleReaderView.swift
//  newsly
//

import SwiftUI
import UIKit

struct ArticleReaderView: View {
    let content: ContentDetail
    let articleBody: ContentBody?
    let isLoading: Bool
    let errorMessage: String?
    let onRetry: () -> Void
    let onDigDeeper: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var bodyFontSize: CGFloat = 18

    private let minBodyFontSize: CGFloat = 16
    private let maxBodyFontSize: CGFloat = 24

    var body: some View {
        VStack(spacing: 0) {
            readerToolbar

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    readerHeader

                    Group {
                        if let text = articleBody?.text.trimmingCharacters(in: .whitespacesAndNewlines),
                           !text.isEmpty {
                            SelectableMarkdownView(
                                markdown: text,
                                textColor: .appOnSurface,
                                baseFont: readerUIFont,
                                onDigDeeper: onDigDeeper
                            )
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .accessibilityIdentifier("article.reader.body")
                        } else if isLoading || errorMessage == nil {
                            readerLoadingState
                        } else if let errorMessage {
                            readerErrorState(errorMessage)
                        }
                    }
                }
                .frame(maxWidth: 720, alignment: .leading)
                .padding(.horizontal, 22)
                .padding(.top, 26)
                .padding(.bottom, 48)
                .frame(maxWidth: .infinity)
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .accessibilityIdentifier("article.reader.screen")
    }

    private var readerToolbar: some View {
        HStack(spacing: 8) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .semibold))
                    .frame(width: 40, height: 40)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close reader")

            Spacer()

            if let url = URL(string: content.url) {
                Button {
                    openURL(url)
                } label: {
                    Image(systemName: "safari")
                        .font(.system(size: 17, weight: .regular))
                        .frame(width: 40, height: 40)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Open original article")
            }

            HStack(spacing: 2) {
                Button {
                    bodyFontSize = max(minBodyFontSize, bodyFontSize - 1)
                } label: {
                    Image(systemName: "minus")
                        .font(.system(size: 14, weight: .semibold))
                        .frame(width: 34, height: 34)
                }
                .disabled(bodyFontSize <= minBodyFontSize)
                .accessibilityLabel("Decrease text size")

                Text("Aa")
                    .font(.custom("Newsreader", size: 16).weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .frame(width: 34, height: 34)

                Button {
                    bodyFontSize = min(maxBodyFontSize, bodyFontSize + 1)
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 14, weight: .semibold))
                        .frame(width: 34, height: 34)
                }
                .disabled(bodyFontSize >= maxBodyFontSize)
                .accessibilityLabel("Increase text size")
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.onSurface)
            .padding(3)
            .background(Color.surfaceTertiary, in: Capsule())
        }
        .foregroundStyle(Color.onSurface)
        .padding(.horizontal, 14)
        .padding(.top, 10)
        .padding(.bottom, 8)
    }

    private var readerHeader: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(content.displayTitle)
                .font(.custom("Newsreader", size: 32).weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("article.reader.title")

            HStack(spacing: 7) {
                if let source = sourceLabel {
                    Text(source)
                        .font(.terracottaBodySmall.weight(.semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)

                    Circle()
                        .fill(Color.onSurfaceSecondary.opacity(0.45))
                        .frame(width: 3, height: 3)
                }

                ContentTimestampText(
                    rawValue: content.primaryTimestamp,
                    style: .detailMeta,
                    fallback: "Recent"
                )
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
    }

    private var readerLoadingState: some View {
        HStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text("Loading article")
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 24)
    }

    private func readerErrorState(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.circle")
                    .foregroundStyle(Color.statusDestructive)
                Text(message)
                    .font(.terracottaBodyMedium)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }

            Button {
                onRetry()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.clockwise")
                    Text("Retry")
                }
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(Color.surfacePrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Color.terracottaPrimary, in: Capsule())
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 24)
    }

    private var readerUIFont: UIFont {
        UIFont(name: "Newsreader", size: bodyFontSize)
            ?? .systemFont(ofSize: bodyFontSize, weight: .regular)
    }

    private var sourceLabel: String? {
        guard let source = content.source?.trimmingCharacters(in: .whitespacesAndNewlines),
              !source.isEmpty else {
            return nil
        }
        return source
    }
}
