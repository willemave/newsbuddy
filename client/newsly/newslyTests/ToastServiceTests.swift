import XCTest
@testable import newsly

@MainActor
final class ToastServiceTests: XCTestCase {
    override func tearDown() {
        ToastService.shared.dismiss()
        super.tearDown()
    }

    func testShowStoresCurrentToastAndDismissClearsIt() {
        let service = ToastService.shared
        service.dismiss()

        service.show("Saved", type: .success, duration: 10)

        XCTAssertEqual(service.currentToast?.message, "Saved")
        XCTAssertEqual(service.currentToast?.type, .success)

        service.dismiss()

        XCTAssertNil(service.currentToast)
    }
}
