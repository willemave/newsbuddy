#!/usr/bin/env python3
"""Select one available iPhone simulator for Newsly UI harnesses."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Iterable, Mapping
from typing import Any

PREFERRED_IPHONE_NAMES = (
    "iPhone 17 Pro",
    "iPhone 17",
    "iPhone 16 Pro",
    "iPhone 16",
    "iPhone 15 Pro",
    "iPhone 15",
)


class SimulatorSelectionError(RuntimeError):
    """Raised when an explicit or automatic iPhone target cannot be resolved."""


def _devices(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    runtime_devices = payload.get("devices")
    if not isinstance(runtime_devices, Mapping):
        return result
    for candidates in runtime_devices.values():
        if not isinstance(candidates, list):
            continue
        result.extend(candidate for candidate in candidates if isinstance(candidate, Mapping))
    return result


def _is_available_iphone(device: Mapping[str, Any]) -> bool:
    device_type = device.get("deviceTypeIdentifier")
    return (
        device.get("isAvailable", True) is not False
        and isinstance(device_type, str)
        and ".SimDeviceType.iPhone-" in device_type
    )


def _udid(device: Mapping[str, Any]) -> str | None:
    value = device.get("udid")
    return value if isinstance(value, str) and value else None


def select_ios_simulator(
    *,
    booted_payload: Mapping[str, Any],
    available_payload: Mapping[str, Any],
    explicit_udid: str | None = None,
    explicit_name: str | None = None,
) -> str:
    """Resolve an explicit iPhone or choose a booted/preferred available one."""
    if explicit_udid and explicit_name:
        raise SimulatorSelectionError("Specify a simulator UDID or name, not both")

    booted = [device for device in _devices(booted_payload) if _is_available_iphone(device)]
    available = [device for device in _devices(available_payload) if _is_available_iphone(device)]
    all_devices = [*booted, *available]

    if explicit_udid:
        for device in all_devices:
            if _udid(device) == explicit_udid:
                return explicit_udid
        raise SimulatorSelectionError(
            f"No available iPhone simulator with UDID {explicit_udid!r} found"
        )

    if explicit_name:
        for device in all_devices:
            if device.get("name") == explicit_name and (resolved := _udid(device)):
                return resolved
        raise SimulatorSelectionError(
            f"No available iPhone simulator named {explicit_name!r} found"
        )

    for device in booted:
        if device.get("state") == "Booted" and (resolved := _udid(device)):
            return resolved

    for preferred_name in PREFERRED_IPHONE_NAMES:
        for device in available:
            if device.get("name") == preferred_name and (resolved := _udid(device)):
                return resolved

    for device in available:
        if resolved := _udid(device):
            return resolved

    raise SimulatorSelectionError("No available iPhone simulator found")


def _load_simctl_devices(kind: str) -> dict[str, Any]:
    output = subprocess.check_output(
        ["xcrun", "simctl", "list", "devices", kind, "-j"],
        text=True,
    )
    parsed = json.loads(output)
    if not isinstance(parsed, dict):
        raise SimulatorSelectionError("simctl returned an invalid device payload")
    return parsed


def select_live_ios_simulator(
    *,
    explicit_udid: str | None = None,
    explicit_name: str | None = None,
) -> str:
    """Select against the Mac's current CoreSimulator inventory."""
    return select_ios_simulator(
        booted_payload=_load_simctl_devices("booted"),
        available_payload=_load_simctl_devices("available"),
        explicit_udid=explicit_udid,
        explicit_name=explicit_name,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--udid", help="Require this available iPhone simulator UDID")
    selector.add_argument("--name", help="Require this available iPhone simulator name")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(
            select_live_ios_simulator(
                explicit_udid=args.udid,
                explicit_name=args.name,
            )
        )
    except (SimulatorSelectionError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
