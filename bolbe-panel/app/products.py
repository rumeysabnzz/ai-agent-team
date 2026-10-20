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
from urllib.parse import quote
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

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
    return f"{base}/pub/{expires}.{sign(name, expires)}/{quote(name)}"


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
    """Yedek yöntem (OpenCV yoksa): düz zemini kenarlardan doldurarak şeffaf yapar.

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


WORK_SIDE = 900     # GrabCut bu boyutta çalışır, maske tam boyuta büyütülür


def _rect_px(box: list[float], w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box)
    return int(x0 * w), int(y0 * h), int(round(x1 * w)), int(round(y1 * h))


def smart_cutout(img: Image.Image, box: list[float], keep: list[list[float]] | None = None,
                 remove: list[list[float]] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """Ürünü renk ve doku farkıyla zeminden ayırır (GrabCut).

    box: ürünün sınırları (görselin 0–1 oranları). Kutunun dışı kesin zemin, içi "muhtemel ürün" sayılır;
    algoritma kutu içindeki zemini dışarıdaki zeminin renklerinden öğrenip ayırır. keep kutuları kesin ürün
    (ör. zemine çok benzeyen beyaz gövde, etiket), remove kutuları kesin zemindir. Ürünün içinde kalan
    delikler doldurulur, tek parça en büyük nesne tutulur, kenar 1–2 piksel yumuşatılır.
    Dönüş: kırpılmış RGBA ürün ve kalite bilgileri.
    """
    import cv2
    import numpy as np

    rgb = img.convert("RGB")
    w, h = rgb.size
    rx0, ry0, rx1, ry1 = _rect_px(box, w, h)
    if rx1 - rx0 < 0.02 * w or ry1 - ry0 < 0.02 * h:
        raise ValueError("Ürün kutusu çok küçük; kutu [x0, y0, x1, y1] 0–1 arası oran olmalı")
    # kutunun çevresinde zemin örneği kalacak kadar pay bırakarak çalışma alanını kes
    pad_x, pad_y = int((rx1 - rx0) * 0.12) + 4, int((ry1 - ry0) * 0.12) + 4
    cx0, cy0, cx1, cy1 = max(0, rx0 - pad_x), max(0, ry0 - pad_y), min(w, rx1 + pad_x), min(h, ry1 + pad_y)
    area = np.array(rgb.crop((cx0, cy0, cx1, cy1)))
    ah, aw = area.shape[:2]
    scale = min(1.0, WORK_SIDE / max(aw, ah))
    sw, sh = max(1, int(aw * scale)), max(1, int(ah * scale))
    small = cv2.resize(area, (sw, sh), interpolation=cv2.INTER_AREA) if scale < 1 else area.copy()

    def to_small(b: list[float]) -> tuple[int, int, int, int]:
        bx0, by0, bx1, by1 = _rect_px(b, w, h)
        return (max(0, int((bx0 - cx0) * scale)), max(0, int((by0 - cy0) * scale)),
                min(sw, int((bx1 - cx0) * scale)), min(sh, int((by1 - cy0) * scale)))

    mask = np.full((sh, sw), cv2.GC_BGD, np.uint8)
    px0, py0, px1, py1 = to_small(box)
    # kutu görselin kenarına dayanıyorsa zemin örneği için ince bir kenar şeridi bırak
    band = max(2, int(min(sw, sh) * 0.015))
    px0, py0 = max(px0, band), max(py0, band)
    px1, py1 = min(px1, sw - band), min(py1, sh - band)
    mask[py0:py1, px0:px1] = cv2.GC_PR_FGD
    for b in remove or []:
        x0, y0, x1, y1 = to_small(b)
        mask[y0:y1, x0:x1] = cv2.GC_BGD
    for b in keep or []:
        x0, y0, x1, y1 = to_small(b)
        mask[y0:y1, x0:x1] = cv2.GC_FGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(7)   # aynı girdi her seferinde aynı sonucu versin (önizleme = kayıt)
    cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), mask, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)

    # yakın parçaları birleştir; kırıntıları at, ürün kutusundaki belirgin parçaları (kopuk kapak/pompa) tut
    k = max(3, int(min(sw, sh) * 0.01)) | 1
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if count <= 1:
        raise ValueError("Ürün zeminden ayrılamadı; kutuyu ürüne göre düzelt veya keep kutusu ver")
    largest_area = int(stats[1:, cv2.CC_STAT_AREA].max())
    kept = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 0.02 * largest_area]
    pieces = len(kept)
    fg = np.where(np.isin(labels, kept), 255, 0).astype(np.uint8)
    # ürünün içinde kalan delikleri doldur (beyaz gövdede zemin sanılan lekeler)
    flood = fg.copy()
    ff_mask = np.zeros((sh + 2, sw + 2), np.uint8)
    for seed in ((0, 0), (sw - 1, 0), (0, sh - 1), (sw - 1, sh - 1)):
        if flood[seed[1], seed[0]] == 0:
            cv2.floodFill(flood, ff_mask, seed, 128)
    holes = flood == 0
    hole_ratio = float(holes.sum()) / max(1, int((fg > 0).sum()))
    fg[holes] = 255

    # tam boyuta büyüt, kenarı yumuşat ve 1 piksel içeri çek (zemin rengi halesini azaltır)
    alpha = cv2.resize(fg, (aw, ah), interpolation=cv2.INTER_LINEAR) if scale < 1 else fg
    alpha = np.where(alpha >= 128, 255, 0).astype(np.uint8)
    edge = max(1, int(round(max(aw, ah) / 900)))
    alpha = cv2.erode(alpha, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * edge + 1, 2 * edge + 1)))
    alpha = cv2.GaussianBlur(alpha, (0, 0), 0.6 + 0.6 * edge)

    ys, xs = np.where(alpha > 8)
    out = Image.fromarray(np.dstack([area, alpha]), "RGBA").crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    # kalite bilgileri: ürün kutunun kenarına dayanıyorsa kesilmiş olabilir; kutunun çok azı ürünse kesim kaçmıştır
    rect_area = max(1, (px1 - px0) * (py1 - py0))
    fill = float((fg[py0:py1, px0:px1] > 0).sum()) / rect_area
    touch = []
    for side, strip in (("sol", fg[py0:py1, px0:px0 + 2]), ("sağ", fg[py0:py1, px1 - 2:px1]),
                        ("üst", fg[py0:py0 + 2, px0:px1]), ("alt", fg[py1 - 2:py1, px0:px1])):
        if strip.size and (strip > 0).mean() > 0.25:
            touch.append(side)
    warnings = []
    if fill < 0.25:
        warnings.append("Kutunun yalnız küçük bir kısmı ürün çıktı; ürün zeminle karışmış olabilir, keep kutusu ekle.")
    if fill > 0.97:
        warnings.append("Kutunun neredeyse tamamı ürün sayıldı; zemin ayrılamamış olabilir, kutuyu ürüne daha sıkı çiz veya remove kutusu ekle.")
    if touch:
        warnings.append("Ürün kutunun şu kenarlarına dayanıyor, kesilmiş olabilir: " + ", ".join(touch) + ". Kutuyu o yönde genişlet.")
    if pieces > 1:
        warnings.append(f"Ürün {pieces} ayrı parça halinde çıktı; aradaki kısım (boyun, kapak bağlantısı) kaybolmuş olabilir, keep kutusuyla bağla. "
                        "Parçalardan biri zemin artığıysa remove kutusuyla sil.")
    if hole_ratio > 0.02:
        warnings.append(f"Ürün içindeki boşluklar dolduruldu (ürünün %{hole_ratio * 100:.0f}'i); şeffaf/delikli ürünse kontrol et.")
    return out, {"method": "grabcut", "fill": round(fill, 2), "pieces": pieces, "holes_filled": round(hole_ratio, 3),
                 "warnings": warnings}


def auto_box(img: Image.Image) -> list[float] | None:
    """Kutu verilmeyen yüklemelerde ürünün sınırlarını kenar (çizgi) yoğunluğundan tahmin eder."""
    import cv2
    import numpy as np

    gray = np.array(img.convert("L"))
    h, w = gray.shape
    scale = min(1.0, 700 / max(w, h))
    small = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else gray
    sh, sw = small.shape
    edges = cv2.Canny(cv2.GaussianBlur(small, (5, 5), 0), 8, 24)
    m = max(2, int(min(sw, sh) * 0.02))
    edges[:m, :], edges[-m:, :], edges[:, :m], edges[:, -m:] = 0, 0, 0, 0
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(edges, 8)
    if count <= 1:
        return None
    largest = int(stats[1:, cv2.CC_STAT_AREA].max())
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 0.01 * largest]
    ys, xs = np.where(np.isin(labels, keep))
    x0, x1 = np.percentile(xs, [0.5, 99.5])
    y0, y1 = np.percentile(ys, [0.5, 99.5])
    pad_x, pad_y = (x1 - x0) * 0.03, (y1 - y0) * 0.03
    box = [max(0, x0 - pad_x) / sw, max(0, y0 - pad_y) / sh, min(sw, x1 + pad_x) / sw, min(sh, y1 + pad_y) / sh]
    if (box[2] - box[0]) * (box[3] - box[1]) < 0.02:
        return None
    return [round(float(v), 4) for v in box]


def straighten(img: Image.Image, degrees: float) -> Image.Image:
    """Eğik çekilmiş ürünü dikleştirir ve boşlukları kırpar. degrees: ürünün eğikliği; pozitif = üstü sağa yatık
    (saat yönünde eğik), negatif = üstü sola yatık. Ürün ters yönde döndürülür."""
    if not degrees:
        return img
    out = img.convert("RGBA").rotate(float(degrees), resample=Image.BICUBIC, expand=True)
    bbox = out.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return out.crop(bbox) if bbox else out


def check_sheet(img: Image.Image, height: int = 520) -> bytes:
    """Dekupeyi damalı, koyu ve renkli zemin üzerinde yan yana gösteren kontrol görseli (JPEG)."""
    item = img.convert("RGBA")
    item.thumbnail((height, height - 40))
    pw = item.width + 40
    sheet = Image.new("RGB", (pw * 3, height), "white")
    for i, bg in enumerate(("checker", (34, 34, 38), (233, 71, 90))):
        tile = Image.new("RGB", (pw, height), (255, 255, 255))
        if bg == "checker":
            d = ImageDraw.Draw(tile)
            for y in range(0, height, 16):
                for x in range(0, pw, 16):
                    if (x // 16 + y // 16) % 2:
                        d.rectangle([x, y, x + 15, y + 15], fill=(204, 204, 204))
        else:
            tile.paste(bg, (0, 0, pw, height))
        tile.paste(item, ((pw - item.width) // 2, (height - item.height) // 2), item)
        sheet.paste(tile, (i * pw, 0))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    return buf.getvalue()


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


def crop_to_library(area: str, name: str, box: list[float], product_name: str, clean_background: bool,
                    keep: list[list[float]] | None = None, remove: list[list[float]] | None = None,
                    rotate: float = 0.0, save: bool = True) -> dict[str, Any]:
    """Ürünü görselden ayırır (zemin temizleme istenirse akıllı kesim), dikleştirir, media alanına PNG kaydeder.

    save False ise yalnız kontrol görseli üretilir; ajan sonucu beğenene kadar kutuları düzeltip dener.
    """
    path = storage.area_file(area, name)
    img = Image.open(path)
    img.load()
    img = ImageOps.exif_transpose(img)
    w, h = img.size
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box)
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        raise ValueError("Kırpma alanı çok küçük; kutu [x0, y0, x1, y1] 0–1 arası oran olmalı")
    info: dict[str, Any] = {"method": "kırpma", "warnings": []}
    if clean_background:
        try:
            box = [x0, y0, x1, y1]
            auto = None
            if (x1 - x0) * (y1 - y0) > 0.95:   # kutu verilmemiş (tüm görsel): ürünün yerini tahmin et
                auto = auto_box(img)
                box = auto or box
            out, info = smart_cutout(img, box, keep, remove)
            if auto:
                info["method"] += f" (ürün kutusu otomatik bulundu: {auto})"
        except ImportError:
            out = remove_background(img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))))
            info = {"method": "kenardan doldurma (OpenCV yok)", "warnings": []}
    else:
        out = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))).convert("RGBA")
    out = _fit(straighten(out, rotate))
    info["size"] = list(out.size)
    info["colors"] = dominant_colors(out)
    info["preview"] = check_sheet(out)
    info["name"] = ""
    if save:
        target = storage.unique_path("media", f"urun-{storage.slug(product_name)}.png")
        out.save(target, "PNG", optimize=True)
        info["name"] = target.name
    return info


async def remove_image(product_id: str, media_name: str) -> dict[str, Any]:
    async with storage.state_lock:
        items = storage.read_state(STATE, [])
        item = next((p for p in items if p["id"] == product_id), None)
        if item is None:
            raise ValueError(f"Ürün bulunamadı: {product_id}")
        if media_name not in item.get("images", []):
            raise ValueError(f"Bu ürünün görseli değil: {media_name}")
        item["images"].remove(media_name)
        if item.get("primary") == media_name:
            item["primary"] = item["images"][0] if item["images"] else ""
        item["updated"] = storage.now()
        storage.write_state(STATE, items)
        return item


# ---------------------------------------------------------------- sahneye yerleştirme


def compose(scene: Image.Image, product: Image.Image, box: list[float], shadow: bool = True,
            match_light: bool = True) -> Image.Image:
    """Gerçek ürün dekupesini sahnenin box alanına (0–1 oranları) oranını koruyarak, tabanı kutunun altına
    oturacak şekilde yerleştirir; istenirse ürünün tonunu sahnenin ışığına hafifçe uydurur ve gölge ekler."""
    scene = ImageOps.exif_transpose(scene).convert("RGB")
    sw, sh = scene.size
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box)
    if x1 - x0 < 0.03 or y1 - y0 < 0.03:
        raise ValueError("Yerleştirme kutusu çok küçük; kutu [x0, y0, x1, y1] 0–1 arası oran olmalı")
    bw, bh = (x1 - x0) * sw, (y1 - y0) * sh
    item = product.convert("RGBA")
    bbox = item.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox:
        item = item.crop(bbox)
    ratio = min(bw / item.width, bh / item.height)
    item = item.resize((max(1, int(item.width * ratio)), max(1, int(item.height * ratio))), Image.LANCZOS)
    px = int(x0 * sw + (bw - item.width) / 2)
    py = int(y1 * sh - item.height)
    alpha = item.getchannel("A")

    if match_light:
        # ürünün ortalama tonunu kutudaki sahne tonuna en fazla %8 yaklaştır (etiket rengi bozulmasın)
        region = scene.crop((int(x0 * sw), int(y0 * sh), int(x1 * sw), int(y1 * sh))).resize((32, 32))
        s_mean = [sum(c) / len(c) for c in zip(*region.getdata())]
        p_pixels = [p[:3] for p in item.resize((64, 64)).getdata() if p[3] > 200] or [(128, 128, 128)]
        p_mean = [sum(c) / len(c) for c in zip(*p_pixels)]
        s_lum, p_lum = sum(s_mean) / 3 or 1, sum(p_mean) / 3 or 1
        gains = []
        for s_c, p_c in zip(s_mean, p_mean):
            cast = (s_c / s_lum) / max(0.01, p_c / p_lum)          # sahnenin renk sıcaklığı
            gains.append(max(0.92, min(1.08, 1 + 0.25 * (cast - 1))))
        light = max(0.9, min(1.05, 1 + 0.15 * (s_lum / 160 - 1)))  # karanlık sahnede biraz koyulaştır
        rgb = item.convert("RGB")
        bands = [b.point(lambda v, g=g * light: min(255, int(v * g))) for b, g in zip(rgb.split(), gains)]
        item = Image.merge("RGB", bands)
        item.putalpha(alpha)

    out = scene.copy()
    if shadow:
        # temas gölgesi: ürünün tabanında yumuşak elips
        cw, ch = int(item.width * 0.9), max(6, int(item.height * 0.06))
        contact = Image.new("L", (cw + ch * 4, ch * 5), 0)
        ImageDraw.Draw(contact).ellipse([ch * 2, ch * 2, ch * 2 + cw, ch * 3], fill=150)
        contact = contact.filter(ImageFilter.GaussianBlur(ch * 0.8))
        dark = Image.new("RGB", contact.size, (25, 20, 18))
        out.paste(dark, (px + (item.width - cw) // 2 - ch * 2, py + item.height - ch * 3 + ch // 2), contact)
        # yumuşak düşen gölge: ürün silüeti sağa-aşağı kaydırılıp bulanıklaştırılır
        off, blur = max(2, int(item.height * 0.02)), max(2, int(item.height * 0.025))
        m = blur * 3   # bulanıklık kutu kenarında kesilmesin diye pay
        drop = Image.new("L", (item.width + 2 * m, item.height + 2 * m), 0)
        drop.paste(alpha.point(lambda v: int(v * 0.28)), (m, m))
        drop = drop.filter(ImageFilter.GaussianBlur(blur))
        out.paste(Image.new("RGB", drop.size, (25, 20, 18)), (px + off - m, py + off - m), drop)
    out.paste(item, (px, py), item)
    return out


def compose_to_media(scene_name: str, product_id: str, image: str, box: list[float], shadow: bool,
                     filename: str) -> dict[str, Any]:
    p = get(product_id)
    if not p:
        raise ValueError(f"Ürün bulunamadı: {product_id}")
    image = image or p.get("primary", "")
    if not image:
        raise ValueError("Ürünün kütüphane görseli yok; önce Ürün Kütüphanecisi dekupe eklemeli")
    if image not in p.get("images", []):
        raise ValueError(f"{image} bu ürünün kütüphane görseli değil")
    product = Image.open(media_path(image))
    product.load()
    note = ""
    if product.mode != "RGBA" or product.getchannel("A").getextrema()[0] == 255:
        note = "Ürün görseli şeffaf zeminli değil; dikdörtgen olarak yerleşti. Kütüphaneciden dekupe isteyin."
    scene = Image.open(media_path(scene_name))
    scene.load()
    out = compose(scene, product, box, shadow)
    stem = storage.slug(Path(filename).stem) or "kompozisyon"
    target = storage.unique_path("media", f"{stem}.jpg")
    out.save(target, "JPEG", quality=92)
    prev = out.copy()
    prev.thumbnail((900, 900))
    buf = io.BytesIO()
    prev.save(buf, "JPEG", quality=85)
    return {"name": target.name, "url": public_url(target.name), "preview": buf.getvalue(), "note": note}


def media_path(name: str) -> Path:
    return storage.area_file("media", name)
