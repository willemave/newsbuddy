//
//  ShareViewController.swift
//  ShareExtension
//
//  Created by Willem Ave on 12/21/25.
//

import UIKit
import UniformTypeIdentifiers

fileprivate enum LinkHandlingMode: String, CaseIterable {
    case addContent
    case addLinks
    case addFeed

    var title: String {
        switch self {
        case .addContent:
            return "Add content"
        case .addLinks:
            return "Add links"
        case .addFeed:
            return "Add feed"
        }
    }

    var description: String {
        switch self {
        case .addContent:
            return "Summarize the shared page in Newsbuddy."
        case .addLinks:
            return "Also crawl important links found on the page."
        case .addFeed:
            return "Subscribe to this site's feed in Newsbuddy."
        }
    }
}

final class ShareViewController: UIViewController {

    private var sharedURL: URL?
    private var linkHandlingMode: LinkHandlingMode = .addContent
    private var optionViews: [LinkHandlingMode: OptionRowView] = [:]

    private let contentStack = UIStackView()
    private let titleLabel = UILabel()
    private let optionsStack = UIStackView()
    private let knowledgeSaveToggleView = ToggleRowView(
        title: "Save to knowledge",
        description: "Download and summarize this item, then mark it read and save it to your knowledge library."
    )
    private let submitButton = UIButton(type: .system)

    override func viewDidLoad() {
        super.viewDidLoad()

        view.backgroundColor = .systemBackground

        if let accessGroup = SharedContainer.keychainAccessGroup {
            KeychainManager.shared.configure(accessGroup: accessGroup)
        }

        configureLayout()
        configureOptions()
        configureSubmitButton()

        extractSharedURL()
        updateSubmitState()
        updateSelectionUI()

        let sharedURLString = sharedURL?.absoluteString ?? "nil"
        print("🔗 [ShareExt] viewDidLoad sharedURL=\(sharedURLString)")
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()

        let targetSize = contentStack.systemLayoutSizeFitting(
            CGSize(width: view.bounds.width - 32, height: UIView.layoutFittingCompressedSize.height),
            withHorizontalFittingPriority: .required,
            verticalFittingPriority: .fittingSizeLevel
        )
        let safeHeight = view.safeAreaInsets.top + view.safeAreaInsets.bottom
        let targetHeight = targetSize.height + safeHeight + 16
        preferredContentSize = CGSize(width: view.bounds.width, height: targetHeight)
    }

    // MARK: - Layout

