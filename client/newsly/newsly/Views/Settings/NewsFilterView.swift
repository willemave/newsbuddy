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
        Form {
            Section {
                Toggle("Filter News in Briefing", isOn: $model.enabled)
                    .accessibilityIdentifier("news_filter.enabled")
                Text("Describe which News you want, or what to leave out. Articles and Podcasts are unaffected.")
                    .foregroundStyle(Color.onSurfaceSecondary)
                TextEditor(text: $model.text)
                    .frame(minHeight: 180)
                    .accessibilityLabel("News filter instructions")
                    .accessibilityIdentifier("news_filter.text")
                Text("\(model.text.unicodeScalars.count) / 8,000 characters")
                    .font(.modalCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                if !model.enabled && model.text.utf8.count > 8_192 {
                    Text("Shorten this filter to preview it.")
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
                if let validation = model.validationMessage {
                    Text(validation).foregroundStyle(Color.statusDestructive)
                }
            } footer: {
                Text("Apply changes only to upcoming News additions. You can always read stories in All News, including those filtered out.")
            }
            .disabled(model.isBusy || model.preference == nil)

            Section {
                Button("Preview on up to 20 recent stories") {
                    Task { await model.preview() }
                }
                .disabled(!model.canPreview)
                .accessibilityIdentifier("news_filter.preview")
                Button("Apply") { Task { await model.apply() } }
                    .disabled(!model.canApply)
                    .accessibilityIdentifier("news_filter.apply")
                if model.isBusy { ProgressView() }
                if let message = model.message {
                    Text(message).foregroundStyle(Color.onSurfaceSecondary)
                        .accessibilityIdentifier("news_filter.message")
                }
                if model.needsReload || (model.preference == nil && !model.isLoading) {
                    Button("Reload saved filter") { Task { await model.load() } }
                        .accessibilityIdentifier("news_filter.reload")
                }
            } footer: {
                Text("Preview uses this draft without saving it.")
            }

            if !model.previewItems.isEmpty {
                Section("Draft preview") {
                    ForEach(model.previewItems, id: \.news.id) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.news.title)
                            Text(newsFilterStatusText(item.filterStatus))
                                .font(.modalSupporting)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                    }
                }
            }
            Section {
                NavigationLink("All News") { NewsFilterNewsView(service: service, filtered: false) }
                NavigationLink("Filtered Out") { NewsFilterNewsView(service: service, filtered: true) }
            }
        }
        .font(.modalBody)
        .foregroundStyle(Color.onSurface)
        .tint(Color.brandPrimary)
        .scrollContentBackground(.hidden)
        .background(Color.surfacePrimary)
        .appNavigationTitle("News Filter")
        .task { if model.preference == nil { await model.load() } }
        .onChange(of: model.text) { _, _ in model.draftChanged() }
        .onChange(of: model.enabled) { _, _ in model.draftChanged() }
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
