import Foundation
import MediaPlayer
import UIKit

@MainActor
struct NarrationRemoteCommandActions {
    let play: () -> Void
    let pause: () -> Void
    let seek: (TimeInterval) -> Void
    let previous: (() -> Void)?
    let next: (() -> Void)?
}

@MainActor
final class NarrationNowPlayingController {
    static let shared = NarrationNowPlayingController()

    private let infoCenter: MPNowPlayingInfoCenter
    private let commandCenter: MPRemoteCommandCenter
    private let fallbackArtwork: MPMediaItemArtwork?
    private let artworkCache = NSCache<NSURL, UIImage>()
    private var commandTargets: [(command: MPRemoteCommand, target: Any)] = []
    private var actions: NarrationRemoteCommandActions?
    private var metadata: NarrationPlaybackMetadata?
    private var artwork: MPMediaItemArtwork?
    private var artworkTask: Task<Void, Never>?
    private var activationID: UUID?

    init(
        infoCenter: MPNowPlayingInfoCenter = .default(),
        commandCenter: MPRemoteCommandCenter = .shared()
    ) {
        self.infoCenter = infoCenter
        self.commandCenter = commandCenter
        self.fallbackArtwork = Self.artwork(
            for: UIImage(named: "AppMark") ?? UIImage(named: "BuddyMark")
        )
        registerCommands()
        setCommandsEnabled(false)
    }

    deinit {
        artworkTask?.cancel()
        for commandTarget in commandTargets {
            commandTarget.command.removeTarget(commandTarget.target)
        }
    }

    func activate(
        metadata: NarrationPlaybackMetadata,
        duration: TimeInterval,
        elapsed: TimeInterval,
        currentRate: Float,
        defaultRate: Float,
        actions: NarrationRemoteCommandActions
    ) {
        artworkTask?.cancel()
        let activationID = UUID()
        self.activationID = activationID
        self.metadata = metadata
        self.actions = actions
        artwork = fallbackArtwork
        setCommandsEnabled(true)
        publish(
            duration: duration,
            elapsed: elapsed,
            currentRate: currentRate,
            defaultRate: defaultRate
        )
        loadArtwork(from: metadata.artworkURL, activationID: activationID)
    }

    func update(
        duration: TimeInterval,
        elapsed: TimeInterval,
        currentRate: Float,
        defaultRate: Float
    ) {
        guard metadata != nil else { return }
        publish(
            duration: duration,
            elapsed: elapsed,
            currentRate: currentRate,
            defaultRate: defaultRate
        )
    }

    func clear() {
        artworkTask?.cancel()
        artworkTask = nil
        activationID = nil
        metadata = nil
        actions = nil
        artwork = nil
        infoCenter.nowPlayingInfo = nil
        setCommandsEnabled(false)
    }

    private func publish(
        duration: TimeInterval,
        elapsed: TimeInterval,
        currentRate: Float,
        defaultRate: Float
    ) {
        guard let metadata else { return }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: metadata.title,
            MPMediaItemPropertyAlbumTitle: metadata.collectionTitle,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: max(elapsed, 0),
            MPNowPlayingInfoPropertyPlaybackRate: currentRate,
            MPNowPlayingInfoPropertyDefaultPlaybackRate: defaultRate,
            MPNowPlayingInfoPropertyPlaybackQueueIndex: metadata.chapterIndex,
            MPNowPlayingInfoPropertyPlaybackQueueCount: metadata.chapterCount,
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
        ]
        if let subtitle = metadata.subtitle, !subtitle.isEmpty {
            info[MPMediaItemPropertyArtist] = subtitle
        } else {
            info[MPMediaItemPropertyArtist] = "Newsly"
        }
        if duration.isFinite, duration > 0 {
            info[MPMediaItemPropertyPlaybackDuration] = duration
        }
        if let artwork {
            info[MPMediaItemPropertyArtwork] = artwork
        }
        infoCenter.nowPlayingInfo = info
    }

    private func loadArtwork(from url: URL?, activationID: UUID) {
        guard let url else { return }
        if let image = artworkCache.object(forKey: url as NSURL) {
            applyArtwork(image, activationID: activationID)
            return
        }
        artworkTask = Task { [weak self] in
            guard let self else { return }
            do {
                let (data, response) = try await URLSession.shared.data(from: url)
                try Task.checkCancellation()
                guard let response = response as? HTTPURLResponse,
                      (200..<300).contains(response.statusCode),
                      let image = UIImage(data: data) else { return }
                artworkCache.setObject(image, forKey: url as NSURL)
                applyArtwork(image, activationID: activationID)
            } catch {
                return
            }
        }
    }

    private func applyArtwork(_ image: UIImage, activationID: UUID) {
        guard self.activationID == activationID else { return }
        artwork = Self.artwork(for: image)
        guard var info = infoCenter.nowPlayingInfo, let artwork else { return }
        info[MPMediaItemPropertyArtwork] = artwork
        infoCenter.nowPlayingInfo = info
    }

    private static func artwork(for image: UIImage?) -> MPMediaItemArtwork? {
        image.map { image in
            MPMediaItemArtwork(boundsSize: image.size) { _ in image }
        }
    }

    private func registerCommands() {
        let playTarget = commandCenter.playCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.actions?.play() }
            return .success
        }
        commandTargets.append((commandCenter.playCommand, playTarget))

        let pauseTarget = commandCenter.pauseCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.actions?.pause() }
            return .success
        }
        commandTargets.append((commandCenter.pauseCommand, pauseTarget))

        let toggleTarget = commandCenter.togglePlayPauseCommand.addTarget { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                if self.currentPlaybackRate > 0 {
                    self.actions?.pause()
                } else {
                    self.actions?.play()
                }
            }
            return .success
        }
        commandTargets.append((commandCenter.togglePlayPauseCommand, toggleTarget))

        let positionTarget = commandCenter.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            let position = event.positionTime
            Task { @MainActor in self?.actions?.seek(position) }
            return .success
        }
        commandTargets.append((commandCenter.changePlaybackPositionCommand, positionTarget))

        let previousTarget = commandCenter.previousTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.actions?.previous?() }
            return .success
        }
        commandTargets.append((commandCenter.previousTrackCommand, previousTarget))

        let nextTarget = commandCenter.nextTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.actions?.next?() }
            return .success
        }
        commandTargets.append((commandCenter.nextTrackCommand, nextTarget))
    }

    private var currentPlaybackRate: Float {
        (infoCenter.nowPlayingInfo?[MPNowPlayingInfoPropertyPlaybackRate] as? NSNumber)?.floatValue ?? 0
    }

    private func setCommandsEnabled(_ active: Bool) {
        commandCenter.playCommand.isEnabled = active
        commandCenter.pauseCommand.isEnabled = active
        commandCenter.togglePlayPauseCommand.isEnabled = active
        commandCenter.changePlaybackPositionCommand.isEnabled = active
        commandCenter.previousTrackCommand.isEnabled = active && actions?.previous != nil
        commandCenter.nextTrackCommand.isEnabled = active && actions?.next != nil
    }
}