    private func configureLayout() {
        contentStack.axis = .vertical
        contentStack.spacing = 16
        contentStack.alignment = .fill
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        contentStack.setContentHuggingPriority(.required, for: .vertical)

        titleLabel.text = "How should Newsbuddy handle this link?"
        titleLabel.font = .preferredFont(forTextStyle: .headline)
        titleLabel.numberOfLines = 0

        optionsStack.axis = .vertical
        optionsStack.spacing = 12
        optionsStack.alignment = .fill
        optionsStack.setContentHuggingPriority(.required, for: .vertical)
        optionsStack.setContentCompressionResistancePriority(.required, for: .vertical)

        submitButton.heightAnchor.constraint(equalToConstant: 44).isActive = true

        contentStack.addArrangedSubview(titleLabel)
        contentStack.addArrangedSubview(optionsStack)
        contentStack.addArrangedSubview(knowledgeSaveToggleView)
        contentStack.addArrangedSubview(submitButton)

        view.addSubview(contentStack)

        NSLayoutConstraint.activate([
            contentStack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 16),
            contentStack.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 16),
            contentStack.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -16),
            contentStack.bottomAnchor.constraint(lessThanOrEqualTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -16),
        ])
    }

    private func configureOptions() {
        LinkHandlingMode.allCases.forEach { mode in
            let optionView = OptionRowView(title: mode.title, description: mode.description)
            optionView.addTarget(self, action: #selector(handleOptionTapped(_:)), for: .touchUpInside)
            optionsStack.addArrangedSubview(optionView)
            optionViews[mode] = optionView
        }
    }

    private func configureSubmitButton() {
        var configuration = UIButton.Configuration.filled()
        configuration.title = "Submit"
        configuration.cornerStyle = .medium
        submitButton.configuration = configuration
        submitButton.addTarget(self, action: #selector(handleSubmitTapped), for: .touchUpInside)
    }

    private func updateSelectionUI() {
        optionViews.forEach { mode, view in
            view.isSelected = (mode == linkHandlingMode)
        }
        updateKnowledgeSaveToggleAvailability()
    }

    private func updateSubmitState() {
        submitButton.isEnabled = sharedURL != nil
    }

    @objc private func handleOptionTapped(_ sender: OptionRowView) {
        guard let match = optionViews.first(where: { $0.value == sender })?.key else { return }
        linkHandlingMode = match
        updateSelectionUI()
    }

    @objc private func handleSubmitTapped() {
        guard let url = sharedURL else {
            showError("No URL found")
            return
        }

        submitButton.isEnabled = false

        Task {
            do {
                try await submitURL(url)
                await MainActor.run {
                    self.extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
                }
            } catch {
                await MainActor.run {
                    self.updateSubmitState()
                    self.showError(error.localizedDescription)
                }
            }
        }
    }

    // MARK: - URL Extraction

    private func extractSharedURL() {
        guard let extensionItems = extensionContext?.inputItems as? [NSExtensionItem] else {
            return
        }

        for item in extensionItems {
            guard let attachments = item.attachments else { continue }

            for attachment in attachments {
                if attachment.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                    attachment.loadItem(forTypeIdentifier: UTType.url.identifier, options: nil) { [weak self] item, _ in
                        if let url = item as? URL {
                            self?.updateSharedURL(url)
                            return
                        }
                        if let text = item as? String, let url = URL(string: text), url.scheme != nil {
                            self?.updateSharedURL(url)
                        }
                    }
                }

                if attachment.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                    attachment.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { [weak self] item, _ in
                        if let text = item as? String {
                            let urls = ShareURLRouting.extractURLs(from: text)
                            if let firstURL = urls.first {
                                self?.updateSharedURL(firstURL)
                                for url in urls.dropFirst() {
                                    self?.updateSharedURL(url)
                                }
                            } else if let url = URL(string: text), url.scheme != nil {
                                self?.updateSharedURL(url)
                            }
                        }
                    }
                }
            }
        }
    }

    private func updateSharedURL(_ candidate: URL) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let best = ShareURLRouting.preferredURL(current: self.sharedURL, candidate: candidate)
            guard best != self.sharedURL else { return }
            self.sharedURL = best
            self.updateSubmitState()
            let handlerKind = ShareURLRouting.handler(for: best).kind.rawValue
            print("🔗 [ShareExt] extracted URL=\(best.absoluteString) handler=\(handlerKind)")
        }
    }

    private func updateKnowledgeSaveToggleAvailability() {
        let isAvailable = linkHandlingMode != .addFeed
        if !isAvailable && knowledgeSaveToggleView.isOn {
            knowledgeSaveToggleView.isOn = false
        }
        knowledgeSaveToggleView.isEnabled = isAvailable
    }

    // MARK: - API Submission

    private func submitURL(_ url: URL) async throws {
        let handler = ShareURLRouting.handler(for: url)
        var body: [String: Any] = [
            "url": url.absoluteString,
            "crawl_links": linkHandlingMode == .addLinks,
            "share_and_chat": false,
            "save_to_knowledge_and_mark_read": knowledgeSaveToggleView.isOn,
            "subscribe_to_feed": linkHandlingMode == .addFeed,
        ]
        if let platform = handler.platform {
            body["platform"] = platform
        }
        let requestBody = try JSONSerialization.data(withJSONObject: body)

        do {
            try await APIClient.shared.requestVoid(
                "/api/content/submit",
                method: "POST",
                body: requestBody
            )
        } catch let error as APIError {
            switch error {
            case .unauthorized:
                throw ShareError.notAuthenticated
            case .invalidURL:
                throw ShareError.invalidURL
            case .networkError(let underlying):
                throw ShareError.networkError(underlying.localizedDescription)
            case .httpError(let statusCode):
                throw ShareError.serverError("Request failed with status \(statusCode)")
            case .decodingError(let underlying):
                throw ShareError.serverError(underlying.localizedDescription)
            case .noData, .unknown:
                throw ShareError.invalidResponse
            }
        } catch {
            throw ShareError.serverError(error.localizedDescription)
        }
    }

    // MARK: - Error Handling

    private func showError(_ message: String) {
        let alert = UIAlertController(
            title: "Error",
            message: message,
            preferredStyle: .alert
        )
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in
            self.extensionContext?.cancelRequest(withError: ShareError.userCancelled)
        })
        present(alert, animated: true)
    }
}

// MARK: - UI Components

private final class OptionRowView: UIControl {

    private let titleLabel = UILabel()
    private let descriptionLabel = UILabel()
    private let indicatorView = UIImageView()

