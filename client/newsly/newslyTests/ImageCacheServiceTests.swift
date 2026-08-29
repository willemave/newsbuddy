import UIKit
import XCTest
@testable import newsly

final class ImageCacheServiceTests: XCTestCase {
    override func tearDown() {
        ImageCacheURLProtocol.requestHandler = nil
        super.tearDown()
    }

    func testConcurrentSizeVariantsShareOneRawDownload() async throws {
        let fixture = makePNGData()
        let requests = LockedRequestMetrics()
        ImageCacheURLProtocol.requestHandler = { request in
            requests.beginRequest()
            defer { requests.endRequest() }
            Thread.sleep(forTimeInterval: 0.1)
            return Self.response(for: request, data: fixture)
        }

        let (cache, session, directory) = try makeCache()
        defer {
            session.invalidateAndCancel()
            try? FileManager.default.removeItem(at: directory)
        }
        let url = URL(string: "https://images.example.test/shared.png")!

        async let small = cache.image(for: url, downloadIfMissing: true, targetPixelSize: 256)
        async let large = cache.image(for: url, downloadIfMissing: true, targetPixelSize: 768)
        let images = await (small, large)

        XCTAssertNotNil(images.0)
        XCTAssertNotNil(images.1)
        XCTAssertEqual(requests.snapshot.requestCount, 1)
    }

    func testPrefetchBoundsConcurrentDownloads() async throws {
        let fixture = makePNGData()
        let requests = LockedRequestMetrics()
        ImageCacheURLProtocol.requestHandler = { request in
            requests.beginRequest()
            defer { requests.endRequest() }
            Thread.sleep(forTimeInterval: 0.05)
            return Self.response(for: request, data: fixture)
        }

        let (cache, session, directory) = try makeCache()
        defer {
            session.invalidateAndCancel()
            try? FileManager.default.removeItem(at: directory)
        }
        let urls = (0..<8).map { URL(string: "https://images.example.test/\($0).png")! }

        await cache.prefetch(urls: urls, maximumConcurrentDownloads: 2)

        XCTAssertEqual(requests.snapshot.requestCount, urls.count)
        XCTAssertLessThanOrEqual(requests.snapshot.peakConcurrentCount, 2)
    }

    func testStableIdentifierReusesImageAcrossRotatingSignedURLs() async throws {
        let fixture = makePNGData()
        let requests = LockedRequestMetrics()
        ImageCacheURLProtocol.requestHandler = { request in
            requests.beginRequest()
            defer { requests.endRequest() }
            return Self.response(for: request, data: fixture)
        }

        let (cache, session, directory) = try makeCache()
        defer {
            session.invalidateAndCancel()
            try? FileManager.default.removeItem(at: directory)
        }
        let firstURL = URL(string: "https://images.example.test/signed/first/thumbnail.png")!
        let refreshedURL = URL(
            string: "https://images.example.test/signed/refreshed/thumbnail.png"
        )!
        let cacheIdentifier = "learning-deck:41:attempt:82"

        let first = await cache.image(
            for: firstURL,
            downloadIfMissing: true,
            targetPixelSize: 128,
            cacheIdentifier: cacheIdentifier
        )
        let refreshed = await cache.image(
            for: refreshedURL,
            downloadIfMissing: true,
            targetPixelSize: 128,
            cacheIdentifier: cacheIdentifier
        )

        XCTAssertNotNil(first)
        XCTAssertNotNil(refreshed)
        XCTAssertEqual(requests.snapshot.requestCount, 1)
    }

    func testTargetPixelSizeUsesStableBuckets() {
        XCTAssertNil(ImageRequestSizing.targetPixelSize(for: nil, scale: 2))
        XCTAssertEqual(
            ImageRequestSizing.targetPixelSize(
                for: CGSize(width: 100, height: 100),
                scale: 2
            ),
            256
        )
        XCTAssertEqual(
            ImageRequestSizing.targetPixelSize(
                for: CGSize(width: 101, height: 100),
                scale: 2
            ),
            256
        )
        XCTAssertEqual(
            ImageRequestSizing.targetPixelSize(
                for: CGSize(width: 129, height: 100),
                scale: 2
            ),
            384
        )
    }

    private func makeCache() throws -> (ImageCacheService, URLSession, URL) {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ImageCacheURLProtocol.self]
        let session = URLSession(configuration: configuration)
        return (
            ImageCacheService(
                session: session,
                cacheDirectory: directory,
                schedulesInitialCleanup: false
            ),
            session,
            directory
        )
    }

    private func makePNGData() -> Data {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 8, height: 8))
        return renderer.pngData { context in
            UIColor.systemBlue.setFill()
            context.cgContext.fill(CGRect(x: 0, y: 0, width: 8, height: 8))
        }
    }

    private static func response(
        for request: URLRequest,
        data: Data
    ) -> (HTTPURLResponse, Data) {
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: 200,
            httpVersion: nil,
            headerFields: ["Content-Type": "image/png"]
        )!
        return (response, data)
    }
}

private final class ImageCacheURLProtocol: URLProtocol {
    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let requestHandler = Self.requestHandler else {
            XCTFail("Missing image request handler")
            return
        }

        do {
            let (response, data) = try requestHandler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private final class LockedRequestMetrics: @unchecked Sendable {
    private let lock = NSLock()
    private var requestCount = 0
    private var concurrentCount = 0
    private var peakConcurrentCount = 0

    var snapshot: (requestCount: Int, peakConcurrentCount: Int) {
        lock.withLock {
            (requestCount, peakConcurrentCount)
        }
    }

    func beginRequest() {
        lock.withLock {
            requestCount += 1
            concurrentCount += 1
            peakConcurrentCount = max(peakConcurrentCount, concurrentCount)
        }
    }

    func endRequest() {
        lock.withLock {
            concurrentCount -= 1
        }
    }
}
