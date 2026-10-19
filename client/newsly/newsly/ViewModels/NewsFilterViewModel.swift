import Foundation
import Observation

@MainActor @Observable
final class NewsFilterViewModel {
    var text = ""
    var enabled = false
    private(set) var preference: APINewsFilterPreference?
    private(set) var isLoading = false
    private(set) var isSaving = false
    private(set) var isPreviewing = false
    private(set) var message: String?
    private(set) var needsReload = false
    private(set) var previewItems: [APINewsFilterNewsItem] = []
    @ObservationIgnored private let service: any NewsFilterServicing

    init(service: any NewsFilterServicing) { self.service = service }

    var validationMessage: String? {
        NewsFilterValidation.message(text: text, enabled: enabled)
    }

    var isBusy: Bool { isLoading || isSaving || isPreviewing }
    var hasChanges: Bool {
        guard let preference else { return false }
        return text != preference.text || enabled != preference.enabled
    }
    var canApply: Bool { hasChanges && validationMessage == nil && !isBusy && !needsReload }
    var canPreview: Bool {
        preference != nil && !isBusy
            && NewsFilterValidation.message(text: text, enabled: true) == nil
    }

    func load() async {
        guard !isBusy else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let result = try await service.load()
            guard !Task.isCancelled else { return }
            preference = result
            text = result.text
            enabled = result.enabled
            message = nil
            needsReload = false
            previewItems = []
        } catch where ClientFailure.classify(error) == .cancelled { }
        catch { message = error.localizedDescription }
    }

    func apply() async {
        guard canApply, let preference else { return }
        isSaving = true
        message = nil
        defer { isSaving = false }
        do {
            let result = try await service.save(revision: preference.revision, text: text, enabled: enabled)
            self.preference = result
            text = result.text
            enabled = result.enabled
            previewItems = []
            message = "Applied to upcoming News additions. Published Briefings stay available."
        } catch {
            switch ClientFailure.classify(error) {
            case .server(409, _), .http(409, _):
                needsReload = true
                message = "Your filter changed elsewhere. Reload the saved filter before applying again. Your draft is still shown."
            case .cancelled: break
            default: message = error.localizedDescription
            }
        }
    }

    func draftChanged() {
        previewItems = []
        if !needsReload { message = nil }
    }

    func preview() async {
        guard canPreview else { return }
        isPreviewing = true
        message = nil
        previewItems = []
        defer { isPreviewing = false }
        do {
            let result = try await service.preview(text: text)
            guard !Task.isCancelled else { return }
            previewItems = result.items
            if result.items.isEmpty { message = "No recent News is ready to preview." }
        } catch where ClientFailure.classify(error) == .cancelled { }
        catch { message = error.localizedDescription }
    }
}
