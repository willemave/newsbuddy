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
    @AppStorage("articleReaderBodyFontSize", store: SharedContainer.userDefaults)
    private var storedBodyFontSize: Double = 18

    private let minBodyFontSize: CGFloat = 16
    private let maxBodyFontSize: CGFloat = 24
    private var bodyFontSize: CGFloat { CGFloat(storedBodyFontSize).clamped(to: minBodyFontSize...maxBodyFontSize) }

    var body: some View {
        VStack(spacing: 0) {
            readerToolbar

            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    readerHeader

                    Group {
                        if let text = articleBody?.text.trimmingCharacters(in: .whitespacesAndNewlines),
                           !text.isEmpty {
                            SelectableMarkdownView(
                                markdown: text,
                                textColor: .appReaderBodyText,
                                baseFont: readerUIFont,
                                adjustsFontForContentSizeCategory: true,
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
                .padding(.horizontal, Spacing.readerHorizontal)
                .padding(.top, 30)
                .padding(.bottom, 56)
                .frame(maxWidth: .infinity)
            }
            .accessibilityIdentifier("article.reader.screen")
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
    }

    private var readerToolbar: some View {
        HStack(spacing: 8) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .semibold))
                    .frame(width: 44, height: 44)
                    .background(Color.surfaceTertiary.opacity(0.78), in: Circle())
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Close reader")
            .accessibilityIdentifier("article.reader.close")

            Spacer()

            if let url = URL(string: content.url) {
                Button {
                    openURL(url)
                } label: {
                    Image(systemName: "safari")
                        .font(.system(size: 17, weight: .regular))
                        .frame(width: 44, height: 44)
                        .background(Color.surfaceTertiary.opacity(0.78), in: Circle())
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Open original article")
                .accessibilityIdentifier("article.reader.open_original")
            }

            HStack(spacing: 2) {
                Button {
                    updateBodyFontSize(by: -1)
                } label: {
                    Image(systemName: "minus")
                        .font(.system(size: 14, weight: .semibold))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .disabled(bodyFontSize <= minBodyFontSize)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Decrease text size")
                .accessibilityIdentifier("article.reader.text_size.decrease")

                Text("Aa")
                    .font(.custom("Newsreader", size: 16).weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .frame(width: 34, height: 34)
                    .accessibilityHidden(true)

                Button {
                    updateBodyFontSize(by: 1)
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 14, weight: .semibold))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .disabled(bodyFontSize >= maxBodyFontSize)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Increase text size")
                .accessibilityIdentifier("article.reader.text_size.increase")
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.onSurface)
            .padding(3)
            .background(.regularMaterial, in: Capsule())
            .overlay {
                Capsule()
                    .stroke(Color.outlineVariant.opacity(0.25), lineWidth: 0.5)
            }
        }
        .foregroundStyle(Color.onSurface)
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(0.28))
                .frame(height: 0.5)
        }
    }

    private var readerHeader: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(content.displayTitle)
                .font(.custom("Newsreader", size: 34).weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("article.reader.title")

            HStack(spacing: 8) {
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

                if let estimatedReadTime {
                    Circle()
                        .fill(Color.onSurfaceSecondary.opacity(0.45))
                        .frame(width: 3, height: 3)

                    Text(estimatedReadTime)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            Rectangle()
                .fill(Color.outlineVariant.opacity(0.34))
                .frame(width: 54, height: 1)
                .padding(.top, 2)
        }
    }

    private var readerLoadingState: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(0..<8, id: \.self) { index in
                loadingLine(index)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 14)
        .accessibilityLabel("Loading article")
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

    private var estimatedReadTime: String? {
        guard let text = articleBody?.text.trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty else {
            return nil
        }
        let wordCount = text.split { $0.isWhitespace || $0.isNewline }.count
        guard wordCount > 0 else { return nil }
        let minutes = max(1, Int(ceil(Double(wordCount) / 225.0)))
        return "\(minutes) min read"
    }

    private var sourceLabel: String? {
        guard let source = content.source?.trimmingCharacters(in: .whitespacesAndNewlines),
              !source.isEmpty else {
            return nil
        }
        return source
    }

    private func updateBodyFontSize(by delta: CGFloat) {
        storedBodyFontSize = Double((bodyFontSize + delta).clamped(to: minBodyFontSize...maxBodyFontSize))
    }

    private func loadingLineWidth(for index: Int) -> CGFloat? {
        switch index {
        case 0: return 260
        case 1: return 690
        case 2: return 640
        case 3: return 705
        case 4: return 520
        case 5: return 660
        case 6: return 610
        default: return 380
        }
    }

    private func loadingLine(_ index: Int) -> some View {
        let opacity = index % 3 == 0 ? 0.12 : 0.08
        let height: CGFloat = index == 0 ? 17 : 13

        return RoundedRectangle(cornerRadius: 3)
            .fill(Color.onSurface.opacity(opacity))
            .frame(height: height)
            .frame(maxWidth: loadingLineWidth(for: index), alignment: .leading)
            .redacted(reason: .placeholder)
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
