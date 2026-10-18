import SwiftUI

struct OnboardingNewsFilterEditor: View {
    let viewModel: OnboardingViewModel

    private var text: Binding<String> {
        Binding(get: { viewModel.newsFilterText ?? "" }, set: {
            viewModel.updateNewsFilter(text: $0, enabled: viewModel.newsFilterEnabled)
        })
    }
    private var enabled: Binding<Bool> {
        Binding(get: { viewModel.newsFilterEnabled }, set: {
            viewModel.updateNewsFilter(text: viewModel.newsFilterText ?? "", enabled: $0)
        })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your News filter").font(.modalTitle)
            Text(viewModel.newsFilterText == nil
                 ? "Describe which News you want in upcoming Briefings. Articles and Podcasts are unaffected."
                 : "We kept your interests below. Edit them to choose which News enters upcoming Briefings. Articles and Podcasts are unaffected.")
                .font(.modalSupporting)
                .foregroundStyle(Color.onSurfaceSecondary)
            Toggle("Filter News in Briefing", isOn: enabled)
                .font(.modalBody)
                .accessibilityIdentifier("onboarding.news_filter.enabled")
            TextEditor(text: text)
                .font(.modalBody)
                .foregroundStyle(Color.onSurface)
                .scrollContentBackground(.hidden)
                .frame(minHeight: 160)
                .padding(8)
                .background(Color.surfacePrimary)
                .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control))
                .accessibilityLabel("News filter instructions")
                .accessibilityIdentifier("onboarding.news_filter.text")
            Text("\((viewModel.newsFilterText ?? "").unicodeScalars.count) / 8,000 characters")
                .font(.modalCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
            if let message = NewsFilterValidation.message(
                text: viewModel.newsFilterText ?? "", enabled: viewModel.newsFilterEnabled
            ) {
                Text(message)
                    .font(.modalSupporting)
                    .foregroundStyle(Color.statusDestructive)
            }
            Text("You can change this in Settings and read all stories in All News.")
                .font(.modalSupporting)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .tint(Color.brandPrimary)
        .padding(.vertical, 16)
    }
}
