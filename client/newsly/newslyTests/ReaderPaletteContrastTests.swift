import UIKit
import XCTest
@testable import newsly

final class ReaderPaletteContrastTests: XCTestCase {
    func testTextColorsMeetNormalTextContrastOnAppSurfaces() {
        for style in [UIUserInterfaceStyle.light, .dark] {
            let traits = UITraitCollection(userInterfaceStyle: style)
            let colors = ReaderPalette.colors
            let surfaces = [colors.surfacePrimary, colors.surfaceSecondary]

            for surface in surfaces {
                XCTAssertGreaterThanOrEqual(
                    contrastRatio(
                        colors.brandPrimary.uiColor(for: traits),
                        surface.uiColor(for: traits)
                    ),
                    4.5,
                    "brandPrimary must support normal text in \(style) mode"
                )
                XCTAssertGreaterThanOrEqual(
                    contrastRatio(
                        colors.onSurfaceTertiary.uiColor(for: traits),
                        surface.uiColor(for: traits)
                    ),
                    4.5,
                    "onSurfaceTertiary must support normal text in \(style) mode"
                )
            }
        }
    }

    func testGlobalAccentAssetMatchesBrandPrimary() throws {
        let accent = try XCTUnwrap(UIColor(named: "AccentColor"))

        for style in [UIUserInterfaceStyle.light, .dark] {
            let traits = UITraitCollection(userInterfaceStyle: style)
            assertEqualRGB(
                accent.resolvedColor(with: traits),
                ReaderPalette.colors.brandPrimary.uiColor(for: traits)
            )
        }
    }

    private func contrastRatio(_ foreground: UIColor, _ background: UIColor) -> CGFloat {
        let foregroundLuminance = relativeLuminance(foreground)
        let backgroundLuminance = relativeLuminance(background)
        let lighter = max(foregroundLuminance, backgroundLuminance)
        let darker = min(foregroundLuminance, backgroundLuminance)
        return (lighter + 0.05) / (darker + 0.05)
    }

    private func relativeLuminance(_ color: UIColor) -> CGFloat {
        let components = rgbComponents(color)
        let linear = components.map { component in
            component <= 0.04045
                ? component / 12.92
                : pow((component + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    }

    private func assertEqualRGB(
        _ actual: UIColor,
        _ expected: UIColor,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let actualComponents = rgbComponents(actual)
        let expectedComponents = rgbComponents(expected)

        for index in 0..<3 {
            XCTAssertEqual(
                actualComponents[index],
                expectedComponents[index],
                accuracy: 0.002,
                file: file,
                line: line
            )
        }
    }

    private func rgbComponents(_ color: UIColor) -> [CGFloat] {
        var red: CGFloat = 0
        var green: CGFloat = 0
        var blue: CGFloat = 0
        var alpha: CGFloat = 0
        XCTAssertTrue(color.getRed(&red, green: &green, blue: &blue, alpha: &alpha))
        return [red, green, blue]
    }
}
