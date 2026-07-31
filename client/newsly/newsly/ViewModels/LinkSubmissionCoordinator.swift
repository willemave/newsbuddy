//
//  LinkSubmissionCoordinator.swift
//  newsly
//

import Foundation

enum LinkReadLaterState: Equatable {
    case idle
    case adding
    case added
    case failed
}

@MainActor
protocol ToastPresenting: AnyObject {
    func show(_ message: String, type: ToastType, duration: TimeInterval)
    func showError(_ message: String)
    func showSuccess(_ message: String)
}

extension ToastService: ToastPresenting {}

@MainActor
final class LinkSubmissionCoordinator {
    typealias SubmitHandler = (URL, String?) async throws -> SubmitContentResponse

    var onStateWillChange: (() -> Void)?

    private let submitLinkToLongFormHandler: SubmitHandler
    private let toastPresenter: any ToastPresenting
    private var linkStates: [String: LinkReadLaterState] = [:]

    init(
        submitLinkToLongFormHandler: @escaping SubmitHandler,
        toastPresenter: any ToastPresenting
    ) {
        self.submitLinkToLongFormHandler = submitLinkToLongFormHandler
        self.toastPresenter = toastPresenter
    }

    func reset() {
        guard !linkStates.isEmpty else { return }
        onStateWillChange?()
        linkStates = [:]
    }

    func state(for linkID: String) -> LinkReadLaterState {
        linkStates[linkID] ?? .idle
    }

    func addRelevantLinkToReadLater(_ link: RelevantLink) async {
        guard let url = URL(string: link.url) else {
            toastPresenter.showError("Invalid link URL")
            return
        }

        await addLinkToReadLater(
            id: link.id,
            url: url,
            title: link.title,
            alreadyExistsMessage: "Already in Read Later",
            successMessage: "Added to Read Later",
            errorPrefix: "Failed to add to Read Later"
        )
    }

    private func addLinkToReadLater(
        id linkID: String,
        url: URL,
        title: String?,
        alreadyExistsMessage: String,
        successMessage: String,
        errorPrefix: String
    ) async {
        let state = linkStates[linkID] ?? .idle
        guard state != .adding && state != .added else {
            return
        }

        setState(.adding, for: linkID)

        do {
            let response = try await submitLinkToLongFormHandler(url, title)
            setState(.added, for: linkID)
            if response.alreadyExists {
                toastPresenter.show(alreadyExistsMessage, type: .info, duration: 3.0)
            } else {
                toastPresenter.showSuccess(successMessage)
            }
        } catch {
            setState(.failed, for: linkID)
            toastPresenter.showError("\(errorPrefix): \(error.localizedDescription)")
        }
    }

    private func setState(_ state: LinkReadLaterState, for linkID: String) {
        guard linkStates[linkID] != state else { return }
        onStateWillChange?()
        linkStates[linkID] = state
    }
}
