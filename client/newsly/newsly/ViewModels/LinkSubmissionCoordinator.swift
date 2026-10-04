//
//  LinkSubmissionCoordinator.swift
//  newsly
//

import Foundation
import Observation

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
@Observable
final class LinkSubmissionCoordinator {
    @ObservationIgnored
    private let contentService: any ContentDetailServicing
    @ObservationIgnored
    private let toastPresenter: any ToastPresenting
    private var linkStates: [String: LinkReadLaterState] = [:]

    init(
        contentService: any ContentDetailServicing,
        toastPresenter: any ToastPresenting
    ) {
        self.contentService = contentService
        self.toastPresenter = toastPresenter
    }

    func reset() {
        guard !linkStates.isEmpty else { return }
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

        let linkID = link.id
        let state = linkStates[linkID] ?? .idle
        guard state != .adding && state != .added else {
            return
        }

        setState(.adding, for: linkID)

        do {
            let response = try await contentService.submitContent(
                url: url,
                title: link.title,
                platform: nil
            )
            setState(.added, for: linkID)
            if response.alreadyExists {
                toastPresenter.show("Already in Read Later", type: .info, duration: 3.0)
            } else {
                toastPresenter.showSuccess("Added to Read Later")
            }
        } catch {
            setState(.failed, for: linkID)
            toastPresenter.showError("Failed to add to Read Later: \(error.localizedDescription)")
        }
    }

    private func setState(_ state: LinkReadLaterState, for linkID: String) {
        guard linkStates[linkID] != state else { return }
        linkStates[linkID] = state
    }
}
