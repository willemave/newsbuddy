//
//  ShareViewController.swift
//  ShareExtension
//
//  Created by Willem Ave on 12/21/25.
//

import UIKit
import UniformTypeIdentifiers

fileprivate enum ShareOutcomeMode: String, CaseIterable {
    case addToBriefing = "add_to_briefing"
    case addToKnowledge = "add_to_knowledge"
    case createDeck = "create_deck"
    case chat

    var title: String {
        switch self {
        case .addToBriefing:
            return "Add to Briefing"
        case .addToKnowledge:
            return "Add to Knowledge"
        case .createDeck:
            return "Create Deck"
        case .chat:
            return "Chat"
        }
    }

    var description: String {
        switch self {
        case .addToBriefing:
            return "Add this item, or subscribe to its source, for future Briefings."
        case .addToKnowledge:
            return "Save this item to Knowledge without adding it to Briefing."
        case .createDeck:
            return "Save this source and turn it into a Learning Deck."
        case .chat:
            return "Save to Knowledge and start a chat after processing."
        }
    }
}

final class ShareViewController: UIViewController, UITextViewDelegate {

    private var sharedURL: URL?
    private var shareOutcomeMode: ShareOutcomeMode = .addToBriefing
    private var optionViews: [ShareOutcomeMode: OptionRowView] = [:]
    private var submissionState = ShareSubmissionPresentationState()

    private let scrollView = UIScrollView()
    private let contentStack = UIStackView()
    private let titleLabel = UILabel()
    private let urlStatusLabel = UILabel()
    private let optionsStack = UIStackView()
    private let deckInstructionsStack = UIStackView()
    private let deckInstructionsLabel = UILabel()
    private let deckInstructionsHelpLabel = UILabel()
    private let deckInstructionsTextView = UITextView()
    private let chatPromptStack = UIStackView()
    private let chatPromptLabel = UILabel()
    private let chatPromptTextView = UITextView()
    private let submitButton = UIButton(type: .system)
    private let cancelButton = UIButton(type: .system)
    private let keyboardSubmitButton = UIBarButtonItem(title: "Start chat", style: .plain, target: nil, action: nil)
    private lazy var keyboardAccessoryView: UIToolbar = {
        let toolbar = UIToolbar()
        toolbar.sizeToFit()
        keyboardSubmitButton.target = self
        keyboardSubmitButton.action = #selector(handleSubmitTapped)
        toolbar.items = [
            UIBarButtonItem(systemItem: .flexibleSpace),
            keyboardSubmitButton,
        ]
        return toolbar
    }()

