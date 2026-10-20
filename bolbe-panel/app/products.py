"""Ürün görsel kütüphanesi: markanın gerçek ürünlerinin temiz görselleri ve görsel kimlik kartları.

Görsel ve video ajanları ürün görünen her üretimde bu görselleri Virale'ye referans olarak verir; böylece
ürün tasarımı uydurulmaz. Virale görselleri indirebilsin diye kütüphane görselleri imzalı, süreli ve
herkese açık bir bağlantıyla (/pub/...) sunulur.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw

from . import storage

STATE = "products"
LINK_DAYS = 7
MIN_SIDE = 120          # bundan küçük PDF görselleri (ikon, logo parçası) atlanır
MAX_SIDE = 2000         # kütüphane görselleri bu boyuta küçültülür


def public_base() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not base and os.getenv("RAILWAY_PUBLIC_DOMAIN"):
        base = "https://" + os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    return base


def _secret() -> bytes:
    return os.getenv("SESSION_SECRET", "").encode()


def sign(name: str, expires: int) -> str:
    return hmac.new(_secret(), f"pub|{name}|{expires}".encode(), hashlib.sha256).hexdigest()[:32]


def public_url(name: str) -> str | None:
    """Virale gibi dış servislerin indirebileceği süreli bağlantı; panel adresi bilinmiyorsa None."""
    base = public_base()
    if not base:
        return None
    expires = int(time.time()) + LINK_DAYS * 86400
    return f"{base}/pub/{expires}.{sign(name, expires)}/{name}"


def verify(token: str, name: str) -> bool:
    expires, _, sig = token.partition(".")
    return (expires.isdigit() and int(expires) > time.time()
            and hmac.compare_digest(sig, sign(name, int(expires))))


# ---------------------------------------------------------------- kayıtlar


def all_products() -> list[dict[str, Any]]:
    return storage.read_state(STATE, [])


def get(product_id: str) -> dict[str, Any] | None:
    return next((p for p in all_products() if p["id"] == product_id), None)


FIELDS = ("name", "category", "size", "price", "colors", "packaging", "label_text", "key_ingredients", "description")


async def save(data: dict[str, Any], by: str) -> dict[str, Any]:
    """Ürünü kimliğine (yoksa adına) göre bulup günceller; yoksa yeni ürün açar."""
    async with storage.state_lock:
        items = storage.read_state(STATE, [])
        pid = data.get("id") or ""
        item = next((p for p in items if p["id"] == pid), None) or next(
            (p for p in items if storage.slug(p["name"]) == storage.slug(data.get("name", ""))), None)
        if item is None:
            if not data.get("name"):
                raise ValueError("Yeni ürün için ad gerekli")
            item = {"id": uuid.uuid4().hex[:8], "images": [], "primary": "", "created": storage.now()}
            items.append(item)
        for key in FIELDS:
            if key in data and data[key] not in (None, "", []):
                item[key] = data[key]
        item["updated"], item["updated_by"] = storage.now(), by
        storage.write_state(STATE, items)
        return item


async def add_image(product_id: str, media_name: str, primary: bool = False) -> dict[str, Any]:
    async with storage.state_lock:
        items = storage.read_state(STATE, [])
        item = next((p for p in items if p["id"] == product_id), None)
        if item is None:
            raise ValueError(f"Ürün bulunamadı: {product_id}")
        if media_name not in item["images"]:
            item["images"].append(media_name)
        if primary or not item.get("primary"):
            item["primary"] = media_name
        item["updated"] = storage.now()
        storage.write_state(STATE, items)
        return item


async def delete(product_id: str) -> bool:
    async with storage.state_lock:
        items = storage.read_state(STATE, [])
        kept = [p for p in items if p["id"] != product_id]
        storage.write_state(STATE, kept)
    return len(kept) != len(items)


def summary_for_prompt(limit: int = 30) -> str:
    rows = []
    for p in all_products()[:limit]:
        bits = [p["name"], p.get("category", ""), p.get("size", ""), ", ".join(p.get("colors", []) or [])]
        rows.append(f"- {p['id']}: " + " · ".join(b for b in bits if b)
                    + (f" (referans görsel: {p['primary']})" if p.get("primary") else " (görsel yok)"))
    return "\n".join(rows)


def reference(product_id: str) -> dict[str, Any]:
    p = get(product_id)
    if not p:
        raise ValueError(f"Ürün bulunamadı: {product_id}")
    names = ([p["primary"]] if p.get("primary") else []) + [i for i in p.get("images", []) if i != p.get("primary")]
    urls = [u for u in (public_url(n) for n in names[:6]) if u]
    return {**{k: p.get(k) for k in ("id", *FIELDS)}, "reference_images": names[:6], "reference_urls": urls,
            "not": ("Bu bağlantıları Virale'de images / referenceImages (role: subject) olarak ver."
                    if urls else "Panelin herkese açık adresi bilinmiyor (PUBLIC_BASE_URL); referans bağlantısı üretilemedi.")}


# ---------------------------------------------------------------- görsel işleme


def _fit(img: Image.Image) -> Image.Image:
    if max(img.size) > MAX_SIDE:
        img = img.copy()
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def remove_background(img: Image.Image, tolerance: int = 28) -> Image.Image:
    """Düz/açık zeminli ürün fotoğraflarında zemini kenarlardan doldurarak şeffaf yapar.

    Zemin karmaşıksa (sahne, doku) neredeyse hiçbir şey silinmez ve görsel olduğu gibi kalır.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    marker = (1, 254, 3)
    work = rgb.copy()
    step = max(1, min(w, h) // 40)
    seeds = [(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
    seeds += [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)]
    for xy in seeds:
        if work.getpixel(xy) != marker:
            ImageDraw.floodfill(work, xy, marker, thresh=tolerance)
    # zemin (işaret rengi) = 0, ürün = 255
    alpha = ImageChops.difference(work, Image.new("RGB", (w, h), marker)).convert("L").point(lambda v: 255 if v else 0)
    removed = alpha.histogram()[0] / (w * h)
    if removed < 0.03 or removed > 0.97:
        return img.convert("RGBA")   # zemin bulunamadı veya her şey silinecekti: dokunma
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    bbox = alpha.getbbox()
    return out.crop(bbox) if bbox else out


def dominant_colors(img: Image.Image, count: int = 4) -> list[str]:
    rgba = img.convert("RGBA")
    rgba.thumbnail((160, 160))
    pixels = [p[:3] for p in rgba.getdata() if p[3] > 200]
    if not pixels:
        return []
    sample = Image.new("RGB", (len(pixels), 1))
    sample.putdata(pixels)
    pal = sample.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    palette = pal.getpalette() or []
    counts = sorted(pal.getcolors() or [], reverse=True)
    return [f"#{palette[i * 3]:02X}{palette[i * 3 + 1]:02X}{palette[i * 3 + 2]:02X}" for _, i in counts[:count]]


def extract_pdf_images(name: str) -> list[str]:
    """Yüklenen PDF katalogdaki gömülü görselleri uploads alanına ayrı dosyalar olarak çıkarır."""
    from pypdf import PdfReader

    path = storage.area_file("uploads", name)
    if path.suffix.lower() != ".pdf":
        raise HTTPException(400, "Yalnız PDF dosyalarından görsel çıkarılabilir")
    saved: list[str] = []
    stem = storage.slug(path.stem)
    for page_no, page in enumerate(PdfReader(str(path)).pages, 1):
        for n, image in enumerate(page.images, 1):
            try:
                img = Image.open(io.BytesIO(image.data))
                img.load()
            except Exception:  # noqa: BLE001 — okunamayan gömülü görsel atlanır
                continue
            if min(img.size) < MIN_SIDE:
                continue
            img = _fit(img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB"))
            target = storage.unique_path("uploads", f"{stem}-s{page_no}-{n}.png")
            img.save(target, "PNG", optimize=True)
            saved.append(target.name)
            if len(saved) >= 60:
                return saved
    return saved


def crop_to_library(area: str, name: str, box: list[float], product_name: str, clean_background: bool) -> tuple[str, list[str]]:
    """Görselin bir bölgesini (0–1 oranlarıyla) kırpar, istenirse zemini temizler, media alanına PNG kaydeder."""
    path = storage.area_file(area, name)
    img = Image.open(path)
    img.load()
    w, h = img.size
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box)
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        raise ValueError("Kırpma alanı çok küçük; kutu [x0, y0, x1, y1] 0–1 arası oran olmalı")
    crop = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    crop = _fit(remove_background(crop) if clean_background else crop.convert("RGBA"))
    target = storage.unique_path("media", f"urun-{storage.slug(product_name)}.png")
    crop.save(target, "PNG", optimize=True)
    return target.name, dominant_colors(crop)


def media_path(name: str) -> Path:
    return storage.area_file("media", name)
