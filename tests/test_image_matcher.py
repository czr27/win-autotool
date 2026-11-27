"""Unit tests for OpenCV template matching and image_template locator resolution."""

import cv2
import numpy as np

from autotool.core.image_matcher import (
    decode_png_b64,
    encode_png_b64,
    find_all_templates_in_image,
    match_template_in_image,
)
from autotool.core.resolver import ControlResolver
from autotool.models.schema import LocatorStrategy, Rect


def _make_synthetic_screen() -> tuple[np.ndarray, np.ndarray]:
    """Create a 320x240 synthetic BGR screen with a distinct 40x30 target patch at (120, 80)."""
    rng = np.random.default_rng(42)
    screen = rng.integers(10, 40, size=(240, 320, 3), dtype=np.uint8)
    # Draw a high-contrast colored icon at (x=120..160, y=80..110)
    patch = np.zeros((30, 40, 3), dtype=np.uint8)
    cv2.rectangle(patch, (2, 2), (37, 27), (255, 180, 40), -1)
    cv2.circle(patch, (20, 15), 8, (40, 60, 255), -1)
    screen[80:110, 120:160] = patch
    return screen, patch


def test_match_template_exact_and_threshold_rejection() -> None:
    screen, patch = _make_synthetic_screen()

    hit = match_template_in_image(screen, patch, threshold=0.95, origin_x=10, origin_y=20)
    assert hit is not None
    rect, score = hit
    assert score >= 0.99
    assert rect == Rect(left=130, top=100, right=170, bottom=130)

    # Grayscale mode should also match accurately
    hit_gray = match_template_in_image(screen, patch, threshold=0.95, grayscale=True)
    assert hit_gray is not None
    assert hit_gray[1] >= 0.99

    # Unrelated blank template with high threshold should fail or mismatch
    wrong_tpl = np.full((30, 40, 3), 250, dtype=np.uint8)
    cv2.line(wrong_tpl, (0, 0), (39, 29), (0, 0, 0), 3)
    miss = match_template_in_image(screen, wrong_tpl, threshold=0.90)
    assert miss is None


def test_base64_png_roundtrip_and_resolver_image_template(monkeypatch) -> None:
    screen, patch = _make_synthetic_screen()
    ok, buf = cv2.imencode(".png", patch)
    assert ok
    b64 = encode_png_b64(buf.tobytes())
    decoded = decode_png_b64(b64)
    assert decoded.shape == patch.shape

    # Patch capture_screen_bgr to return our synthetic screen
    from autotool.core import image_matcher

    monkeypatch.setattr(image_matcher, "capture_screen_bgr", lambda region=None: (screen, 0, 0))

    strategy = LocatorStrategy(
        strategy_type="image_template",
        template_image_b64=b64,
        similarity_threshold=0.90,
        anchor_mode="center",
        timeout_ms=500,
    )
    resolved = ControlResolver().resolve(strategy, activate=False)
    assert resolved.control_type == "ImageTemplate"
    assert resolved.match_score is not None and resolved.match_score >= 0.99
    assert resolved.rect == Rect(left=120, top=80, right=160, bottom=110)
    assert (resolved.target_x, resolved.target_y) == (140, 95)


def test_find_all_templates_and_diagnose_detects_non_unique_images(monkeypatch) -> None:
    screen, patch = _make_synthetic_screen()
    # Place 2 additional identical copies at (20, 20) and (240, 170) -> total 3 copies
    screen[20:50, 20:60] = patch
    screen[170:200, 240:280] = patch

    all_hits = find_all_templates_in_image(screen, patch, threshold=0.95)
    assert len(all_hits) == 3

    ok, buf = cv2.imencode(".png", patch)
    assert ok
    b64 = encode_png_b64(buf.tobytes())

    from autotool.core import image_matcher

    monkeypatch.setattr(image_matcher, "capture_screen_bgr", lambda region=None: (screen, 0, 0))

    strat = LocatorStrategy(
        strategy_type="image_template",
        template_image_b64=b64,
        similarity_threshold=0.90,
        search_in_window=False,
    )
    diag = ControlResolver().diagnose(strat)
    assert len(diag.control_matches) == 3
    report = diag.format_report()
    assert "符合 3 个" in report
    assert "不唯一" in report
