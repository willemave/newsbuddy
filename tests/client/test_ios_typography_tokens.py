from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "client/newsly/newsly"
SHARE_EXTENSION_ROOT = REPO_ROOT / "client/newsly/ShareExtension"
DESIGN_TOKENS = APP_ROOT / "Views/Shared/DesignTokens.swift"
APP_INFO_PLIST = APP_ROOT / "Info.plist"
SHARE_EXTENSION_INFO_PLIST = SHARE_EXTENSION_ROOT / "Info.plist"
SHARE_VIEW_CONTROLLER = SHARE_EXTENSION_ROOT / "ShareViewController.swift"
SHARE_EXTENSION_STYLE = APP_ROOT / "Shared/ShareExtensionStyle.swift"


def test_ios_body_sans_family_uses_lato() -> None:
    source = DESIGN_TOKENS.read_text()

    assert 'static let sans = "Lato-Regular"' in source
    assert 'static let sansItalic = "Lato-Italic"' in source
    assert 'static let serif = "Lora-Regular"' in source
    assert 'static let serifItalic = "Lora-Italic"' in source


def test_lato_fonts_are_registered_for_app_and_share_extension() -> None:
    app_plist = APP_INFO_PLIST.read_text()
    share_extension_plist = SHARE_EXTENSION_INFO_PLIST.read_text()

    for filename in ("Lato.ttf", "Lato-Italic.ttf"):
        assert (APP_ROOT / "Fonts" / filename).is_file()
        assert f"<string>{filename}</string>" in app_plist

        assert (SHARE_EXTENSION_ROOT / "Fonts" / filename).is_file()
        assert f"<string>{filename}</string>" in share_extension_plist


def test_share_extension_body_family_matches_app_sans_family() -> None:
    controller_source = SHARE_VIEW_CONTROLLER.read_text()
    style_source = SHARE_EXTENSION_STYLE.read_text()

    assert 'static let bodyFamily = "Lato-Regular"' in style_source
    assert 'static let titleFamily = "Lora-Regular"' in style_source
    assert "ShareExtensionStyle.font(textStyle: .body)" in controller_source
    assert "ShareExtensionStyle.titleFont(textStyle: .headline)" in controller_source