    private var chatInitialMessage: String {
        chatPromptTextView.text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var deckInstructions: String {
        deckInstructionsTextView.text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var hasRequiredSubmissionInput: Bool {
        shareOutcomeMode != .chat || !chatInitialMessage.isEmpty
    }

    override func viewDidLoad() {
        super.viewDidLoad()

        view.backgroundColor = .systemBackground

        KeychainManager.shared.configure(accessGroup: SharedContainer.keychainAccessGroup)

        configureLayout()
        configureOptions()
        configureDeckInstructions()
        configureChatPrompt()
        configureSubmitButton()
        registerKeyboardObservers()

        extractSharedURL()
        updateSubmitState()
        updateSelectionUI()

        let sharedURLString = sharedURL?.absoluteString ?? "nil"
        print("🔗 [ShareExt] viewDidLoad sharedURL=\(sharedURLString)")
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
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

        titleLabel.text = "What would you like to do with this?"
        titleLabel.font = ShareExtensionStyle.titleFont(textStyle: .headline)
        titleLabel.adjustsFontForContentSizeCategory = true
        titleLabel.numberOfLines = 0
        titleLabel.accessibilityIdentifier = "share.title"

        urlStatusLabel.text = "Reading the shared link…"
        urlStatusLabel.font = ShareExtensionStyle.font(textStyle: .footnote)
        urlStatusLabel.adjustsFontForContentSizeCategory = true
        urlStatusLabel.textColor = .secondaryLabel
        urlStatusLabel.numberOfLines = 0
        urlStatusLabel.accessibilityIdentifier = "share.url_status"

        optionsStack.axis = .vertical
        optionsStack.spacing = 12
        optionsStack.alignment = .fill
        optionsStack.setContentHuggingPriority(.required, for: .vertical)
        optionsStack.setContentCompressionResistancePriority(.required, for: .vertical)

        submitButton.heightAnchor.constraint(equalToConstant: 44).isActive = true

        contentStack.addArrangedSubview(titleLabel)
        contentStack.addArrangedSubview(urlStatusLabel)
        contentStack.addArrangedSubview(optionsStack)
        contentStack.addArrangedSubview(deckInstructionsStack)
        contentStack.addArrangedSubview(chatPromptStack)
        contentStack.addArrangedSubview(submitButton)
        contentStack.addArrangedSubview(cancelButton)

        scrollView.alwaysBounceVertical = false
        scrollView.keyboardDismissMode = .interactive
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(scrollView)
        scrollView.addSubview(contentStack)

        NSLayoutConstraint.activate([
            scrollView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            scrollView.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor),

            contentStack.topAnchor.constraint(equalTo: scrollView.contentLayoutGuide.topAnchor, constant: 16),
            contentStack.leadingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.leadingAnchor, constant: 16),
            contentStack.trailingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.trailingAnchor, constant: -16),
            contentStack.bottomAnchor.constraint(equalTo: scrollView.contentLayoutGuide.bottomAnchor, constant: -16),
            contentStack.widthAnchor.constraint(equalTo: scrollView.frameLayoutGuide.widthAnchor, constant: -32),
        ])
    }

    private func configureOptions() {
        ShareOutcomeMode.allCases.forEach { mode in
            let optionView = OptionRowView(
                title: mode.title,
                description: mode.description,
                accessibilityIdentifier: "share.action.\(mode.rawValue)"
            )
            optionView.addTarget(self, action: #selector(handleOptionTapped(_:)), for: .touchUpInside)
            optionsStack.addArrangedSubview(optionView)
            optionViews[mode] = optionView
        }
    }

    private func configureDeckInstructions() {
        deckInstructionsStack.axis = .vertical
        deckInstructionsStack.spacing = 8
        deckInstructionsStack.alignment = .fill
        deckInstructionsStack.isHidden = true

        deckInstructionsLabel.text = "Further instructions (optional)"
        deckInstructionsLabel.font = ShareExtensionStyle.font(textStyle: .subheadline, weight: .medium)
        deckInstructionsLabel.adjustsFontForContentSizeCategory = true
        deckInstructionsLabel.textColor = .label

        deckInstructionsHelpLabel.text = "Tell the deck builder what else to capture, compare, or investigate."
        deckInstructionsHelpLabel.font = ShareExtensionStyle.font(textStyle: .footnote)
        deckInstructionsHelpLabel.adjustsFontForContentSizeCategory = true
        deckInstructionsHelpLabel.textColor = .secondaryLabel
        deckInstructionsHelpLabel.numberOfLines = 0

        deckInstructionsTextView.delegate = self
        deckInstructionsTextView.font = ShareExtensionStyle.font(textStyle: .body)
        deckInstructionsTextView.adjustsFontForContentSizeCategory = true
        deckInstructionsTextView.backgroundColor = .secondarySystemBackground
        deckInstructionsTextView.layer.cornerRadius = 10
        deckInstructionsTextView.layer.borderWidth = 1
        deckInstructionsTextView.layer.borderColor = UIColor.separator.cgColor
        deckInstructionsTextView.textContainerInset = UIEdgeInsets(top: 10, left: 8, bottom: 10, right: 8)
        deckInstructionsTextView.heightAnchor.constraint(equalToConstant: 104).isActive = true
        deckInstructionsTextView.inputAccessoryView = keyboardAccessoryView
        deckInstructionsTextView.accessibilityLabel = "Further deck instructions"
        deckInstructionsTextView.accessibilityHint = "Optional instructions for what the deck builder should capture or investigate"
        deckInstructionsTextView.accessibilityIdentifier = "share.deck.instructions"

        deckInstructionsStack.addArrangedSubview(deckInstructionsLabel)
        deckInstructionsStack.addArrangedSubview(deckInstructionsHelpLabel)
        deckInstructionsStack.addArrangedSubview(deckInstructionsTextView)
    }

    private func configureChatPrompt() {
        chatPromptStack.axis = .vertical
        chatPromptStack.spacing = 8
        chatPromptStack.alignment = .fill
        chatPromptStack.isHidden = true

        chatPromptLabel.text = "First message"
        chatPromptLabel.font = ShareExtensionStyle.font(textStyle: .subheadline, weight: .medium)
        chatPromptLabel.adjustsFontForContentSizeCategory = true
        chatPromptLabel.textColor = .secondaryLabel

        chatPromptTextView.delegate = self
        chatPromptTextView.font = ShareExtensionStyle.font(textStyle: .body)
        chatPromptTextView.adjustsFontForContentSizeCategory = true
        chatPromptTextView.backgroundColor = .secondarySystemBackground
        chatPromptTextView.layer.cornerRadius = 10
        chatPromptTextView.layer.borderWidth = 1
        chatPromptTextView.layer.borderColor = UIColor.separator.cgColor
        chatPromptTextView.textContainerInset = UIEdgeInsets(top: 10, left: 8, bottom: 10, right: 8)
        chatPromptTextView.heightAnchor.constraint(equalToConstant: 104).isActive = true
        chatPromptTextView.inputAccessoryView = keyboardAccessoryView
        chatPromptTextView.accessibilityLabel = "First chat message"
        chatPromptTextView.accessibilityHint = "Required before starting the chat"
        chatPromptTextView.accessibilityIdentifier = "share.chat.prompt"

        chatPromptStack.addArrangedSubview(chatPromptLabel)
        chatPromptStack.addArrangedSubview(chatPromptTextView)
    }

    private func configureSubmitButton() {
        var configuration = UIButton.Configuration.filled()
        configuration.title = "Submit"
        configuration.cornerStyle = .medium
        configuration.baseBackgroundColor = ShareExtensionStyle.brandAccent
        configuration.baseForegroundColor = .white
        submitButton.configuration = configuration
        submitButton.addTarget(self, action: #selector(handleSubmitTapped), for: .touchUpInside)
        submitButton.accessibilityIdentifier = "share.submit"

        var cancelConfiguration = UIButton.Configuration.plain()
        cancelConfiguration.title = "Cancel"
        cancelButton.configuration = cancelConfiguration
        cancelButton.addTarget(self, action: #selector(handleCancelTapped), for: .touchUpInside)
        cancelButton.accessibilityIdentifier = "share.cancel"
    }

    private func updateSelectionUI() {
        optionViews.forEach { mode, view in
            view.isSelected = (mode == shareOutcomeMode)
        }
        deckInstructionsStack.isHidden = shareOutcomeMode != .createDeck
        chatPromptStack.isHidden = shareOutcomeMode != .chat
        if shareOutcomeMode != .createDeck && deckInstructionsTextView.isFirstResponder {
            deckInstructionsTextView.resignFirstResponder()
        }
        if shareOutcomeMode != .chat && chatPromptTextView.isFirstResponder {
            chatPromptTextView.resignFirstResponder()
        }
        updateSubmitButtonTitle()
        updateSubmitState()
    }

    private func updateSubmitState() {
        let canEditSubmission = submissionState.canBeginSubmission
        optionViews.values.forEach { $0.isEnabled = canEditSubmission }
        deckInstructionsTextView.isEditable = canEditSubmission
        deckInstructionsTextView.isSelectable = canEditSubmission
        chatPromptTextView.isEditable = canEditSubmission
        chatPromptTextView.isSelectable = canEditSubmission
        let isSubmittable = sharedURL != nil
            && hasRequiredSubmissionInput
            && canEditSubmission
        submitButton.isEnabled = isSubmittable
        keyboardSubmitButton.isEnabled = isSubmittable
        cancelButton.isEnabled = !submissionState.isSubmitting
    }

    @objc private func handleOptionTapped(_ sender: OptionRowView) {
        guard submissionState.canBeginSubmission else { return }
        guard let match = optionViews.first(where: { $0.value == sender })?.key else { return }
        shareOutcomeMode = match
        updateSelectionUI()
        if match == .chat {
            focusChatPrompt()
        }
    }

    func textViewDidChange(_ textView: UITextView) {
        updateSubmitState()
    }

    func textView(
        _ textView: UITextView,
        shouldChangeTextIn range: NSRange,
        replacementText text: String
    ) -> Bool {
        guard textView === deckInstructionsTextView else { return true }
        guard let currentRange = Range(range, in: textView.text) else { return false }
        return textView.text.replacingCharacters(in: currentRange, with: text).count <= 4000
    }

    @objc private func handleSubmitTapped() {
        guard submissionState.canBeginSubmission else { return }
        guard hasRequiredSubmissionInput else {
            focusChatPrompt()
            return
        }
        guard submissionState.begin(hasValidURL: sharedURL != nil), let url = sharedURL else {
            showError(ShareError.invalidURL)
            return
        }

        deckInstructionsTextView.resignFirstResponder()
        chatPromptTextView.resignFirstResponder()
        updateSubmitButtonTitle()
        updateSubmitState()

        Task {
            do {
                try await submitURL(url)
                await MainActor.run {
                    self.submissionState.succeed()
                    self.extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
                }
            } catch {
                await MainActor.run {
                    self.submissionState.fail(self.presentationFailure(for: error))
                    self.updateSubmitButtonTitle()
                    self.updateSubmitState()
                    self.showError(error)
                }
            }
        }
    }

    @objc private func handleCancelTapped() {
        guard !submissionState.isSubmitting else { return }
        extensionContext?.cancelRequest(withError: ShareError.userCancelled)
    }

    // MARK: - URL Extraction

    private func extractSharedURL() {
        guard let extensionItems = extensionContext?.inputItems as? [NSExtensionItem] else {
            urlStatusLabel.text = "No web link was found. Share a page or URL and try again."
            return
        }

        let loadingGroup = DispatchGroup()
        var didRequestValue = false

        for item in extensionItems {
            guard let attachments = item.attachments else { continue }

            for attachment in attachments {
                if attachment.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                    didRequestValue = true
                    loadingGroup.enter()
                    attachment.loadItem(forTypeIdentifier: UTType.url.identifier, options: nil) { [weak self] item, _ in
                        defer { loadingGroup.leave() }
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
                    didRequestValue = true
                    loadingGroup.enter()
                    attachment.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { [weak self] item, _ in
                        defer { loadingGroup.leave() }
                        if let text = item as? String {
                            let urls = ShareURLRouting.extractURLs(from: text)
                            for url in urls {
                                self?.updateSharedURL(url)
                            }
                            if urls.isEmpty, let url = URL(string: text), url.scheme != nil {
                                self?.updateSharedURL(url)
                            }
                        }
                    }
                }
            }
        }

        guard didRequestValue else {
            urlStatusLabel.text = "No web link was found. Share a page or URL and try again."
            return
        }

        loadingGroup.notify(queue: .main) { [weak self] in
            guard let self, self.sharedURL == nil else { return }
            self.urlStatusLabel.text = "No web link was found. Share a page or URL and try again."
            self.updateSubmitState()
        }
    }

    private func updateSharedURL(_ candidate: URL) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            guard ShareURLRouting.isWebURL(candidate) else { return }
            let best = ShareURLRouting.preferredURL(current: self.sharedURL, candidate: candidate)
            guard best != self.sharedURL else { return }
            self.sharedURL = best
            self.urlStatusLabel.text = "Ready: \(best.host ?? best.absoluteString)"
            self.updateSubmitState()
            let handlerKind = ShareURLRouting.handler(for: best).kind.rawValue
            print("🔗 [ShareExt] extracted URL=\(best.absoluteString) handler=\(handlerKind)")
        }
    }

    private func updateSubmitButtonTitle() {
        var configuration = submitButton.configuration ?? UIButton.Configuration.filled()
        let title: String
        if submissionState.isSubmitting {
            switch shareOutcomeMode {
            case .addToBriefing:
                title = "Adding to Briefing…"
            case .addToKnowledge:
                title = "Saving to Knowledge…"
            case .createDeck:
                title = "Creating deck…"
            case .chat:
                title = "Starting chat…"
            }
        } else {
            switch shareOutcomeMode {
            case .addToBriefing:
                title = "Add to Briefing"
            case .addToKnowledge:
                title = "Add to Knowledge"
            case .createDeck:
                title = "Create deck"
            case .chat:
                title = "Start chat"
            }
        }
        configuration.title = title
        submitButton.configuration = configuration
        keyboardSubmitButton.title = title
    }

    // MARK: - Keyboard

    private func registerKeyboardObservers() {
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleKeyboardFrameWillChange(_:)),
            name: UIResponder.keyboardWillChangeFrameNotification,
            object: nil
        )
    }

    private func focusChatPrompt() {
        view.setNeedsLayout()
        view.layoutIfNeeded()
        chatPromptTextView.becomeFirstResponder()
        scrollChatPromptIntoView(animated: true)
    }

    @objc private func handleKeyboardFrameWillChange(_ notification: Notification) {
        guard shareOutcomeMode == .chat || shareOutcomeMode == .createDeck else { return }

        let duration = notification.userInfo?[UIResponder.keyboardAnimationDurationUserInfoKey] as? TimeInterval
            ?? 0.25
        UIView.animate(
            withDuration: duration,
            delay: 0,
            options: [.beginFromCurrentState, .curveEaseInOut]
        ) {
            self.view.layoutIfNeeded()
            self.scrollActivePromptIntoView(animated: false)
        }
    }

    private func scrollChatPromptIntoView(animated: Bool) {
        guard shareOutcomeMode == .chat, !chatPromptStack.isHidden else { return }

        scrollPromptIntoView(chatPromptStack, animated: animated)
    }

    private func scrollActivePromptIntoView(animated: Bool) {
        switch shareOutcomeMode {
        case .createDeck where !deckInstructionsStack.isHidden:
            scrollPromptIntoView(deckInstructionsStack, animated: animated)
        case .chat where !chatPromptStack.isHidden:
            scrollPromptIntoView(chatPromptStack, animated: animated)
        default:
            break
        }
    }

    private func scrollPromptIntoView(_ promptStack: UIStackView, animated: Bool) {
        view.layoutIfNeeded()
        let promptFrame = promptStack.convert(promptStack.bounds, to: scrollView)
        let submitFrame = submitButton.convert(submitButton.bounds, to: scrollView)
        scrollView.scrollRectToVisible(promptFrame.union(submitFrame).insetBy(dx: 0, dy: -16), animated: animated)
    }

    // MARK: - API Submission

    private func submitURL(_ url: URL) async throws {
        let payload = ShareActionRequest(
            url: url.absoluteString,
            mode: shareActionMode(),
            chatInitialMessage: shareOutcomeMode == .chat ? chatInitialMessage : nil,
            interestsPrompt: shareOutcomeMode == .createDeck && !deckInstructions.isEmpty
                ? deckInstructions
                : nil
        )
        let requestBody = try JSONEncoder().encode(payload)

        do {
            try await ShareExtensionTransport.shared.requestVoid(
                "/api/share-actions",
                method: .post,
                body: requestBody
            )
        } catch let error as ShareExtensionTransportError {
            switch error {
            case .notAuthenticated:
                throw ShareError.notAuthenticated
            case .invalidURL:
                throw ShareError.invalidURL
            case .network(let underlying):
                throw ShareError.networkError(underlying.localizedDescription)
            case .server(let statusCode, let detail):
                if let message = detail?.trimmingCharacters(in: .whitespacesAndNewlines), !message.isEmpty {
                    throw ShareError.serverError(message)
                }
                throw ShareError.serverError("Request failed with status \(statusCode)")
            case .invalidResponse:
                throw ShareError.invalidResponse
            }
        } catch {
            throw ShareError.serverError(error.localizedDescription)
        }
    }

    private func shareActionMode() -> String {
        switch shareOutcomeMode {
        case .addToBriefing:
            return "add_to_briefing"
        case .addToKnowledge:
            return "bookmark_only"
        case .createDeck:
            return "presentation"
        case .chat:
            return "chat"
        }
    }

    // MARK: - Error Handling

    private func showError(_ error: Error) {
        let alert = UIAlertController(
            title: "Couldn't finish",
            message: error.localizedDescription,
            preferredStyle: .alert
        )
        switch submissionState.recoveryAction {
        case .openApp:
            alert.addAction(UIAlertAction(title: "Open Newsbuddy", style: .default) { _ in
                guard let appURL = URL(string: "newsly://") else { return }
                self.extensionContext?.open(appURL) { opened in
                    DispatchQueue.main.async {
                        self.submissionState.finishOpeningApp(opened: opened)
                        self.updateSubmitButtonTitle()
                        self.updateSubmitState()
                        if opened {
                            self.extensionContext?.completeRequest(
                                returningItems: [],
                                completionHandler: nil
                            )
                        } else {
                            self.showManualOpenFallback()
                        }
                    }
                }
            })
        case .retry:
            alert.addAction(UIAlertAction(title: "Try Again", style: .default) { _ in
                self.handleSubmitTapped()
            })
        case .none:
            break
        }
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in
            self.extensionContext?.cancelRequest(withError: ShareError.userCancelled)
        })
        present(alert, animated: true)
    }

    private func showManualOpenFallback() {
        let alert = UIAlertController(
            title: "Open Newsbuddy manually",
            message: "Copy the link, close this share sheet, and open Newsbuddy to sign in. Then share it again.",
            preferredStyle: .alert
        )
        if let sharedURL {
            alert.addAction(UIAlertAction(title: "Copy Link & Close", style: .default) { _ in
                self.submissionState.finishManualFallback()
                UIPasteboard.general.url = sharedURL
                self.extensionContext?.completeRequest(returningItems: nil)
            })
        }
        alert.addAction(UIAlertAction(title: "Close", style: .cancel) { _ in
            self.extensionContext?.cancelRequest(withError: ShareError.userCancelled)
        })
        present(alert, animated: true)
    }

    private func presentationFailure(for error: Error) -> ShareSubmissionFailure {
        guard let shareError = error as? ShareError else {
            return .recoverable
        }
        switch shareError {
        case .notAuthenticated:
            return .authenticationRequired
        case .invalidURL:
            return .invalidURL
        case .invalidResponse, .networkError, .serverError, .userCancelled:
            return .recoverable
        }
    }
}

