//
//  StructuredSummaryView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI
import UIKit

// MARK: - Design Tokens
private enum SummaryDesign {
    static let sectionSpacing: CGFloat = 20
    static let itemSpacing: CGFloat = 12
    static let cornerRadius: CGFloat = 12
    static let quoteBarWidth: CGFloat = 3
}

struct StructuredSummaryView: View {
    let summary: StructuredSummary
    var contentId: Int?
    var onTopicDeepDive: ((String) -> Void)?

    @State private var isQuotesExpanded = true
    @State private var isKeyPointsExpanded = true
    @State private var isQuestionsExpanded = false
    @State private var isCounterArgsExpanded = false
    @State private var selectedTopic: String?
    @State private var topicSession: ChatSessionSummary?

    var body: some View {
        VStack(alignment: .leading, spacing: SummaryDesign.sectionSpacing) {
            // Quotes Section (first, expanded by default)
            if !summary.quotes.isEmpty {
                modernSection(
                    title: "Notable Quotes",
                    icon: "quote.opening",
                    iconColor: .purple,
                    isExpanded: $isQuotesExpanded
                ) {
                    VStack(alignment: .leading, spacing: 16) {
                        ForEach(summary.quotes, id: \.text) { quote in
                            modernQuoteCard(quote: quote)
                        }
                    }
                }
            }

            // Key Points Section (expanded by default)
            if !summary.bulletPoints.isEmpty {
                modernSection(
                    title: "Key Points",
                    icon: "list.bullet.rectangle",
                    iconColor: .blue,
                    isExpanded: $isKeyPointsExpanded
                ) {
                    VStack(alignment: .leading, spacing: SummaryDesign.itemSpacing) {
                        ForEach(summary.bulletPoints, id: \.text) { point in
                            ModernKeyPointRow(
                                point: point,
                                contentId: contentId,
                                onDigDeeper: { pointText in
                                    startKeyPointChat(keyPoint: pointText)
                                }
                            )
                        }
                    }
                }
            }

            // Questions Section
            if !(summary.questions ?? []).isEmpty {
                modernSection(
                    title: "Questions to Explore",
                    icon: "questionmark.circle",
                    iconColor: .orange,
                    isExpanded: $isQuestionsExpanded
                ) {
                    VStack(alignment: .leading, spacing: SummaryDesign.itemSpacing) {
                        ForEach(Array((summary.questions ?? []).enumerated()), id: \.offset) { index, question in
                            modernQuestionRow(index: index + 1, question: question)
                        }
                    }
                }
            }

            // Counter Arguments Section
            if !(summary.counterArguments ?? []).isEmpty {
                modernSection(
                    title: "Counter Arguments",
                    icon: "arrow.left.arrow.right",
                    iconColor: .red,
                    isExpanded: $isCounterArgsExpanded
                ) {
                    VStack(alignment: .leading, spacing: SummaryDesign.itemSpacing) {
                        ForEach(summary.counterArguments ?? [], id: \.self) { argument in
                            modernCounterArgRow(argument: argument)
                        }
                    }
                }
            }

            // Topics Section (always visible, no disclosure)
            if !summary.topics.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 8) {
                        Image(systemName: "tag")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        Text("Topics")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundColor(.secondary)
                            .textCase(.uppercase)
                            .tracking(0.5)
                    }

                    FlowLayout(spacing: 8) {
                        ForEach(summary.topics, id: \.self) { topic in
                            modernTopicPill(topic: topic)
                        }
                    }
                }
            }
        }
        .sheet(item: $topicSession) { session in
            NavigationStack {
                ChatSessionView(route: ChatSessionRoute(session: session))
            }
        }
    }

    // MARK: - Modern Section Component
    @ViewBuilder
    private func modernSection<Content: View>(
        title: String,
        icon: String,
        iconColor: Color,
        isExpanded: Binding<Bool>,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.wrappedValue.toggle()
                }
            } label: {
                HStack {
                    HStack(spacing: 8) {
                        Image(systemName: icon)
                            .font(.subheadline)
                            .foregroundColor(iconColor)
                        Text(title)
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundColor(.primary)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .fontWeight(.bold)
                        .foregroundColor(.secondary.opacity(0.6))
                        .rotationEffect(.degrees(isExpanded.wrappedValue ? 90 : 0))
                }
            }
            .buttonStyle(.plain)

            if isExpanded.wrappedValue {
                content()
                    .padding(.top, 14)
            }
        }
    }

    // MARK: - Modern Quote Card
    @ViewBuilder
    private func modernQuoteCard(quote: Quote) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(quote.text)
                .font(.callout)
                .italic()
                .foregroundColor(.primary.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)

            if let context = quote.context {
                Text("— \(context)")
                    .font(.footnote)
                    .fontWeight(.medium)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.leading, 14)
        .padding(.vertical, 2)
        .overlay(
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [.purple.opacity(0.8), .purple.opacity(0.4)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: SummaryDesign.quoteBarWidth),
            alignment: .leading
        )
    }

    // MARK: - Modern Question Row
    @ViewBuilder
    private func modernQuestionRow(index: Int, question: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(index)")
                .font(.caption)
                .fontWeight(.bold)
                .foregroundColor(.white)
                .frame(width: 22, height: 22)
                .background(
                    Circle()
                        .fill(Color.orange.opacity(0.8))
                )

            Text(question)
                .font(.callout)
                .foregroundColor(.primary.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Modern Counter Argument Row (no background)
    @ViewBuilder
    private func modernCounterArgRow(argument: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle")
                .font(.caption)
                .foregroundColor(.orange)
                .frame(width: 16)
                .padding(.top, 2)

            Text(argument)
                .font(.callout)
                .foregroundColor(.primary.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Modern Topic Pill (flat, no border)
    @ViewBuilder
    private func modernTopicPill(topic: String) -> some View {
        Text(topic)
            .font(.footnote)
            .fontWeight(.medium)
            .foregroundColor(.secondary)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color(.tertiarySystemFill))
            .clipShape(Capsule())
            .contextMenu {
                if contentId != nil {
                    Button {
                        selectedTopic = topic
                        startTopicChat(topic: topic)
                    } label: {
                        Label("Deep Dive: \(topic)", systemImage: "brain.head.profile")
                    }
                }
            }
    }

    private func startTopicChat(topic: String) {
        guard let contentId = contentId else { return }
        Task {
            do {
                let session = try await ChatService.shared.startTopicChat(
                    contentId: contentId,
                    topic: topic
                )
                topicSession = session
            } catch {
                print("Failed to start topic chat: \(error)")
            }
        }
    }

    private func startKeyPointChat(keyPoint: String) {
        guard let contentId = contentId else { return }
        Task {
            do {
                // Create a topic chat focused on this key point
                let session = try await ChatService.shared.startTopicChat(
                    contentId: contentId,
                    topic: "Dig deeper: \(keyPoint)"
                )
                topicSession = session
            } catch {
                print("Failed to start key point chat: \(error)")
            }
        }
    }

    // Helper function for category colors
    private func categoryColor(for category: String) -> Color {
        switch category.lowercased() {
        case "key_finding":
            return .green
        case "warning":
            return .red
        case "recommendation":
            return .blue
        default:
            return .gray
        }
    }
}

// MARK: - Modern Key Point Row

struct ModernKeyPointRow: View {
    let point: BulletPoint
    let contentId: Int?
    var onDigDeeper: ((String) -> Void)?

    @Environment(\.colorScheme) private var colorScheme

    private func categoryConfig(for category: String) -> (color: Color, icon: String) {
        switch category.lowercased() {
        case "key_finding":
            return (.green, "checkmark.circle.fill")
        case "warning":
            return (.red, "exclamationmark.triangle.fill")
        case "recommendation":
            return (.blue, "lightbulb.fill")
        default:
            return (.secondary, "circle.fill")
        }
    }

    private var bulletColor: Color {
        if let category = point.category {
            return categoryConfig(for: category).color
        }
        return .blue.opacity(0.7)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // Modern bullet with category color
            Circle()
                .fill(bulletColor)
                .frame(width: 6, height: 6)
                .padding(.top, 7)

            VStack(alignment: .leading, spacing: 6) {
                Text(point.text)
                    .font(.callout)
                    .foregroundColor(.primary.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)

                if let category = point.category {
                    let config = categoryConfig(for: category)
                    HStack(spacing: 4) {
                        Image(systemName: config.icon)
                            .font(.caption2)
                        Text(category.replacingOccurrences(of: "_", with: " ").capitalized)
                            .font(.caption)
                            .fontWeight(.medium)
                    }
                    .foregroundColor(config.color.opacity(0.9))
                }
            }
        }
        .contentShape(Rectangle())
        .contextMenu {
            Button {
                UIPasteboard.general.string = point.text
            } label: {
                Label("Copy", systemImage: "doc.on.doc")
            }

            if contentId != nil {
                Button {
                    onDigDeeper?(point.text)
                } label: {
                    Label("Dig Deeper", systemImage: "brain.head.profile")
                }
            }
        }
    }
}

// Legacy alias for backwards compatibility
typealias KeyPointRow = ModernKeyPointRow

// Simple flow layout for topics
struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let result = FlowResult(
            in: proposal.replacingUnspecifiedDimensions().width,
            subviews: subviews,
            spacing: spacing
        )
        return result.bounds
    }
    
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = FlowResult(
            in: bounds.width,
            subviews: subviews,
            spacing: spacing
        )
        for row in result.rows {
            for (frameIndex, subviewIndex) in row.indices.enumerated() {
                let frame = row.frames[frameIndex]
                let position = CGPoint(
                    x: bounds.minX + frame.minX,
                    y: bounds.minY + frame.minY
                )
                subviews[subviewIndex].place(at: position, proposal: ProposedViewSize(frame.size))
            }
        }
    }
    
    struct FlowResult {
        var bounds = CGSize.zero
        var rows = [Row]()
        
        struct Row {
            var indices: Range<Int>
            var frames: [CGRect]
        }
        
        init(in maxPossibleWidth: CGFloat, subviews: Subviews, spacing: CGFloat) {
            var itemsInRow = 0
            var remainingWidth = maxPossibleWidth.isFinite ? maxPossibleWidth : .greatestFiniteMagnitude
            var rowMinY: CGFloat = 0.0
            var rowHeight: CGFloat = 0.0
            var rows = [Row]()
            
            for (index, subview) in zip(subviews.indices, subviews) {
                let idealSize = subview.sizeThatFits(.unspecified)
                if index != 0 && widthInRow(index: index, idealWidth: idealSize.width, spacing: spacing) > remainingWidth {
                    finalizeRow(indices: index - itemsInRow..<index, y: rowMinY, rows: &rows)
                    
                    bounds.width = max(bounds.width, maxPossibleWidth - remainingWidth)
                    rowMinY += rowHeight + spacing
                    itemsInRow = 0
                    remainingWidth = maxPossibleWidth
                    rowHeight = 0
                }
                
                addToRow(index: index, idealSize: idealSize, spacing: spacing, &remainingWidth, &rowHeight)
                
                itemsInRow += 1
            }
            
            if itemsInRow > 0 {
                finalizeRow(indices: subviews.count - itemsInRow..<subviews.count, y: rowMinY, rows: &rows)
                bounds.width = max(bounds.width, maxPossibleWidth - remainingWidth)
            }
            
            bounds.height = rowMinY + rowHeight
            self.rows = rows
            
            func widthInRow(index: Int, idealWidth: CGFloat, spacing: CGFloat) -> CGFloat {
                idealWidth + (index == 0 ? 0 : spacing)
            }
            
            func addToRow(index: Int, idealSize: CGSize, spacing: CGFloat, _ remainingWidth: inout CGFloat, _ rowHeight: inout CGFloat) {
                let width = widthInRow(index: index, idealWidth: idealSize.width, spacing: spacing)
                
                remainingWidth -= width
                rowHeight = max(rowHeight, idealSize.height)
            }
            
            func finalizeRow(indices: Range<Int>, y: CGFloat, rows: inout [Row]) {
                var frames = [CGRect]()
                var x = 0.0
                for index in indices {
                    let idealSize = subviews[index].sizeThatFits(.unspecified)
                    let width = idealSize.width
                    let height = idealSize.height
                    frames.append(CGRect(x: x, y: y, width: width, height: height))
                    x += width + spacing
                }
                rows.append(Row(indices: indices, frames: frames))
            }
        }
    }
}

#Preview {
    StructuredSummaryView(summary: StructuredSummary(
        title: "Sample Title",
        overview: "This is a sample overview",
        bulletPoints: [
            BulletPoint(text: "Point 1", category: "key_finding"),
            BulletPoint(text: "Point 2", category: nil)
        ],
        quotes: [
            Quote(text: "Sample quote", context: "John Doe", attribution: nil)
        ],
        topics: ["Topic 1", "Topic 2"],
        questions: [
            "What are the implications of this approach?",
            "How might this affect existing systems?"
        ],
        counterArguments: [
            "Some critics argue that this approach is too complex",
            "Alternative methods might be more efficient"
        ],
        summarizationDate: nil,
        classification: "to_read"
    ))
    .padding()
}
