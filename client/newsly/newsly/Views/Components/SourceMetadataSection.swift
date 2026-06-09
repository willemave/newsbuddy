//
//  SourceMetadataSection.swift
//  newsly
//

import SwiftUI

struct SourceMetadataSection: View {
    let metadata: SourceMetadata
    let openURL: (URL) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header

            if let synopsis = metadata.displaySynopsis {
                Text(synopsis)
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
            }

            authorsList
            facts
            arxivButton
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.readerBody)
                .foregroundColor(Color.onSurfaceSecondary)
                .accessibilityHidden(true)

            Text("Paper metadata")
                .font(.readerBody)
                .foregroundColor(Color.onSurfaceSecondary)
        }
    }

    @ViewBuilder
    private var authorsList: some View {
        let authors = Array(metadata.displayAuthors.prefix(8))
        if !authors.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(authors) { author in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "person")
                            .font(.appCaption)
                            .foregroundColor(Color.onSurfaceSecondary.opacity(0.75))
                            .frame(width: 18, height: 18)
                            .accessibilityHidden(true)

                        VStack(alignment: .leading, spacing: 2) {
                            if let name = author.displayName {
                                Text(name)
                                    .font(.appCaption.weight(.medium))
                                    .foregroundColor(Color.onSurface)
                            }
                            if let affiliation = author.displayAffiliation {
                                Text(affiliation)
                                    .font(.appCaption2)
                                    .foregroundColor(Color.onSurfaceSecondary)
                            }
                        }
                    }
                }
            }
        }
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let categoryLine = metadata.categoryLine {
                fact(icon: "tag", value: categoryLine)
            }
            if let publishedDate = metadata.publishedDateDisplay {
                fact(icon: "calendar", value: publishedDate)
            }
            if let sourceID = metadata.sourceID {
                fact(icon: "number", value: sourceID)
            }
        }
    }

    @ViewBuilder
    private var arxivButton: some View {
        if let arxivURL = metadata.arxivURL,
           let url = URL(string: arxivURL) {
            Button {
                openURL(url)
            } label: {
                Label("arXiv", systemImage: "arrow.up.right.square")
                    .font(.appCaption.weight(.medium))
            }
            .buttonStyle(.plain)
            .foregroundColor(Color.brandPrimary)
            .accessibilityIdentifier("content.source_metadata.open_arxiv")
        }
    }

    private func fact(icon: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: icon)
                .font(.appCaption2)
                .foregroundColor(Color.onSurfaceSecondary.opacity(0.72))
                .frame(width: 16)
                .accessibilityHidden(true)

            Text(value)
                .font(.appCaption)
                .foregroundColor(Color.onSurfaceSecondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
