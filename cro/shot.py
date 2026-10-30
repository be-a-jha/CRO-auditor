"""Image helpers: palette sampling from a screenshot, slicing images for the vision model, cropping assets."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image

from .colors import delta_e, saturation, to_hex

Image.MAX_IMAGE_PIXELS = 200_000_000


def extract_palette(path: Path, max_colors: int = 12) -> list[dict]:
    """Flat UI colours dominate exact-pixel counts; photo pixels scatter. So count exact colours
    instead of clustering, which would let product photos swallow small but important colours like the CTA."""
    img = Image.open(path).convert("RGB")
    scale = min(1.0, 600 / img.width)
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.NEAREST)
    arr = np.asarray(img).astype(np.int32)  # exact pixels; JPEG noise is merged below by colour distance
    packed = (arr[..., 0] << 16) | (arr[..., 1] << 8) | arr[..., 2]
    vals, counts = np.unique(packed.ravel(), return_counts=True)
    order = np.argsort(-counts)
    total = packed.size
    merged: list[list] = []  # [rgb, count]
    for idx in order[:1500]:
        v, c = int(vals[idx]), int(counts[idx])
        if c / total < 0.0002:
            break
        rgb = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        for m in merged:
            if delta_e(m[0], rgb) < 6:
                m[1] += c
                break
        else:
            merged.append([rgb, c])
    keep = [m for m in merged if m[1] / total >= 0.0015 or (saturation(m[0]) > 0.35 and m[1] / total >= 0.0008)]
    keep.sort(key=lambda m: -m[1])
    return [{"hex": to_hex(rgb), "weight": round(c / total, 4)} for rgb, c in keep[:max_colors]]


def is_mobile_shot(path: Path) -> bool:
    with Image.open(path) as im:
        w, h = im.size
    # Phone captures: narrow CSS width, or native retina width (1080-1290px) with a tall aspect ratio.
    return w < 900 or (w <= 1300 and h / max(w, 1) > 1.9)


def chunks_for_model(path: Path, max_w: int = 1200, max_h: int = 2400, max_chunks: int = 4,
                     max_doc_h: int | None = None) -> list[dict]:
    """Return [{b64, media_type, y0, scale, w, h}] slices sized to stay inside the model's native resolution,
    so text stays legible and returned coordinates map back exactly."""
    img = Image.open(path).convert("RGB")
    if max_doc_h:
        img = img.crop((0, 0, img.width, min(img.height, max_doc_h)))
    scale = min(1.0, max_w / img.width)
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    out = []
    y = 0
    while y < img.height and len(out) < max_chunks:
        part = img.crop((0, y, img.width, min(img.height, y + max_h)))
        buf = io.BytesIO()
        part.save(buf, "JPEG", quality=85, optimize=True)
        out.append({"b64": base64.b64encode(buf.getvalue()).decode(), "media_type": "image/jpeg",
                    "y0": y / scale, "scale": scale, "w": part.width, "h": part.height})
        y += max_h
    return out


def crop_asset(path: Path, chunk: dict, bbox: list, dest: Path) -> Path | None:
    """bbox = [x, y, w, h] in the chunk's pixel space."""
    try:
        x, y, w, h = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None
    if w < 8 or h < 8:
        return None
    s = chunk["scale"]
    box = (int(x / s), int(chunk["y0"] + y / s), int((x + w) / s), int(chunk["y0"] + (y + h) / s))
    with Image.open(path) as im:
        im = im.convert("RGB")
        box = (max(0, box[0]), max(0, box[1]), min(im.width, box[2]), min(im.height, box[3]))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return None
        crop = im.crop(box)
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, "PNG" if dest.suffix == ".png" else "JPEG", quality=90)
    return dest


def b64_file(path: Path, max_w: int = 1200, max_h: int = 2400) -> dict:
    return chunks_for_model(path, max_w=max_w, max_h=max_h, max_chunks=1)[0]


def region_for_model(path: Path, y0: int, y1: int, width: int, quality: int = 80) -> dict | None:
    """One JPEG of rows y0..y1 (image px) resized to `width`, for the model."""
    img = Image.open(path).convert("RGB")
    y1 = min(y1, img.height)
    if y1 - y0 < 40:
        return None
    part = img.crop((0, y0, img.width, y1))
    s = min(1.0, width / part.width)
    if s < 1:
        part = part.resize((int(part.width * s), int(part.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    part.save(buf, "JPEG", quality=quality, optimize=True)
    return {"b64": base64.b64encode(buf.getvalue()).decode(), "media_type": "image/jpeg", "y0": y0, "scale": s,
            "w": part.width, "h": part.height}
