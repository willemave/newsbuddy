"""State-verifying AXe driver for Newsly iOS end-to-end tests.

AXe's HID commands only prove that an event was dispatched.  This helper makes
the post-action accessibility tree and screenshot part of every interaction so
tests cannot accidentally treat dispatch as a product-state assertion.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AxeStateExpectation:
    """Semantic state that must be visible after an AXe interaction."""

    ids: tuple[str, ...] = ()
    absent_ids: tuple[str, ...] = ()
    texts: tuple[str, ...] = ()
    id_values: Mapping[str, str] = field(default_factory=dict)
    enabled_ids: tuple[str, ...] = ()

    def describe(self) -> str:
        return (
            f"ids={self.ids!r} absent_ids={self.absent_ids!r} "
            f"texts={self.texts!r} id_values={dict(self.id_values)!r} "
            f"enabled_ids={self.enabled_ids!r}"
        )


@dataclass(frozen=True)
class AxeCapturedState:
    """Persisted AX tree and screenshot produced after an interaction."""

    tree: Any
    ui_path: Path
    screenshot_path: Path


class AxeHarnessError(AssertionError):
    """Raised when AXe dispatch or the required product transition fails."""


class AxeRunner:
    """Drive one explicit simulator while recording assertion evidence."""

    def __init__(
        self,
        *,
        axe_binary: str,
        udid: str,
        bundle_id: str,
        app_bundle_path: Path,
        artifact_dir: Path,
    ) -> None:
        self.axe_binary = axe_binary
        self.udid = udid
        self.bundle_id = bundle_id
        self.app_bundle_path = app_bundle_path
        self.artifact_dir = artifact_dir
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._capture_index = 0
        self._write_metadata()

    def install_clean(self) -> None:
        """Install the build with isolated app data and simulator credentials."""
        self._run(["xcrun", "simctl", "bootstatus", self.udid, "-b"])
        subprocess.run(
            ["xcrun", "simctl", "terminate", self.udid, self.bundle_id],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["xcrun", "simctl", "uninstall", self.udid, self.bundle_id],
            capture_output=True,
            text=True,
            check=False,
        )
        self._run(["xcrun", "simctl", "keychain", self.udid, "reset"])
        self._run(["xcrun", "simctl", "install", self.udid, str(self.app_bundle_path)])

    def launch(
        self,
        *,
        arguments: Mapping[str, str | int | bool],
        expectation: AxeStateExpectation,
        timeout_seconds: float = 15,
        name: str = "launch",
    ) -> AxeCapturedState:
        """Launch Newsly with E2E arguments and prove its initial product state."""
        command = [
            "xcrun",
            "simctl",
            "launch",
            "--terminate-running-process",
            self.udid,
            self.bundle_id,
        ]
        for key, raw_value in arguments.items():
            command.extend([f"-{key}", _launch_argument_value(raw_value)])
        self._run(command)
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
        )

    def open_url(
        self,
        url: str,
        *,
        expectation: AxeStateExpectation,
        name: str,
        timeout_seconds: float = 15,
    ) -> AxeCapturedState:
        """Open a URL on this simulator and prove the resulting visible state."""
        self._run(["xcrun", "simctl", "openurl", self.udid, url])
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
            accept_open_url_confirmation=True,
        )

    def tap_id(
        self,
        identifier: str,
        *,
        expectation: AxeStateExpectation,
        name: str,
        timeout_seconds: float = 10,
        action_wait_seconds: float = 5,
        pre_delay_seconds: float = 0.25,
        post_delay_seconds: float = 0.05,
    ) -> AxeCapturedState:
        """Tap by accessibility identifier, then capture and assert state."""
        self._run(
            [
                self.axe_binary,
                "tap",
                "--id",
                identifier,
                "--wait-timeout",
                str(action_wait_seconds),
                "--pre-delay",
                str(pre_delay_seconds),
                "--post-delay",
                str(post_delay_seconds),
                "--udid",
                self.udid,
            ]
        )
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
        )

    def tap_label(
        self,
        label: str,
        *,
        expectation: AxeStateExpectation,
        name: str,
        timeout_seconds: float = 10,
        action_wait_seconds: float = 5,
        pre_delay_seconds: float = 0.25,
        post_delay_seconds: float = 0.05,
        element_type: str | None = None,
    ) -> AxeCapturedState:
        """Tap an accessibility label when SwiftUI does not expose its identifier."""
        command = [
            self.axe_binary,
            "tap",
            "--label",
            label,
            "--wait-timeout",
            str(action_wait_seconds),
            "--pre-delay",
            str(pre_delay_seconds),
            "--post-delay",
            str(post_delay_seconds),
        ]
        if element_type is not None:
            command.extend(["--element-type", element_type])
        command.extend(["--udid", self.udid])
        self._run(command)
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
        )

    def tap_id_label(
        self,
        identifier: str,
        label: str,
        *,
        expectation: AxeStateExpectation,
        name: str,
        timeout_seconds: float = 10,
        pre_delay_seconds: float = 0.25,
        post_delay_seconds: float = 0.05,
        element_type: str = "Button",
    ) -> AxeCapturedState:
        """Tap one semantic match when SwiftUI flattens child identifiers.

        SwiftUI can propagate a container accessibility identifier to each of
        its children. AXe then correctly refuses an ambiguous ``--id`` or
        ``--label`` tap. Resolve the unique identifier/label/type tuple from a
        fresh tree and use its current center point instead.
        """
        matches = [
            node
            for node in _iter_nodes(self.describe_ui())
            if node.get("AXUniqueId") == identifier
            and node.get("AXLabel") == label
            and node.get("type") == element_type
        ]
        if len(matches) != 1:
            raise AxeHarnessError(
                "Expected exactly one AX element matching "
                f"id={identifier!r}, label={label!r}, type={element_type!r}; "
                f"found {len(matches)}"
            )
        frame = matches[0].get("frame")
        if not isinstance(frame, dict) or not all(
            isinstance(frame.get(key), (int, float)) for key in ("x", "y", "width", "height")
        ):
            raise AxeHarnessError(
                f"AX element matching id={identifier!r}, label={label!r} has no usable frame"
            )
        center_x = frame["x"] + frame["width"] / 2
        center_y = frame["y"] + frame["height"] / 2
        self._run(
            [
                self.axe_binary,
                "tap",
                "-x",
                str(center_x),
                "-y",
                str(center_y),
                "--pre-delay",
                str(pre_delay_seconds),
                "--post-delay",
                str(post_delay_seconds),
                "--udid",
                self.udid,
            ]
        )
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
        )

    def tap_point(
        self,
        x: float,
        y: float,
        *,
        expectation: AxeStateExpectation,
        name: str,
        timeout_seconds: float = 10,
        inspection_point: tuple[float, float] | None = None,
        inspection_points: tuple[tuple[float, float], ...] | None = None,
        pre_delay_seconds: float = 0.25,
        post_delay_seconds: float = 0.05,
    ) -> AxeCapturedState:
        """Tap a verified coordinate and capture either the full or point AX state.

        Coordinate dispatch is reserved for system/extension surfaces whose
        remote accessibility subtree is omitted from AXe's full-tree output.
        """
        if inspection_point is not None and inspection_points is not None:
            raise AxeHarnessError("Provide either inspection_point or inspection_points, not both")
        self._run(
            [
                self.axe_binary,
                "tap",
                "-x",
                str(x),
                "-y",
                str(y),
                "--pre-delay",
                str(pre_delay_seconds),
                "--post-delay",
                str(post_delay_seconds),
                "--udid",
                self.udid,
            ]
        )
        if inspection_points is not None:
            return self.capture_points_until(
                name=name,
                points=inspection_points,
                expectation=expectation,
                timeout_seconds=timeout_seconds,
            )
        if inspection_point is not None:
            return self.capture_point_until(
                name=name,
                x=inspection_point[0],
                y=inspection_point[1],
                expectation=expectation,
                timeout_seconds=timeout_seconds,
            )
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
        )

    def type_text(
        self,
        text: str,
        *,
        expectation: AxeStateExpectation,
        name: str,
        timeout_seconds: float = 10,
        inspection_point: tuple[float, float] | None = None,
        inspection_points: tuple[tuple[float, float], ...] | None = None,
    ) -> AxeCapturedState:
        """Type through AXe HID, then capture and assert the resulting value."""
        if inspection_point is not None and inspection_points is not None:
            raise AxeHarnessError("Provide either inspection_point or inspection_points, not both")
        self._run(
            [self.axe_binary, "type", "--stdin", "--udid", self.udid],
            input_text=text,
        )
        if inspection_points is not None:
            return self.capture_points_until(
                name=name,
                points=inspection_points,
                expectation=expectation,
                timeout_seconds=timeout_seconds,
            )
        if inspection_point is not None:
            return self.capture_point_until(
                name=name,
                x=inspection_point[0],
                y=inspection_point[1],
                expectation=expectation,
                timeout_seconds=timeout_seconds,
            )
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
        )

    def swipe_up(
        self,
        *,
        expectation: AxeStateExpectation,
        name: str,
        timeout_seconds: float = 5,
    ) -> AxeCapturedState:
        """Scroll the current app frame and prove it remained on the expected path."""
        start_x, start_y, end_x, end_y = _swipe_up_coordinates(self.describe_ui())
        self._run(
            [
                self.axe_binary,
                "swipe",
                "--start-x",
                str(start_x),
                "--start-y",
                str(start_y),
                "--end-x",
                str(end_x),
                "--end-y",
                str(end_y),
                "--duration",
                "0.35",
                "--post-delay",
                "0.1",
                "--udid",
                self.udid,
            ]
        )
        return self.capture_until(
            name=name,
            expectation=expectation,
            timeout_seconds=timeout_seconds,
        )

    def capture_until(
        self,
        *,
        name: str,
        expectation: AxeStateExpectation,
        timeout_seconds: float,
        poll_seconds: float = 0.15,
        accept_open_url_confirmation: bool = False,
    ) -> AxeCapturedState:
        """Poll fresh AX trees, persist the terminal tree/screenshot, and assert."""
        deadline = time.monotonic() + timeout_seconds
        last_tree: Any = []
        failures: list[str] = []

        while True:
            last_tree = self.describe_ui()
            if accept_open_url_confirmation and _is_system_open_confirmation(last_tree):
                self._run(
                    [
                        self.axe_binary,
                        "tap",
                        "--label",
                        "Open",
                        "--element-type",
                        "Button",
                        "--wait-timeout",
                        "2",
                        "--post-delay",
                        "0.05",
                        "--udid",
                        self.udid,
                    ]
                )
                continue
            failures = _expectation_failures(last_tree, expectation)
            if not failures or time.monotonic() >= deadline:
                break
            time.sleep(poll_seconds)

        captured = self._persist_capture(name=name, tree=last_tree)
        if failures:
            raise AxeHarnessError(
                f"AXe state did not satisfy {expectation.describe()} after {name}: "
                f"{'; '.join(failures)}. UI: {captured.ui_path}. "
                f"Screenshot: {captured.screenshot_path}"
            )
        return captured

    def capture_point_until(
        self,
        *,
        name: str,
        x: float,
        y: float,
        expectation: AxeStateExpectation,
        timeout_seconds: float,
        poll_seconds: float = 0.15,
    ) -> AxeCapturedState:
        """Poll one remote accessibility point and persist terminal evidence."""
        deadline = time.monotonic() + timeout_seconds
        last_tree: Any = {}
        failures: list[str] = []

        while True:
            try:
                last_tree = self.describe_ui(point=(x, y))
                failures = _expectation_failures(last_tree, expectation)
            except AxeHarnessError as exc:
                last_tree = {}
                failures = [str(exc)]
                if expectation.absent_ids and not (
                    expectation.ids
                    or expectation.texts
                    or expectation.id_values
                    or expectation.enabled_ids
                ):
                    failures = []
            if not failures or time.monotonic() >= deadline:
                break
            time.sleep(poll_seconds)

        captured = self._persist_capture(name=name, tree=last_tree)
        if failures:
            raise AxeHarnessError(
                f"AXe point state did not satisfy {expectation.describe()} after {name}: "
                f"{'; '.join(failures)}. UI: {captured.ui_path}. "
                f"Screenshot: {captured.screenshot_path}"
            )
        return captured

    def capture_points_until(
        self,
        *,
        name: str,
        points: tuple[tuple[float, float], ...],
        expectation: AxeStateExpectation,
        timeout_seconds: float,
        poll_seconds: float = 0.15,
    ) -> AxeCapturedState:
        """Poll candidate points for a remote surface whose layout can shift."""
        if not points:
            raise AxeHarnessError("At least one inspection point is required")

        deadline = time.monotonic() + timeout_seconds
        last_tree: Any = {}
        failures: list[str] = []

        while True:
            for point in points:
                try:
                    last_tree = self.describe_ui(point=point)
                    failures = _expectation_failures(last_tree, expectation)
                except AxeHarnessError as exc:
                    last_tree = {}
                    failures = [str(exc)]
                if not failures:
                    break
            if not failures or time.monotonic() >= deadline:
                break
            time.sleep(poll_seconds)

        captured = self._persist_capture(name=name, tree=last_tree)
        if failures:
            raise AxeHarnessError(
                f"AXe candidate-point state did not satisfy {expectation.describe()} after "
                f"{name}: {'; '.join(failures)}. UI: {captured.ui_path}. "
                f"Screenshot: {captured.screenshot_path}"
            )
        return captured

    def describe_ui(self, *, point: tuple[float, float] | None = None) -> Any:
        command = [self.axe_binary, "describe-ui"]
        if point is not None:
            command.extend(["--point", f"{point[0]},{point[1]}"])
        command.extend(["--udid", self.udid])
        result = self._run(command)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AxeHarnessError(
                f"AXe returned invalid describe-ui JSON: {result.stdout[:500]}"
            ) from exc

    def _persist_capture(self, *, name: str, tree: Any) -> AxeCapturedState:
        self._capture_index += 1
        safe_name = (
            "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in name
            ).strip("_")
            or "state"
        )
        stem = f"{self._capture_index:03d}_{safe_name}"
        ui_path = self.artifact_dir / f"{stem}.json"
        screenshot_path = self.artifact_dir / f"{stem}.png"
        ui_path.write_text(json.dumps(tree, indent=2), encoding="utf-8")
        self._run(
            [
                self.axe_binary,
                "screenshot",
                "--udid",
                self.udid,
                "--output",
                str(screenshot_path),
            ]
        )
        return AxeCapturedState(
            tree=tree,
            ui_path=ui_path,
            screenshot_path=screenshot_path,
        )

    def _write_metadata(self) -> None:
        version = self._run([self.axe_binary, "--version"]).stdout.strip()
        metadata = {
            "axe_version": version,
            "simulator_udid": self.udid,
            "bundle_id": self.bundle_id,
            "app_bundle_path": str(self.app_bundle_path),
        }
        (self.artifact_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _run(
        command: list[str],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AxeHarnessError(
                f"Command failed: {command!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result


def tree_has_id(tree: Any, identifier: str) -> bool:
    return any(node.get("AXUniqueId") == identifier for node in _iter_nodes(tree))


def tree_text(tree: Any) -> str:
    values: list[str] = []
    for node in _iter_nodes(tree):
        for key in ("AXLabel", "AXValue", "title", "help"):
            value = node.get(key)
            if isinstance(value, str) and value:
                values.append(value)
    return "\n".join(values)


def element_center(tree: Any, identifier: str) -> tuple[float, float]:
    """Return the center of one identified accessibility element."""
    matches = [node for node in _iter_nodes(tree) if node.get("AXUniqueId") == identifier]
    if len(matches) != 1:
        raise AxeHarnessError(
            f"Expected exactly one AX element with id={identifier!r}; found {len(matches)}"
        )
    frame = matches[0].get("frame")
    if not isinstance(frame, dict) or not all(
        isinstance(frame.get(key), (int, float)) for key in ("x", "y", "width", "height")
    ):
        raise AxeHarnessError(f"AX element with id={identifier!r} has no usable frame")
    return (
        float(frame["x"]) + float(frame["width"]) / 2,
        float(frame["y"]) + float(frame["height"]) / 2,
    )


def _is_system_open_confirmation(tree: Any) -> bool:
    """Return whether iOS is asking to open a custom URL in Newsbuddy."""
    nodes = list(_iter_nodes(tree))
    has_prompt = any(
        node.get("type") == "StaticText"
        and isinstance(node.get("AXLabel"), str)
        and node["AXLabel"].startswith("Open in “Newsbuddy”?")
        for node in nodes
    )
    has_open_button = any(
        node.get("type") == "Button"
        and node.get("AXLabel") == "Open"
        and node.get("enabled") is True
        for node in nodes
    )
    return has_prompt and has_open_button


def _swipe_up_coordinates(tree: Any) -> tuple[float, float, float, float]:
    """Return a stable vertical swipe scaled to the visible application frame."""
    for node in _iter_nodes(tree):
        if node.get("type") != "Application":
            continue
        frame = node.get("frame")
        if not isinstance(frame, dict) or not all(
            isinstance(frame.get(key), (int, float)) for key in ("x", "y", "width", "height")
        ):
            continue
        x = float(frame["x"])
        y = float(frame["y"])
        width = float(frame["width"])
        height = float(frame["height"])
        if width <= 0 or height <= 0:
            continue
        center_x = x + width * 0.5
        return (
            round(center_x, 3),
            round(y + height * 0.8, 3),
            round(center_x, 3),
            round(y + height * 0.25, 3),
        )
    raise AxeHarnessError("AX tree has no usable Application frame for swipe geometry")


def _expectation_failures(
    tree: Any,
    expectation: AxeStateExpectation,
) -> list[str]:
    nodes = list(_iter_nodes(tree))
    nodes_by_id: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        identifier = node.get("AXUniqueId")
        if isinstance(identifier, str):
            nodes_by_id.setdefault(identifier, []).append(node)

    failures: list[str] = []
    for identifier in expectation.ids:
        if identifier not in nodes_by_id:
            failures.append(f"missing id {identifier!r}")
    for identifier in expectation.absent_ids:
        if identifier in nodes_by_id:
            failures.append(f"unexpected id {identifier!r}")

    rendered_text = tree_text(tree)
    for text in expectation.texts:
        if text not in rendered_text:
            failures.append(f"missing text {text!r}")

    for identifier, expected_value in expectation.id_values.items():
        values = [str(node.get("AXValue") or "") for node in nodes_by_id.get(identifier, [])]
        if not any(expected_value in value for value in values):
            failures.append(
                f"id {identifier!r} lacks value containing {expected_value!r}; values={values!r}"
            )

    for identifier in expectation.enabled_ids:
        matches = nodes_by_id.get(identifier, [])
        if not matches or not any(node.get("enabled") is True for node in matches):
            failures.append(f"id {identifier!r} is missing or disabled")
    return failures


def _iter_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nodes(child)


def _launch_argument_value(value: str | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
