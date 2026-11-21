import XCTest
@testable import newsly

@MainActor
final class BriefingSavedSourcesTests: XCTestCase {
    func testSavePreservesNamespaceAndRecordsSavedSource() async {
        var requests: [KnowledgeSource] = []
        let store = BriefingSavedSources(saveSource: { source in
            requests.append(source)
            return requests.count == 1 ? "Saved" : "Already saved"
        })

        let news = await store.save(sourceKey: "news:7", source: .news(7))
        let content = await store.save(sourceKey: "content:7", source: .content(7))

        XCTAssertEqual(news, .saved(message: "Saved"))
        XCTAssertEqual(content, .saved(message: "Already saved"))
        XCTAssertEqual(requests, [.news(7), .content(7)])
        XCTAssertTrue(store.isSaved("news:7"))
        XCTAssertTrue(store.isSaved("content:7"))
        XCTAssertFalse(store.isPending("news:7"))
        XCTAssertFalse(store.isPending("content:7"))
    }

    func testRepeatedSaveIsIdempotentAndNeverToggles() async {
        let store = BriefingSavedSources(saveSource: { _ in "Saved" })
        _ = await store.save(sourceKey: "content:7", source: .content(7))
        let repeated = await store.save(sourceKey: "content:7", source: .content(7))

        XCTAssertEqual(repeated, .saved(message: "Saved"))
        XCTAssertTrue(store.isSaved("content:7"))
    }

    func testPendingSaveRejectsDuplicateButNotOtherSources() async {
        var continuation: CheckedContinuation<String, Never>?
        var requests: [KnowledgeSource] = []
        let store = BriefingSavedSources(saveSource: { source in
            requests.append(source)
            if source == .content(7) {
                return await withCheckedContinuation { continuation = $0 }
            }
            return "Saved"
        })

        let first = Task { await store.save(sourceKey: "content:7", source: .content(7)) }
        while continuation == nil { await Task.yield() }
        XCTAssertTrue(store.isPending("content:7"))

        let duplicate = await store.save(sourceKey: "content:7", source: .content(7))
        let other = await store.save(sourceKey: "content:8", source: .content(8))
        XCTAssertEqual(duplicate, .ignored)
        XCTAssertEqual(other, .saved(message: "Saved"))
        XCTAssertTrue(store.isSaved("content:8"))
        XCTAssertFalse(store.isSaved("content:7"))

        continuation?.resume(returning: "Saved")
        let result = await first.value
        XCTAssertEqual(result, .saved(message: "Saved"))
        XCTAssertTrue(store.isSaved("content:7"))
        XCTAssertFalse(store.isPending("content:7"))
        XCTAssertEqual(requests, [.content(7), .content(8)])
    }

    func testFailureLeavesSourceUnsavedAndRetryCanSucceed() async {
        var attempts = 0
        let store = BriefingSavedSources(saveSource: { _ in
            attempts += 1
            if attempts == 1 { throw TestFailure.failed }
            return "Saved"
        })

        let failed = await store.save(sourceKey: "content:7", source: .content(7))
        XCTAssertEqual(failed, .failed(message: "Test failure"))
        XCTAssertFalse(store.isSaved("content:7"))
        XCTAssertFalse(store.isPending("content:7"))

        let retry = await store.save(sourceKey: "content:7", source: .content(7))
        XCTAssertEqual(retry, .saved(message: "Saved"))
        XCTAssertTrue(store.isSaved("content:7"))
    }

    func testCancellationSuppressesLateFailure() async {
        var continuation: CheckedContinuation<String, Error>?
        let store = BriefingSavedSources(saveSource: { _ in
            try await withCheckedThrowingContinuation { continuation = $0 }
        })
        let task = Task { await store.save(sourceKey: "content:7", source: .content(7)) }
        while continuation == nil { await Task.yield() }
        task.cancel()
        continuation?.resume(throwing: TestFailure.failed)

        let result = await task.value
        XCTAssertEqual(result, .ignored)
        XCTAssertFalse(store.isSaved("content:7"))
        XCTAssertFalse(store.isPending("content:7"))
    }

    func testCancelledCallerStillRecordsAcceptedSave() async {
        var continuation: CheckedContinuation<String, Never>?
        let store = BriefingSavedSources(saveSource: { _ in
            await withCheckedContinuation { continuation = $0 }
        })
        let task = Task { await store.save(sourceKey: "content:7", source: .content(7)) }
        while continuation == nil { await Task.yield() }
        task.cancel()
        continuation?.resume(returning: "Saved")

        let result = await task.value
        XCTAssertEqual(result, .ignored)
        XCTAssertTrue(store.isSaved("content:7"))
    }

    func testDetachCancelsPendingSaveAndDiscardsItsLateSuccess() async {
        var continuation: CheckedContinuation<String, Never>?
        var cancellationObserved = false
        let store = BriefingSavedSources(saveSource: { _ in
            let message = await withCheckedContinuation { continuation = $0 }
            cancellationObserved = Task.isCancelled
            return message
        })
        let task = Task { await store.save(sourceKey: "content:7", source: .content(7)) }
        while continuation == nil { await Task.yield() }

        store.detach()
        XCTAssertFalse(store.isPending("content:7"))
        let newSave = await store.save(sourceKey: "content:8", source: .content(8))
        XCTAssertEqual(newSave, .ignored)
        continuation?.resume(returning: "Saved")

        let result = await task.value
        XCTAssertEqual(result, .ignored)
        XCTAssertTrue(cancellationObserved)
        XCTAssertFalse(store.isSaved("content:7"))
    }

    func testAlreadyCancelledTaskDoesNotSubmit() async {
        var callCount = 0
        let store = BriefingSavedSources(saveSource: { _ in callCount += 1; return "Saved" })
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return await store.save(sourceKey: "content:7", source: .content(7))
        }

        let result = await task.value
        XCTAssertEqual(result, .ignored)
        XCTAssertEqual(callCount, 0)
    }
}

private enum TestFailure: LocalizedError {
    case failed
    var errorDescription: String? { "Test failure" }
}