    init(title: String, description: String) {
        super.init(frame: .zero)

        layer.cornerRadius = 12
        layer.borderWidth = 1
        layer.borderColor = UIColor.separator.cgColor
        backgroundColor = .secondarySystemBackground
        isUserInteractionEnabled = true

        titleLabel.text = title
        titleLabel.font = UIFont.preferredFont(forTextStyle: .body)
        titleLabel.textColor = .label

        descriptionLabel.text = description
        descriptionLabel.font = UIFont.preferredFont(forTextStyle: .footnote)
        descriptionLabel.textColor = .secondaryLabel
        descriptionLabel.numberOfLines = 0

        indicatorView.tintColor = .systemBlue
        indicatorView.setContentHuggingPriority(.required, for: .horizontal)
        indicatorView.setContentCompressionResistancePriority(.required, for: .horizontal)

        let labelsStack = UIStackView(arrangedSubviews: [titleLabel, descriptionLabel])
        labelsStack.axis = .vertical
        labelsStack.spacing = 4
        labelsStack.alignment = .fill

        let rowStack = UIStackView(arrangedSubviews: [indicatorView, labelsStack])
        rowStack.axis = .horizontal
        rowStack.alignment = .center
        rowStack.spacing = 12
        rowStack.translatesAutoresizingMaskIntoConstraints = false
        rowStack.isUserInteractionEnabled = false
        addSubview(rowStack)

        NSLayoutConstraint.activate([
            rowStack.topAnchor.constraint(equalTo: topAnchor, constant: 12),
            rowStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            rowStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            rowStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -12),
            indicatorView.widthAnchor.constraint(equalToConstant: 22),
            indicatorView.heightAnchor.constraint(equalToConstant: 22),
        ])

        updateSelectionState()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override var isSelected: Bool {
        didSet {
            updateSelectionState()
        }
    }

    override var isHighlighted: Bool {
        didSet {
            updateSelectionState()
        }
    }

    private func updateSelectionState() {
        if isSelected {
            indicatorView.image = UIImage(systemName: "checkmark.circle.fill")
            layer.borderColor = UIColor.systemBlue.cgColor
        } else {
            indicatorView.image = UIImage(systemName: "circle")
            layer.borderColor = UIColor.separator.cgColor
        }

        if isHighlighted {
            backgroundColor = UIColor.systemGray6
        } else {
            backgroundColor = isSelected ? UIColor.systemBackground : UIColor.secondarySystemBackground
        }
    }
}

private final class ToggleRowView: UIControl {

    private let titleLabel = UILabel()
    private let descriptionLabel = UILabel()
    private let toggleSwitch = UISwitch()

    var isOn: Bool {
        get { toggleSwitch.isOn }
        set { toggleSwitch.setOn(newValue, animated: false) }
    }

    override var isEnabled: Bool {
        didSet {
            toggleSwitch.isEnabled = isEnabled
            alpha = isEnabled ? 1.0 : 0.5
        }
    }

    init(title: String, description: String) {
        super.init(frame: .zero)

        layer.cornerRadius = 12
        layer.borderWidth = 1
        layer.borderColor = UIColor.separator.cgColor
        backgroundColor = .secondarySystemBackground
        isUserInteractionEnabled = true

        titleLabel.text = title
        titleLabel.font = UIFont.preferredFont(forTextStyle: .body)
        titleLabel.textColor = .label

        descriptionLabel.text = description
        descriptionLabel.font = UIFont.preferredFont(forTextStyle: .footnote)
        descriptionLabel.textColor = .secondaryLabel
        descriptionLabel.numberOfLines = 0

        toggleSwitch.setContentHuggingPriority(.required, for: .horizontal)
        toggleSwitch.setContentCompressionResistancePriority(.required, for: .horizontal)

        let labelsStack = UIStackView(arrangedSubviews: [titleLabel, descriptionLabel])
        labelsStack.axis = .vertical
        labelsStack.spacing = 4
        labelsStack.alignment = .fill

        let rowStack = UIStackView(arrangedSubviews: [labelsStack, toggleSwitch])
        rowStack.axis = .horizontal
        rowStack.alignment = .center
        rowStack.spacing = 12
        rowStack.translatesAutoresizingMaskIntoConstraints = false

        addSubview(rowStack)

        NSLayoutConstraint.activate([
            rowStack.topAnchor.constraint(equalTo: topAnchor, constant: 12),
            rowStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            rowStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            rowStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -12),
        ])

        addTarget(self, action: #selector(handleControlTapped), for: .touchUpInside)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    @objc private func handleControlTapped() {
        guard isEnabled else { return }
        isOn.toggle()
    }
}

// MARK: - Errors

enum ShareError: LocalizedError {
    case notAuthenticated
    case invalidURL
    case invalidResponse
    case networkError(String)
    case serverError(String)
    case userCancelled

    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "Session expired. Open Newsbuddy and sign in again."
        case .invalidURL:
            return "Invalid URL"
        case .invalidResponse:
            return "Invalid server response"
        case .networkError(let message):
            return "Network error: \(message)"
        case .serverError(let message):
            return message
        case .userCancelled:
            return "Cancelled"
        }
    }
}
