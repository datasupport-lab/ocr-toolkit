"""
preprocess.py — OpenCV preprocessing for Tesseract OCR.

Preprocessing "modes":
  * auto        -> inspect the image and pick the best of the modes below
  * document    -> clean scans / printed pages (default manual)
  * photo       -> phone photos / uneven lighting
  * lowcontrast -> faint text (Sauvola threshold)
  * inverted    -> light text on a dark background
  * none        -> pass through unchanged

detect_mode(img) returns the auto-chosen mode name so the UI/CLI can show it.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MODES = ["auto", "document", "photo", "lowcontrast", "inverted", "none"]


@dataclass
class PreConfig:
    mode: str = "auto"
    grayscale: bool = True
    upscale_min_height: int = 1000
    denoise: bool = True
    deskew: bool = True
    threshold: str = "adaptive"
    remove_borders: bool = True


# ===========================================================================
# AUTO mode detection — heuristic, fast, no ML
# ===========================================================================
def detect_mode(img: np.ndarray) -> str:
    """Inspect an image and return the best preprocessing mode name.

    Heuristics (in priority order):
      1. inverted    : page is mostly dark (light text on dark bg)
      2. lowcontrast : narrow intensity spread (faint / washed-out text)
      3. photo       : colorful and/or noisy (phone photo, uneven lighting)
      4. document    : everything else (clean scan / printed page)
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    mean = float(gray.mean())
    std = float(gray.std())

    # 1) dark background dominant -> inverted
    if mean < 110:
        return "inverted"

    # 2) photo -> meaningful COLOR is the reliable signal (scans are near-gray).
    #    Checked before contrast so a colorful photo of a faint page still maps
    #    to 'photo'. We deliberately avoid edge/Laplacian variance, because crisp
    #    printed text also produces high edge variance and would look like noise.
    if img.ndim == 3:
        b, g, r = cv2.split(img.astype(np.int16))
        chroma = (np.abs(b - g).mean() + np.abs(g - r).mean() + np.abs(b - r).mean()) / 3.0
        if chroma > 12:            # colorful -> a photo, not a B/W scan
            return "photo"

    # 3) low contrast -> lowcontrast. Reliable signal is the intensity GAP
    #    between text (foreground) and background via an Otsu split. Normal docs
    #    are mostly white with near-black text (large gap); faint scans small.
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fg = gray[gray < t]; bg = gray[gray >= t]
    if fg.size > 20 and bg.size > 20:
        gap = float(bg.mean()) - float(fg.mean())
        if gap < 90:
            return "lowcontrast"
    elif std < 25:
        return "lowcontrast"

    # 4) default
    return "document"


def _percentile_from_hist(hist: np.ndarray, q: float) -> int:
    total = hist.sum() or 1
    target = total * q
    cum = 0.0
    for i, v in enumerate(hist):
        cum += v
        if cum >= target:
            return i
    return 255


# ===========================================================================
# main entry
# ===========================================================================
def preprocess(img: np.ndarray, cfg: PreConfig) -> np.ndarray:
    mode = cfg.mode
    if mode == "auto":
        mode = detect_mode(img)
    if mode == "none":
        return img
    if mode == "inverted":
        return _invert_if_dark(_gray(img), force=True)
    if mode == "photo":
        cfg = PreConfig(threshold="adaptive")
    elif mode == "lowcontrast":
        cfg = PreConfig(threshold="sauvola")
    else:  # document (or fallback)
        cfg = PreConfig(threshold="adaptive")
    return _document(img, cfg)


def _document(img, cfg):
    out = _gray(img) if cfg.grayscale else img
    out = _invert_if_dark(out)
    out = _upscale(out, cfg.upscale_min_height)
    if cfg.denoise:
        out = cv2.fastNlMeansDenoising(out, h=10, templateWindowSize=7, searchWindowSize=21)
    if cfg.deskew:
        out = _deskew(out)
    if cfg.threshold == "adaptive":
        out = cv2.adaptiveThreshold(out, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 31, 15)
    elif cfg.threshold == "otsu":
        _, out = cv2.threshold(out, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif cfg.threshold == "sauvola":
        out = _sauvola(out)
    if cfg.remove_borders and cfg.threshold != "none":
        out = _remove_borders(out)
    return out


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img

def _invert_if_dark(gray, force=False):
    if force or gray.mean() < 110:
        return cv2.bitwise_not(gray)
    return gray

def _upscale(img, min_h):
    h = img.shape[0]
    if 0 < h < min_h:
        s = min_h / h
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    return img

def _deskew(img):
    thr = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thr > 0))
    if coords.shape[0] < 50:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    if angle > 45:
        angle -= 90
    if abs(angle) < 0.3 or abs(angle) > 45:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)

def _remove_borders(img):
    cnts, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return img
    c = max(cnts, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(c)
    if w < img.shape[1] * 0.5 or h < img.shape[0] * 0.5:
        return img
    return img[y:y + h, x:x + w]

def _sauvola(gray, window=25, k=0.2):
    g = gray.astype(np.float32)
    mean = cv2.boxFilter(g, cv2.CV_32F, (window, window))
    sq = cv2.boxFilter(g * g, cv2.CV_32F, (window, window))
    std = cv2.sqrt(cv2.max(sq - mean * mean, 0))
    thresh = mean * (1 + k * ((std / 128.0) - 1))
    return np.where(g > thresh, 255, 0).astype(np.uint8)
