import SwiftUI

struct NewsFilterView: View {
    @State private var model: NewsFilterViewModel
    let service: any NewsFilterServicing

    init(service: any NewsFilterServicing) {
        self.service = service
        _model = State(initialValue: NewsFilterViewModel(service: service))
    }

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text("Tell Newsbuddy which News stories to keep or skip. Articles and Podcasts are never filtered.")
                    .font(.modalSupporting)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Spacing.appHorizontalMargin)
                    .padding(.top, 12)

                VStack(spacing: 0) {
                    SettingsRow(icon: "line.3.horizontal.decrease.circle", title: "Filter News in Briefing") {
                        Toggle("Filter News in Briefing", isOn: $model.enabled)
                            .labelsHidden()
                            .accessibilityIdentifier("news_filter.enabled")
                    }
                }
                .settingsCard()
                .padding(.top, 16)
                .disabled(model.isBusy || model.preference == nil)

                SectionHeader(title: "Instructions", font: .modalSupporting.weight(.semibold))
                instructionsEditor
                    .disabled(model.isBusy || model.preference == nil)

                actions

                if !model.previewItems.isEmpty {
                    SectionHeader(title: "Draft preview", font: .modalSupporting.weight(.semibold))
                    previewList
                }

                SectionHeader(title: "Browse", font: .modalSupporting.weight(.semibold))
                VStack(spacing: 0) {
                    NavigationLink {
                        NewsFilterNewsView(service: service, filtered: false)
                    } label: {
                        SettingsRow(icon: "newspaper", title: "All News")
                    }
                    .buttonStyle(.plain)

                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    NavigationLink {
                        NewsFilterNewsView(service: service, filtered: true)
                    } label: {
                        SettingsRow(icon: "eye.slash", title: "Filtered Out")
                    }
                    .buttonStyle(.plain)
                }
                .settingsCard()

                Text("Filtered stories are never deleted. You can still read them here.")
                    .font(.modalCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .padding(.horizontal, Spacing.appHorizontalMargin + Spacing.rowHorizontal)
                    .padding(.top, 8)
            }
            .padding(.bottom, 48)
        }
        .tint(Color.brandPrimary)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .appNavigationTitle("News Filter")
        .task { if model.preference == nil { await model.load() } }
        .onChange(of: model.text) { _, _ in model.draftChanged() }
        .onChange(of: model.enabled) { _, _ in model.draftChanged() }
    }

    private var instructionsEditor: some View {
        @Bindable var model = model
        return VStack(alignment: .leading, spacing: 8) {
            ZStack(alignment: .topLeading) {
                TextEditor(text: $model.text)
                    .scrollContentBackground(.hidden)
                    .font(.modalBody)
                    .foregroundStyle(Color.onSurface)
                    .frame(minHeight: 132)
                    .accessibilityLabel("News filter instructions")
                    .accessibilityIdentifier("news_filter.text")

                if model.text.isEmpty {
                    Text("e.g. More science and climate research. Skip celebrity gossip and sports scores.")
                        .font(.modalBody)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 8)
                        .allowsHitTesting(false)
                }
            }

            HStack(alignment: .firstTextBaseline) {
                if let validation = model.validationMessage {
                    Text(validation)
                        .foregroundStyle(Color.statusDestructive)
                } else if !model.enabled && model.text.utf8.count > 8_192 {
                    Text("Shorten this filter to preview it.")
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
                Spacer(minLength: 8)
                Text("\(model.text.unicodeScalars.count.formatted()) / 8,000")
                    .monospacedDigit()
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .font(.modalCaption)
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 10)
        .settingsCard()
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Button {
                    Task { await model.preview() }
                } label: {
                    actionLabel(model.isPreviewing ? nil : "Preview")
                        .foregroundStyle(Color.onSurface)
                }
                .buttonStyle(.glass)
                .disabled(!model.canPreview)
                .accessibilityIdentifier("news_filter.preview")

                Button {
                    Task { await model.apply() }
                } label: {
                    actionLabel(model.isSaving ? nil : "Apply")
                        .foregroundStyle(Color.surfacePrimary)
                }
                .buttonStyle(.glassProminent)
                .tint(Color.onSurface)
                .disabled(!model.canApply)
                .accessibilityIdentifier("news_filter.apply")
            }
            .buttonBorderShape(.capsule)

            Group {
                if let message = model.message {
                    Text(message)
                        .accessibilityIdentifier("news_filter.message")
                } else {
                    Text("Preview tests this draft on up to 20 recent stories without saving. Apply affects News added from now on.")
                }
            }
            .font(.modalCaption)
            .foregroundStyle(Color.onSurfaceSecondary)
            .fixedSize(horizontal: false, vertical: true)

            if model.needsReload || (model.preference == nil && !model.isLoading) {
                Button("Reload saved filter") { Task { await model.load() } }
                    .font(.modalSupporting.weight(.semibold))
                    .accessibilityIdentifier("news_filter.reload")
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 16)
    }

    /// A nil title renders a spinner so the button keeps its size while busy.
    private func actionLabel(_ title: String?) -> some View {
        ZStack {
            Text(title ?? " ").opacity(title == nil ? 0 : 1)
            if title == nil { ProgressView() }
        }
        .font(.appCallout.weight(.semibold))
        .frame(maxWidth: .infinity, minHeight: 44)
    }

    private var previewList: some View {
        VStack(spacing: 0) {
            ForEach(Array(model.previewItems.enumerated()), id: \.element.news.id) { index, item in
                if index > 0 { RowDivider(leadingInset: Spacing.rowHorizontal) }
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: item.filterStatus == "filtered_out" ? "minus.circle" : "checkmark.circle")
                        .font(.appSymbol(size: 14, weight: .regular))
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.news.title)
                            .font(.modalBody)
                            .foregroundStyle(item.filterStatus == "filtered_out" ? Color.onSurfaceSecondary : Color.onSurface)
                            .lineLimit(2)
                        Text(newsFilterStatusText(item.filterStatus))
                            .font(.modalCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, Spacing.rowHorizontal)
                .padding(.vertical, Spacing.rowVertical)
                .accessibilityElement(children: .combine)
            }
        }
        .settingsCard()
    }
}

struct SettingsNewsFilterSection: View {
    @Environment(RootDependencyFactory.self) private var factory

    var body: some View {
        VStack(spacing: 0) {
            NavigationLink {
                NewsFilterView(service: factory.newsFilterService)
            } label: {
                SettingsRow(icon: "line.3.horizontal.decrease.circle", title: "News Filter") {
                    NavigationChevron()
                }
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("settings.news_filter")

            RowDivider(leadingInset: Spacing.rowHorizontal)

            NavigationLink {
                NewsFilterNewsView(service: factory.newsFilterService, filtered: false)
            } label: {
                SettingsRow(icon: "newspaper", title: "All News") {
                    NavigationChevron()
                }
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("settings.all_news")
        }
        .settingsCard()
        .padding(.top, 12)
    }
}
