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
    private let session: URLSession
    private let cacheDirectory: URL
    private var inFlightImagePreparations: [String: Task<UIImage?, Never>] = [:]
    private var inFlightDataDownloads: [String: Task<Data?, Never>] = [:]
    private var diskCleanupTask: Task<Void, Never>?
    private var lastDiskCleanupDate: Date?
    // MARK: - Initialization
    
    init(
        session: URLSession = .newslyDefault,
        cacheDirectory: URL? = nil,
        schedulesInitialCleanup: Bool = true
    ) {
        let memoryCache = NSCache<NSString, UIImage>()
        memoryCache.countLimit = 100 // Max 100 images in memory
        memoryCache.totalCostLimit = 50 * 1024 * 1024 // 50MB memory limit
        self.memoryCache = memoryCache
        self.session = session

        // Set up cache directory
        let cachesDirectory = fileManager.urls(for: .cachesDirectory, in: .userDomainMask).first!
        self.cacheDirectory = cacheDirectory
            ?? cachesDirectory.appendingPathComponent("ImageCache", isDirectory: true)
        
        // Create cache directory if needed
        do {
            try fileManager.createDirectory(at: self.cacheDirectory, withIntermediateDirectories: true)
        } catch {
            imageCacheLogger.error(
                "Failed to create image cache directory | path=\(self.cacheDirectory.path, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
        }
        
        if schedulesInitialCleanup {
            Task {
                await scheduleDiskCleanupIfNeeded(reason: "init", force: true)
            }
        }
    }
    
    // MARK: - Public API
    
    /// Get an image from cache (memory first, then disk).
    func image(
        for url: URL,
        targetPixelSize: Int? = nil,
        cacheIdentifier: String? = nil
    ) async -> UIImage? {
        let memoryKey = cacheKey(
            for: url,
            targetPixelSize: targetPixelSize,
            cacheIdentifier: cacheIdentifier
        )
        let diskKey = diskCacheKey(for: url, cacheIdentifier: cacheIdentifier)
        
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
    func image(
        for url: URL,
        downloadIfMissing: Bool,
        targetPixelSize: Int? = nil,
        cacheIdentifier: String? = nil
    ) async -> UIImage? {
        if let cached = await image(
            for: url,
            targetPixelSize: targetPixelSize,
            cacheIdentifier: cacheIdentifier
        ) {
            return cached
        }

        guard downloadIfMissing else {
            return nil
        }

        let key = cacheKey(
            for: url,
            targetPixelSize: targetPixelSize,
            cacheIdentifier: cacheIdentifier
        )
        if let task = inFlightImagePreparations[key] {
            return await task.value
        }

        let task = Task {
            await self.downloadAndCache(
                url: url,
                targetPixelSize: targetPixelSize,
                cacheIdentifier: cacheIdentifier
            )
        }
        inFlightImagePreparations[key] = task
        let image = await task.value
        inFlightImagePreparations[key] = nil
        return image
    }
    
    // MARK: - Private Methods
    
    private func cacheKey(
        for url: URL,
        targetPixelSize: Int?,
        cacheIdentifier: String?
    ) -> String {
        // Hash the stable source identity when one is available; otherwise use the URL.
        let sizeKey = targetPixelSize.map { "px:\($0)" } ?? "original"
        let sourceKey = sourceCacheKey(for: url, cacheIdentifier: cacheIdentifier)
        let data = Data("\(sourceKey)|\(sizeKey)".utf8)
        let hash = SHA256.hash(data: data)
        return hash.compactMap { String(format: "%02x", $0) }.joined()
    }

    private func diskCacheKey(for url: URL, cacheIdentifier: String?) -> String {
        let data = Data(sourceCacheKey(for: url, cacheIdentifier: cacheIdentifier).utf8)
        let hash = SHA256.hash(data: data)
        return hash.compactMap { String(format: "%02x", $0) }.joined()
    }

    private func sourceCacheKey(for url: URL, cacheIdentifier: String?) -> String {
        if let cacheIdentifier, !cacheIdentifier.isEmpty {
            return "identifier:\(cacheIdentifier)"
        }
        return "url:\(url.absoluteString)"
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
    
    private func downloadAndCache(
        url: URL,
        targetPixelSize: Int?,
        cacheIdentifier: String?
    ) async -> UIImage? {
        guard let data = await cachedOrDownloadedData(
            for: url,
            cacheIdentifier: cacheIdentifier
        ), !Task.isCancelled else {
            return nil
        }

        guard let image = await preparedImage(from: data, targetPixelSize: targetPixelSize) else {
            return nil
        }

        storeInMemory(
            image,
            forKey: cacheKey(
                for: url,
                targetPixelSize: targetPixelSize,
                cacheIdentifier: cacheIdentifier
            )
        )
        return image
    }

    /// Coalesce the raw transfer by URL so thumbnail/full-size decode variants share one request.
    private func cachedOrDownloadedData(
        for url: URL,
        cacheIdentifier: String? = nil
    ) async -> Data? {
        let diskKey = diskCacheKey(for: url, cacheIdentifier: cacheIdentifier)
        if let cached = await loadDataFromDisk(key: diskKey) {
            return cached
        }

        let downloadKey = sourceCacheKey(for: url, cacheIdentifier: cacheIdentifier)
        if let task = inFlightDataDownloads[downloadKey] {
            return await task.value
        }

        let task = Task { await self.downloadData(for: url) }
        inFlightDataDownloads[downloadKey] = task
        let data = await task.value
        inFlightDataDownloads[downloadKey] = nil

        if let data, !Task.isCancelled {
            await saveDataToDisk(data, key: diskKey)
        }
        return data
    }

    private func downloadData(for url: URL) async -> Data? {
        do {
            let (data, response) = try await session.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse,
                  (200..<300).contains(httpResponse.statusCode) else {
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
                imageCacheLogger.error(
                    "Image request failed | url=\(url.absoluteString, privacy: .public) status=\(statusCode)"
                )
                return nil
            }
            return data
        } catch {
            imageCacheLogger.error(
                "Failed to download image | url=\(url.absoluteString, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
            return nil
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
