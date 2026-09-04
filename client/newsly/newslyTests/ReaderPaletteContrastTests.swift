import UIKit
import XCTest
@testable import newsly

final class ReaderPaletteContrastTests: XCTestCase {
    func testTextColorsMeetNormalTextContrastOnPrimaryAndSecondarySurfaces() {
        for style in [UIUserInterfaceStyle.light, .dark] {
            let traits = UITraitCollection(userInterfaceStyle: style)
            let colors = ReaderPalette.colors
            let surfaces = [colors.surfacePrimary, colors.surfaceSecondary]
            let textRoles = [
                ("onSurface", colors.onSurface),
                ("readerBodyText", colors.readerBodyText),
                ("onSurfaceSecondary", colors.onSurfaceSecondary),
                ("onSurfaceTertiary", colors.onSurfaceTertiary),
                ("brandPrimary", colors.brandPrimary)
            ]

            for surface in surfaces {
                for (name, textColor) in textRoles {
                    XCTAssertGreaterThanOrEqual(
                        contrastRatio(
                            textColor.uiColor(for: traits),
                            surface.uiColor(for: traits)
                        ),
                        4.5,
                        "\(name) must support normal text in \(style) mode"
                    )
                }
            }
        }
    }

    func testSecondaryTextMeetsNormalTextContrastOnTertiarySurface() {
        for style in [UIUserInterfaceStyle.light, .dark] {
            let traits = UITraitCollection(userInterfaceStyle: style)
            let colors = ReaderPalette.colors

            XCTAssertGreaterThanOrEqual(
                contrastRatio(
                    colors.onSurfaceSecondary.uiColor(for: traits),
                    colors.surfaceTertiary.uiColor(for: traits)
                ),
                4.5,
                "onSurfaceSecondary must support normal text on surfaceTertiary in \(style) mode"
            )
        }
    }

    /// Content placed on the accent must invert with it. `.white` fails against the
    /// light dark-mode accent — the on-accent foreground is `surfacePrimary`.
    func testSurfacePrimaryReadsOnAccentFills() {
        for style in [UIUserInterfaceStyle.light, .dark] {
            let traits = UITraitCollection(userInterfaceStyle: style)
            let colors = ReaderPalette.colors

            for accent in [colors.brandPrimary, colors.brandPrimaryStrong] {
                XCTAssertGreaterThanOrEqual(
                    contrastRatio(
                        colors.surfacePrimary.uiColor(for: traits),
                        accent.uiColor(for: traits)
                    ),
                    4.5,
                    "surfacePrimary must stay readable on accent fills in \(style) mode"
                )
            }
        }
    }

    /// The launch colorset is a hand-copied second source of `surfacePrimary`. If the
    /// palette moves and this asset does not, cold start flashes the old ground.
    func testLaunchBackgroundAssetMatchesSurfacePrimary() throws {
        let launch = try XCTUnwrap(UIColor(named: "LaunchBackground"))

        for style in [UIUserInterfaceStyle.light, .dark] {
            let traits = UITraitCollection(userInterfaceStyle: style)
            assertEqualRGB(
                launch.resolvedColor(with: traits),
                ReaderPalette.colors.surfacePrimary.uiColor(for: traits)
            )
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
