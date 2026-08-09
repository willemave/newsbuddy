from __future__ import annotations

import pytest

from scripts.select_ios_simulator import SimulatorSelectionError, select_ios_simulator


def _device(
    udid: str,
    name: str,
    *,
    device_type: str,
    state: str = "Shutdown",
    available: bool = True,
) -> dict[str, object]:
    return {
        "udid": udid,
        "name": name,
        "state": state,
        "isAvailable": available,
        "deviceTypeIdentifier": device_type,
    }


def _payload(*devices: dict[str, object]) -> dict[str, object]:
    return {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-26-4": list(devices)}}


IPHONE_TYPE = "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"
IPAD_TYPE = "com.apple.CoreSimulator.SimDeviceType.iPad-Pro-13-inch-M5-12GB"


def test_explicit_udid_and_name_resolve_available_iphones() -> None:
    available = _payload(
        _device("iphone-a", "iPhone 17 Pro", device_type=IPHONE_TYPE),
        _device("iphone-b", "Newsly Regression", device_type=IPHONE_TYPE),
    )

    assert (
        select_ios_simulator(
            booted_payload=_payload(),
            available_payload=available,
            explicit_udid="iphone-a",
        )
        == "iphone-a"
    )
    assert (
        select_ios_simulator(
            booted_payload=_payload(),
            available_payload=available,
            explicit_name="Newsly Regression",
        )
        == "iphone-b"
    )


def test_auto_selection_prefers_booted_iphone_over_booted_ipad() -> None:
    booted = _payload(
        _device("ipad", "iPad Pro", device_type=IPAD_TYPE, state="Booted"),
        _device("iphone", "Custom Phone", device_type=IPHONE_TYPE, state="Booted"),
    )

    assert (
        select_ios_simulator(
            booted_payload=booted,
            available_payload=booted,
        )
        == "iphone"
    )


def test_auto_selection_prefers_known_available_iphone_then_falls_back() -> None:
    preferred = _payload(
        _device("fallback", "Newsly Phone", device_type=IPHONE_TYPE),
        _device("preferred", "iPhone 17 Pro", device_type=IPHONE_TYPE),
    )
    fallback = _payload(_device("custom", "Newsly Phone", device_type=IPHONE_TYPE))

    assert (
        select_ios_simulator(
            booted_payload=_payload(),
            available_payload=preferred,
        )
        == "preferred"
    )
    assert (
        select_ios_simulator(
            booted_payload=_payload(),
            available_payload=fallback,
        )
        == "custom"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"explicit_udid": "missing"}, "UDID"),
        ({"explicit_name": "Missing Phone"}, "named"),
        ({}, "No available iPhone"),
    ],
)
def test_invalid_explicit_or_missing_iphone_fails(kwargs, message: str) -> None:
    with pytest.raises(SimulatorSelectionError, match=message):
        select_ios_simulator(
            booted_payload=_payload(),
            available_payload=_payload(_device("ipad", "iPad Pro", device_type=IPAD_TYPE)),
            **kwargs,
        )
