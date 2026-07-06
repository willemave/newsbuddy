//
//  ImageCacheService.swift
//  newsly
//
//  Created by Assistant on 12/23/25.
//

import Foundation
import UIKit
import CryptoKit
import ImageIO
import os.log

private let imageCacheLogger = Logger(subsystem: "com.newsly", category: "ImageCacheService")

/// Two-tier image caching service with memory (NSCache) and disk (FileManager) caching.
actor ImageCacheService {
    static let shared = ImageCacheService()
    
    // MARK: - Configuration
    
    private let maxDiskCacheSize: Int64 = 100 * 1024 * 1024 // 100MB
    private let maxCacheAge: TimeInterval = 7 * 24 * 60 * 60 // 7 days
    private let diskCleanupInterval: TimeInterval = 12 * 60 * 60 // 12 hours
    
    // MARK: - Private Properties
    
    private let memoryCache: NSCache<NSString, UIImage>
    private let fileManager = FileManager.default
    private let cacheDirectory: URL
    private var inFlightDownloads: [String: Task<UIImage?, Never>] = [:]
    private var diskCleanupTask: Task<Void, Never>?
    private var lastDiskCleanupDate: Date?
    // MARK: - Initialization
    
    private init() {
        let memoryCache = NSCache<NSString, UIImage>()
        memoryCache.countLimit = 100 // Max 100 images in memory
        memoryCache.totalCostLimit = 50 * 1024 * 1024 // 50MB memory limit
        self.memoryCache = memoryCache

        // Set up cache directory
        let cachesDirectory = fileManager.urls(for: .cachesDirectory, in: .userDomainMask).first!
        cacheDirectory = cachesDirectory.appendingPathComponent("ImageCache", isDirectory: true)
        
        // Create cache directory if needed
        do {
            try fileManager.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
        } catch {
            imageCacheLogger.error(
                "Failed to create image cache directory | path=\(self.cacheDirectory.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
        }
        
        Task {
            await scheduleDiskCleanupIfNeeded(reason: "init", force: true)
        }
    }
    
    // MARK: - Public API
    
    /// Get an image from cache (memory first, then disk).
    func image(for url: URL, targetPixelSize: Int? = nil) async -> UIImage? {
        let memoryKey = cacheKey(for: url, targetPixelSize: targetPixelSize)
        let diskKey = diskCacheKey(for: url)
        
        // Check memory cache first
        if let cachedImage = memoryCache.object(forKey: memoryKey as NSString) {
            return cachedImage
        }
        
        // Check disk cache
        if let diskImage = await loadFromDisk(key: diskKey, targetPixelSize: targetPixelSize) {
            storeInMemory(diskImage, forKey: memoryKey)
            return diskImage
        }
        
        return nil
    }

    /// Return an image from cache, optionally downloading it once for all concurrent callers.
    func image(for url: URL, downloadIfMissing: Bool, targetPixelSize: Int? = nil) async -> UIImage? {
        if let cached = await image(for: url, targetPixelSize: targetPixelSize) {
            return cached
        }

        guard downloadIfMissing else {
            return nil
        }

        let key = cacheKey(for: url, targetPixelSize: targetPixelSize)
        if let task = inFlightDownloads[key] {
            return await task.value
        }

        let task = Task { await self.downloadAndCache(url: url, targetPixelSize: targetPixelSize) }
        inFlightDownloads[key] = task
        let image = await task.value
        inFlightDownloads[key] = nil
        return image
    }
    
    /// Cache an image in both memory and disk.
    func cache(_ image: UIImage, for url: URL, targetPixelSize: Int? = nil) async {
        let key = cacheKey(for: url, targetPixelSize: targetPixelSize)

        storeInMemory(image, forKey: key)
        await saveToDisk(image: image, key: diskCacheKey(for: url))
    }

    /// Decode downloaded image data off the view task and cache the original bytes.
    func cacheImageData(_ data: Data, for url: URL, targetPixelSize: Int? = nil) async -> UIImage? {
        guard let image = await preparedImage(from: data, targetPixelSize: targetPixelSize) else {
            return nil
        }

        storeInMemory(image, forKey: cacheKey(for: url, targetPixelSize: targetPixelSize))
        await saveDataToDisk(data, key: diskCacheKey(for: url))
        return image
    }
    
    /// Prefetch multiple images in the background.
    func prefetch(urls: [URL]) async {
        let urls = Array(Set(urls))
        await withTaskGroup(of: Void.self) { group in
            for url in urls {
                group.addTask {
                    await self.downloadToDiskIfMissing(url: url)
                }
            }
        }
    }
    
    /// Clear all cached images.
    func clearCache() async {
        // Clear memory cache
        memoryCache.removeAllObjects()

        let fileURLs: [URL]
        do {
            fileURLs = try fileManager.contentsOfDirectory(
                at: cacheDirectory,
                includingPropertiesForKeys: nil
            )
        } catch {
            imageCacheLogger.error(
                "Failed to enumerate image cache during clear | path=\(self.cacheDirectory.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
            return
        }
        
        for fileURL in fileURLs {
            do {
                try fileManager.removeItem(at: fileURL)
            } catch {
                imageCacheLogger.error(
                    "Failed to remove cached image during clear | path=\(fileURL.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
                )
            }
        }
    }
    
    // MARK: - Private Methods
    
    private func cacheKey(for url: URL, targetPixelSize: Int?) -> String {
        // Use SHA256 hash of URL as cache key
        let sizeKey = targetPixelSize.map { "px:\($0)" } ?? "original"
        let data = Data("\(url.absoluteString)|\(sizeKey)".utf8)
        let hash = SHA256.hash(data: data)
        return hash.compactMap { String(format: "%02x", $0) }.joined()
    }

    private func diskCacheKey(for url: URL) -> String {
        let data = Data(url.absoluteString.utf8)
        let hash = SHA256.hash(data: data)
        return hash.compactMap { String(format: "%02x", $0) }.joined()
    }

    private func storeInMemory(_ image: UIImage, forKey key: String) {
        let cost = image.cgImage.map { $0.bytesPerRow * $0.height } ?? 0
        memoryCache.setObject(image, forKey: key as NSString, cost: cost)
    }
    
    private func diskCacheURL(for key: String) -> URL {
        cacheDirectory.appendingPathComponent("\(key).image")
    }
    
    private func loadFromDisk(key: String, targetPixelSize: Int?) async -> UIImage? {
        guard let data = await loadDataFromDisk(key: key) else {
            return nil
        }
        return await preparedImage(from: data, targetPixelSize: targetPixelSize)
    }

    private func loadDataFromDisk(key: String) async -> Data? {
        let fileURL = diskCacheURL(for: key)
        
        guard fileManager.fileExists(atPath: fileURL.path) else {
            return nil
        }
        
        // Check if file is too old
        if let attributes = try? fileManager.attributesOfItem(atPath: fileURL.path),
           let modificationDate = attributes[.modificationDate] as? Date,
           Date().timeIntervalSince(modificationDate) > maxCacheAge {
            // Remove stale entry
            do {
                try fileManager.removeItem(at: fileURL)
            } catch {
                imageCacheLogger.error(
                    "Failed to remove stale cached image | path=\(fileURL.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
                )
            }
            return nil
        }
        
        do {
            return try Data(contentsOf: fileURL)
        } catch {
            imageCacheLogger.error(
                "Failed to read cached image data | path=\(fileURL.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
            return nil
        }
    }
    
    private func saveToDisk(image: UIImage, key: String) async {
        guard let data = image.pngData() else { return }

        await saveDataToDisk(data, key: key)
    }

    private func saveDataToDisk(_ data: Data, key: String) async {
        let fileURL = diskCacheURL(for: key)

        do {
            try data.write(to: fileURL)
            scheduleDiskCleanupIfNeeded(reason: "write")
        } catch {
            imageCacheLogger.error(
                "Failed to write image cache data | path=\(fileURL.path, privacy: .public) bytes=\(data.count) error=\(error.localizedDescription, privacy: .public)"
            )
        }
    }
    
    private func downloadAndCache(url: URL, targetPixelSize: Int?) async -> UIImage? {
        do {
            let (data, _) = try await URLSession.newslyDefault.data(from: url)
            return await cacheImageData(data, for: url, targetPixelSize: targetPixelSize)
        } catch {
            imageCacheLogger.error(
                "Failed to download image | url=\(url.absoluteString, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
            return nil
        }
    }

    private func downloadToDiskIfMissing(url: URL) async {
        let key = diskCacheKey(for: url)
        if await loadDataFromDisk(key: key) != nil {
            return
        }

        do {
            let (data, _) = try await URLSession.newslyDefault.data(from: url)
            await saveDataToDisk(data, key: key)
        } catch {
            imageCacheLogger.error(
                "Failed to prefetch image | url=\(url.absoluteString, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
        }
    }

    private func preparedImage(from data: Data, targetPixelSize: Int?) async -> UIImage? {
        let image: UIImage?
        if let targetPixelSize, targetPixelSize > 0 {
            image = downsampledImage(from: data, maxPixelSize: targetPixelSize) ?? UIImage(data: data)
        } else {
            image = UIImage(data: data)
        }
        guard let image else { return nil }
        return await image.byPreparingForDisplay() ?? image
    }

    private func downsampledImage(from data: Data, maxPixelSize: Int) -> UIImage? {
        let sourceOptions = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let source = CGImageSourceCreateWithData(data as CFData, sourceOptions) else {
            return nil
        }

        let downsampleOptions = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
        ] as CFDictionary

        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, downsampleOptions) else {
            return nil
        }
        return UIImage(cgImage: image)
    }
    
    private func scheduleDiskCleanupIfNeeded(reason: String, force: Bool = false) {
        let now = Date()
        if !force,
           let lastDiskCleanupDate,
           now.timeIntervalSince(lastDiskCleanupDate) < diskCleanupInterval {
            return
        }
        guard diskCleanupTask == nil else {
            return
        }

        diskCleanupTask = Task {
            await self.cleanupDiskCache(reason: reason)
        }
    }

    private func cleanupDiskCache(reason: String) async {
        defer {
            lastDiskCleanupDate = Date()
            diskCleanupTask = nil
        }

        let fileURLs: [URL]
        do {
            fileURLs = try fileManager.contentsOfDirectory(
                at: cacheDirectory,
                includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey]
            )
        } catch {
            imageCacheLogger.error(
                "Failed to enumerate image cache directory | reason=\(reason, privacy: .public) path=\(self.cacheDirectory.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
            return
        }
        
        var totalSize: Int64 = 0
        var filesToDelete: [URL] = []
        
        // Collect files and their info
        var fileInfos: [(url: URL, date: Date, size: Int64)] = []
        
        for fileURL in fileURLs {
            guard let attributes = try? fileManager.attributesOfItem(atPath: fileURL.path),
                  let modificationDate = attributes[.modificationDate] as? Date,
                  let fileSize = attributes[.size] as? Int64 else {
                continue
            }
            
            // Remove files older than max age
            if Date().timeIntervalSince(modificationDate) > maxCacheAge {
                filesToDelete.append(fileURL)
                continue
            }
            
            fileInfos.append((fileURL, modificationDate, fileSize))
            totalSize += fileSize
        }
        
        // If over size limit, remove oldest files until under limit
        if totalSize > maxDiskCacheSize {
            // Sort by date (oldest first)
            fileInfos.sort { $0.date < $1.date }
            
            var sizeToFree = totalSize - maxDiskCacheSize
            for fileInfo in fileInfos {
                if sizeToFree <= 0 { break }
                filesToDelete.append(fileInfo.url)
                sizeToFree -= fileInfo.size
            }
        }
        
        // Delete files
        for fileURL in filesToDelete {
            do {
                try fileManager.removeItem(at: fileURL)
            } catch {
                imageCacheLogger.error(
                    "Failed to remove cached image during cleanup | reason=\(reason, privacy: .public) path=\(fileURL.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
                )
            }
        }

        if !filesToDelete.isEmpty {
            imageCacheLogger.info(
                "Image cache cleanup completed | reason=\(reason, privacy: .public) removed=\(filesToDelete.count) scanned=\(fileURLs.count)"
            )
        }
    }
}
