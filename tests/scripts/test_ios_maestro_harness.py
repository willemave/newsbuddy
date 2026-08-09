"""Source contracts for the combined Maestro and AXe simulator harness."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "tests" / "scripts" / "ios_maestro.sh"


def test_ios_harness_uses_its_current_build_for_axe() -> None:
    """AXe must not silently reinstall an older app after the harness builds Newsly."""
    source = HARNESS.read_text(encoding="utf-8")

    assert 'export NEWSLY_AXE_APP_PATH="${NEWSLY_AXE_APP_PATH:-$APP_PATH}"' in source
    assert 'export NEWSLY_AXE_SIMULATOR_ID="${NEWSLY_AXE_SIMULATOR_ID:-$SIMULATOR_ID}"' in source


def test_ios_harness_uses_shared_iphone_selector() -> None:
    source = HARNESS.read_text(encoding="utf-8")

    assert "scripts/select_ios_simulator.py" in source
    assert "simctl list" not in source