private struct ShareActionRequest: Encodable {
    let url: String
    let mode: String
    let chatInitialMessage: String?
    let interestsPrompt: String?

    enum CodingKeys: String, CodingKey {
        case url
        case mode
        case chatInitialMessage = "chat_initial_message"
        case interestsPrompt = "interests_prompt"
    }
}

// MARK: - UI Components

private final class OptionRowView: UIControl {

    private let titleLabel = UILabel()
    private let descriptionLabel = UILabel()
    private let indicatorView = UIImageView()

    init(title: String, description: String, accessibilityIdentifier: String) {
        super.init(frame: .zero)

        layer.cornerRadius = 12
        layer.borderWidth = 1
        layer.borderColor = UIColor.separator.cgColor
        backgroundColor = .secondarySystemBackground
        isUserInteractionEnabled = true
        isAccessibilityElement = true
        accessibilityLabel = title
        accessibilityHint = description
        self.accessibilityIdentifier = accessibilityIdentifier

        titleLabel.text = title
        titleLabel.font = ShareExtensionStyle.font(textStyle: .body, weight: .medium)
        titleLabel.adjustsFontForContentSizeCategory = true
        titleLabel.textColor = .label

        descriptionLabel.text = description
        descriptionLabel.font = ShareExtensionStyle.font(textStyle: .footnote)
        descriptionLabel.adjustsFontForContentSizeCategory = true
        descriptionLabel.textColor = .secondaryLabel
        descriptionLabel.numberOfLines = 0

        indicatorView.tintColor = ShareExtensionStyle.brandAccent
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

    override var isEnabled: Bool {
        didSet {
            updateSelectionState()
        }
    }

    private func updateSelectionState() {
        var traits: UIAccessibilityTraits = [.button]
        if isSelected {
            traits.insert(.selected)
        }
        if !isEnabled {
            traits.insert(.notEnabled)
        }
        accessibilityTraits = traits
        alpha = isEnabled ? 1 : 0.55
        if isSelected {
            indicatorView.image = UIImage(systemName: "checkmark.circle.fill")
            layer.borderColor = ShareExtensionStyle.brandAccent.cgColor
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
            return "No web link was found. Share a page or URL and try again."
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
