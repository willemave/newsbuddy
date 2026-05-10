//
//  ImageCacheService.swift
//  newsly
//
//  Created by Assistant on 12/23/25.
//

import Foundation
import UIKit
import CryptoKit

/// Two-tier image caching service with memory (NSCache) and disk (FileManager) caching.
actor ImageCacheService {
    static let shared = ImageCacheService()
    
    // MARK: - Configuration
    
    private let maxDiskCacheSize: Int64 = 100 * 1024 * 1024 // 100MB
    private let maxCacheAge: TimeInterval = 7 * 24 * 60 * 60 // 7 days
    
    // MARK: - Private Properties
    
    private let memoryCache = NSCache<NSString, UIImage>()
    private let fileManager = FileManager.default
    private let cacheDirectory: URL
    private var inFlightDownloads: [String: Task<UIImage?, Never>] = [:]
    // MARK: - Initialization
    
    private init() {
        // Set up cache directory
        let cachesDirectory = fileManager.urls(for: .cachesDirectory, in: .userDomainMask).first!
        cacheDirectory = cachesDirectory.appendingPathComponent("ImageCache", isDirectory: true)
        
        // Create cache directory if needed
        try? fileManager.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
        
        // Configure memory cache
        memoryCache.countLimit = 100 // Max 100 images in memory
        memoryCache.totalCostLimit = 50 * 1024 * 1024 // 50MB memory limit
        
        // Clean up old entries on init (async)
        Task {
            await cleanupDiskCache()
        }
    }
    
    // MARK: - Public API
    
    /// Get an image from cache (memory first, then disk).
    func image(for url: URL) async -> UIImage? {
        let key = cacheKey(for: url)
        
        // Check memory cache first
        if let cachedImage = memoryCache.object(forKey: key as NSString) {
            return cachedImage
        }
        
        // Check disk cache
        if let diskImage = await loadFromDisk(key: key) {
            storeInMemory(diskImage, forKey: key)
            return diskImage
        }
        
        return nil
    }

    /// Return an image from cache, optionally downloading it once for all concurrent callers.
    func image(for url: URL, downloadIfMissing: Bool) async -> UIImage? {
        if let cached = await image(for: url) {
            return cached
        }

        guard downloadIfMissing else {
            return nil
        }

        let key = cacheKey(for: url)
        if let task = inFlightDownloads[key] {
            return await task.value
        }

        let task = Task { await self.downloadAndCache(url: url) }
        inFlightDownloads[key] = task
        let image = await task.value
        inFlightDownloads[key] = nil
        return image
    }
    
    /// Cache an image in both memory and disk.
    func cache(_ image: UIImage, for url: URL) async {
        let key = cacheKey(for: url)

        storeInMemory(image, forKey: key)
        await saveToDisk(image: image, key: key)
    }

    /// Decode downloaded image data off the view task and cache the original bytes.
    func cacheImageData(_ data: Data, for url: URL) async -> UIImage? {
        guard let image = UIImage(data: data) else {
            return nil
        }

        let key = cacheKey(for: url)
        storeInMemory(image, forKey: key)
        await saveDataToDisk(data, key: key)
        return image
    }
    
    /// Prefetch multiple images in the background.
    func prefetch(urls: [URL]) async {
        let urls = Array(Set(urls))
        await withTaskGroup(of: Void.self) { group in
            for url in urls {
                group.addTask {
                    _ = await self.image(for: url, downloadIfMissing: true)
                }
            }
        }
    }
    
    /// Clear all cached images.
    func clearCache() async {
        // Clear memory cache
        memoryCache.removeAllObjects()
        
        // Clear disk cache
        let fileURLs = (try? fileManager.contentsOfDirectory(
            at: cacheDirectory,
            includingPropertiesForKeys: nil
        )) ?? []
        
        for fileURL in fileURLs {
            try? fileManager.removeItem(at: fileURL)
        }
    }
    
    // MARK: - Private Methods
    
    private func cacheKey(for url: URL) -> String {
        // Use SHA256 hash of URL as cache key
        let data = Data(url.absoluteString.utf8)
        let hash = SHA256.hash(data: data)
        return hash.compactMap { String(format: "%02x", $0) }.joined()
    }

    private func storeInMemory(_ image: UIImage, forKey key: String) {
        let cost = image.cgImage.map { $0.bytesPerRow * $0.height } ?? 0
        memoryCache.setObject(image, forKey: key as NSString, cost: cost)
    }
    
    private func diskCacheURL(for key: String) -> URL {
        cacheDirectory.appendingPathComponent("\(key).png")
    }
    
    private func loadFromDisk(key: String) async -> UIImage? {
        let fileURL = diskCacheURL(for: key)
        
        guard fileManager.fileExists(atPath: fileURL.path) else {
            return nil
        }
        
        // Check if file is too old
        if let attributes = try? fileManager.attributesOfItem(atPath: fileURL.path),
           let modificationDate = attributes[.modificationDate] as? Date,
           Date().timeIntervalSince(modificationDate) > maxCacheAge {
            // Remove stale entry
            try? fileManager.removeItem(at: fileURL)
            return nil
        }
        
        guard let data = try? Data(contentsOf: fileURL),
              let image = UIImage(data: data) else {
            return nil
        }
        
        return image
    }
    
    private func saveToDisk(image: UIImage, key: String) async {
        guard let data = image.pngData() else { return }

        await saveDataToDisk(data, key: key)
    }

    private func saveDataToDisk(_ data: Data, key: String) async {
        let fileURL = diskCacheURL(for: key)

        do {
            try data.write(to: fileURL)
        } catch {}
    }
    
    private func downloadAndCache(url: URL) async -> UIImage? {
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            return await cacheImageData(data, for: url)
        } catch {
            return nil
        }
    }
    
    private func cleanupDiskCache() async {
        guard let fileURLs = try? fileManager.contentsOfDirectory(
            at: cacheDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey]
        ) else { return }
        
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
            try? fileManager.removeItem(at: fileURL)
        }
    }
}
