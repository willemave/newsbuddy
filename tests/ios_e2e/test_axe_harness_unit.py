from __future__ import annotations

import pytest

from tests.ios_e2e.axe_harness import AxeHarnessError, _swipe_up_coordinates


def test_swipe_up_coordinates_scale_to_phone_application_frame() -> None:
    tree = [
        {
            "type": "Application",
            "frame": {"x": 0, "y": 0, "width": 402, "height": 874},
        }
    ]

    assert _swipe_up_coordinates(tree) == (201.0, 699.2, 201.0, 218.5)


def test_swipe_up_coordinates_honor_offset_application_frame() -> None:
    tree = {
        "children": [
            {
                "type": "Application",
                "frame": {"x": 10, "y": 20, "width": 430, "height": 932},
            }
        ]
    }

    assert _swipe_up_coordinates(tree) == (225.0, 765.6, 225.0, 253.0)


@pytest.mark.parametrize(
    "tree",
    [
        [],
        [{"type": "Application", "frame": {"x": 0, "y": 0, "width": 0, "height": 874}}],
        [{"type": "Application", "frame": {"x": 0, "y": "0", "width": 402, "height": 874}}],
    ],
)
def test_swipe_up_coordinates_reject_missing_or_invalid_application_frame(tree: object) -> None:
    with pytest.raises(AxeHarnessError, match="no usable Application frame"):
        _swipe_up_coordinates(tree)
