//
//  FeedListText.swift
//  newsly
//

import SwiftUI

struct FeedListText: View {
    let text: String
    let textColor: Color
    let font: Font
    let lineLimit: Int
    var onDigDeeper: ((String) -> Void)?
    var onTap: (() -> Void)?

    init(
        _ text: String,
        textColor: Color = .readerBodyText,
        font: Font = .readerBody,
        lineLimit: Int = 3,
        onDigDeeper: ((String) -> Void)? = nil,
        onTap: (() -> Void)? = nil
    ) {
        self.text = text
        self.textColor = textColor
        self.font = font
        self.lineLimit = lineLimit
        self.onDigDeeper = onDigDeeper
        self.onTap = onTap
    }

    var body: some View {
        Text(text)
            .font(font)
            .foregroundStyle(textColor)
            .lineLimit(lineLimit)
            .truncationMode(.tail)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .modifier(FeedListTextTapModifier(onTap: onTap))
            .modifier(FeedListTextDigDeeperModifier(text: text, onDigDeeper: onDigDeeper))
    }
}

private struct FeedListTextTapModifier: ViewModifier {
    var onTap: (() -> Void)?

    @ViewBuilder
    func body(content: Content) -> some View {
        if let onTap {
            content.onTapGesture(perform: onTap)
        } else {
            content
        }
    }
}

private struct FeedListTextDigDeeperModifier: ViewModifier {
    let text: String
    var onDigDeeper: ((String) -> Void)?

    @ViewBuilder
    func body(content: Content) -> some View {
        if let onDigDeeper {
            content.contextMenu {
                Button {
                    onDigDeeper(text)
                } label: {
                    Label("Dig Deeper", systemImage: "magnifyingglass")
                }
            }
        } else {
            content
        }
    }
}
