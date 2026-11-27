"""Screen capture and OpenCV template matching for image-based control localization."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

import cv2
import mss
import numpy as np

from autotool.models.schema import Rect


def capture_screen_bgr(region: Rect | None = None) -> tuple[np.ndarray, int, int]:
    """Capture a physical screen region (or full virtual desktop) as a BGR numpy array.

    Returns `(bgr_image, origin_x, origin_y)`.
    """
    with mss.mss() as sct:
        if region is not None and region.width > 0 and region.height > 0:
            monitor = {
                "left": int(region.left),
                "top": int(region.top),
                "width": int(region.width),
                "height": int(region.height),
            }
        else:
            monitor = sct.monitors[0]
        raw = sct.grab(monitor)
        bgra = np.asarray(raw, dtype=np.uint8)
        bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        return bgr, int(monitor["left"]), int(monitor["top"])


def capture_region_png_bytes(region: Rect) -> bytes:
    """Capture a physical screen rectangle and encode it as PNG bytes."""
    bgr, _, _ = capture_screen_bgr(region)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError("截图编码 PNG 失败")
    return buf.tobytes()


def encode_png_b64(png_bytes: bytes) -> str:
    return base64.b64encode(png_bytes).decode("ascii")


def decode_png_b64(b64_str: str) -> np.ndarray:
    """Decode a base64-encoded PNG/image string into a BGR numpy array."""
    raw = base64.b64decode(b64_str)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码模板图片数据")
    return img


def load_template_bgr(b64_str: str = "", file_path: str = "") -> np.ndarray:
    """Load a template BGR image from base64 data or a local file path."""
    if b64_str:
        return decode_png_b64(b64_str)
    if file_path:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"找不到模板图片文件: {file_path}")
        raw = p.read_bytes()
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"无法读取模板图片文件: {file_path}")
        return img
    raise ValueError("未提供模板图片 (template_image_b64 或 template_image_path)")


def match_template_in_image(
    haystack_bgr: np.ndarray,
    template_bgr: np.ndarray,
    threshold: float = 0.85,
    origin_x: int = 0,
    origin_y: int = 0,
    grayscale: bool = False,
) -> Optional[tuple[Rect, float]]:
    """Find `template_bgr` inside `haystack_bgr` using normalized cross-correlation.

    Returns `(matched_physical_rect, similarity_0_to_1)` if best match >= `threshold`, else None.
    """
    th, tw = template_bgr.shape[:2]
    hh, hw = haystack_bgr.shape[:2]
    if th <= 0 or tw <= 0 or th > hh or tw > hw:
        return None

    if grayscale:
        hay = cv2.cvtColor(haystack_bgr, cv2.COLOR_BGR2GRAY)
        tpl = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    else:
        hay = haystack_bgr
        tpl = template_bgr

    res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    score = float(max_val)
    if score < float(threshold):
        return None

    left = origin_x + int(max_loc[0])
    top = origin_y + int(max_loc[1])
    return Rect(left=left, top=top, right=left + int(tw), bottom=top + int(th)), score


def find_all_templates_in_image(
    haystack_bgr: np.ndarray,
    template_bgr: np.ndarray,
    threshold: float = 0.85,
    origin_x: int = 0,
    origin_y: int = 0,
    grayscale: bool = False,
    max_results: int = 200,
) -> list[tuple[Rect, float]]:
    """Find ALL distinct occurrences of `template_bgr` in `haystack_bgr` with similarity >= `threshold`.

    Uses greedy peak-suppression (NMS) so each distinct visual occurrence is reported once,
    sorted by similarity descending.
    """
    th, tw = template_bgr.shape[:2]
    hh, hw = haystack_bgr.shape[:2]
    if th <= 0 or tw <= 0 or th > hh or tw > hw:
        return []

    if grayscale:
        hay = cv2.cvtColor(haystack_bgr, cv2.COLOR_BGR2GRAY)
        tpl = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    else:
        hay = haystack_bgr
        tpl = template_bgr

    res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
    work = res.copy()
    rh, rw = work.shape[:2]
    sup_x = max(1, tw // 2)
    sup_y = max(1, th // 2)

    matches: list[tuple[Rect, float]] = []
    thresh = float(threshold)
    while len(matches) < max_results:
        _, max_val, _, max_loc = cv2.minMaxLoc(work)
        score = float(max_val)
        if score < thresh:
            break
        mx, my = int(max_loc[0]), int(max_loc[1])
        left = origin_x + mx
        top = origin_y + my
        matches.append((Rect(left=left, top=top, right=left + int(tw), bottom=top + int(th)), score))

        # Suppress overlapping neighborhood around this peak
        x1 = max(0, mx - sup_x)
        y1 = max(0, my - sup_y)
        x2 = min(rw, mx + sup_x + 1)
        y2 = min(rh, my + sup_y + 1)
        work[y1:y2, x1:x2] = -1.0

    return matches


def find_template_on_screen(
    template_bgr: np.ndarray,
    threshold: float = 0.85,
    search_rect: Rect | None = None,
    grayscale: bool = False,
) -> Optional[tuple[Rect, float]]:
    """Capture the screen (or `search_rect`) and match `template_bgr`."""
    haystack_bgr, ox, oy = capture_screen_bgr(search_rect)
    return match_template_in_image(
        haystack_bgr=haystack_bgr,
        template_bgr=template_bgr,
        threshold=threshold,
        origin_x=ox,
        origin_y=oy,
        grayscale=grayscale,
    )


def find_all_templates_on_screen(
    template_bgr: np.ndarray,
    threshold: float = 0.85,
    search_rect: Rect | None = None,
    grayscale: bool = False,
    max_results: int = 200,
) -> list[tuple[Rect, float]]:
    """Capture the screen (or `search_rect`) and return ALL distinct occurrences >= `threshold`."""
    haystack_bgr, ox, oy = capture_screen_bgr(search_rect)
    return find_all_templates_in_image(
        haystack_bgr=haystack_bgr,
        template_bgr=template_bgr,
        threshold=threshold,
        origin_x=ox,
        origin_y=oy,
        grayscale=grayscale,
        max_results=max_results,
    )
