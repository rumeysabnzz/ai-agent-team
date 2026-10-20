"""Galeri klasörleri: görsel ve videoların sanal klasörleri.

Dosyalar diskte yerinde kalır (onaylar, ürün kütüphanesi ve raporlar dosya adıyla bağlantı kurar); klasör
yalnız bir etikettir. Ajanların ürettiği medya, üretildiği çalışmanın klasörüne kendiliğinden girer;
revizyonlar ilk çalışmanın klasörüne eklenir. Kurucu klasör açar, adlandırır ve dosyaları taşır.
"""

from __future__ import annotations

import re
import tempfile
import uuid
import zipfile
from datetime import datetime
from typing import Any

from . import storage

STATE = "media_folders"
PRODUCTS = "Ürün kütüphanesi"
COMPETITORS = "Rakip ürünleri"
UPLOADS = "Yüklenenler"
MEDIA_KINDS = ("image", "video")


def _load() -> dict[str, Any]:
    data = storage.read_state(STATE, {})
    data.setdefault("folders", [])
    data.setdefault("assign", {})
    return data


def _key(name: str) -> str:
    # Türkçe I/ı/İ farkı aynı klasörü iki kez açtırmasın
    return re.sub(r"\s+", " ", name).strip().replace("İ", "i").replace("I", "i").replace("ı", "i").casefold()


def _find(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    key = _key(name)
    return next((f for f in data["folders"] if _key(f["name"]) == key), None)


def _ensure(data: dict[str, Any], name: str, auto: bool) -> str:
    name = re.sub(r"\s+", " ", name).strip()[:80] or UPLOADS
    folder = _find(data, name)
    if folder is None:
        folder = {"id": uuid.uuid4().hex[:8], "name": name, "auto": auto, "created": storage.now()}
        data["folders"].append(folder)
    return folder["id"]


def run_folder_name(run: Any, tz: Any) -> str:
    """Çalışmanın klasör adı: tarih · çalışma adı. Revizyon, ilk içeriğin klasörüne gider."""
    if getattr(run, "revision_of", None):
        data = _load()
        original = next((a for a in storage.read_state("approvals", []) if a["id"] == run.revision_of), None)
        for media in (original or {}).get("media", []):
            folder = next((f for f in data["folders"] if f["id"] == data["assign"].get(media)), None)
            if folder:
                return folder["name"]
    started = datetime.fromisoformat(run.created).astimezone(tz)
    title = re.sub(r"^Revizyon:\s*", "", run.title)
    return f"{started:%d.%m.%Y} · {title}"


async def place(names: list[str], folder_name: str, auto: bool = True, overwrite: bool = True) -> None:
    async with storage.state_lock:
        data = _load()
        fid = _ensure(data, folder_name, auto)
        for name in names:
            if overwrite or name not in data["assign"]:
                data["assign"][name] = fid
        storage.write_state(STATE, data)


def folder_for_media(name: str, agent: dict[str, Any] | None, run: Any, tz: Any) -> str:
    if name.startswith("urun-") and agent and agent.get("library"):
        return PRODUCTS
    if agent and agent.get("product_images"):
        return COMPETITORS
    return run_folder_name(run, tz) if run is not None else UPLOADS


async def forget(name: str) -> None:
    async with storage.state_lock:
        data = _load()
        if data["assign"].pop(name, None) is not None:
            storage.write_state(STATE, data)


def _backfill(data: dict[str, Any], tz: Any) -> None:
    """İlk açılışta eski medyayı geçmiş çalışmalardan ve dosya adından klasörlere yerleştirir."""
    runs_dir = storage.DATA_DIR / "state" / "runs"
    runs = []
    for path in runs_dir.glob("*.json") if runs_dir.is_dir() else []:
        run = storage.read_state(f"runs/{path.stem}", None)
        if isinstance(run, dict) and run.get("outputs"):
            runs.append(run)
    runs.sort(key=lambda r: r.get("created", ""))
    approvals = {a["id"]: a for a in storage.read_state("approvals", [])}
    for run in runs:
        media = [o["name"] for o in run["outputs"] if o.get("type") == "media" and o.get("name")]
        if not media:
            continue
        name = None
        original = approvals.get(run.get("revision_of") or "")
        for m in (original or {}).get("media", []):
            fid = data["assign"].get(m)
            name = next((f["name"] for f in data["folders"] if f["id"] == fid), None)
            if name:
                break
        if not name:
            try:
                started = datetime.fromisoformat(run["created"]).astimezone(tz)
            except (KeyError, ValueError):
                continue
            title = re.sub(r"^Revizyon:\s*", "", run.get("title", "Çalışma"))
            name = f"{started:%d.%m.%Y} · {title}"
        for m in media:
            if m.startswith("urun-"):
                data["assign"].setdefault(m, _ensure(data, PRODUCTS, True))
            elif m.startswith("rakip-"):
                data["assign"].setdefault(m, _ensure(data, COMPETITORS, True))
            else:
                data["assign"].setdefault(m, _ensure(data, name, True))
    for f in storage.list_area("media"):
        if f["name"] in data["assign"] or f["kind"] not in MEDIA_KINDS:
            continue
        if f["name"].startswith("urun-"):
            data["assign"][f["name"]] = _ensure(data, PRODUCTS, True)
        elif f["name"].startswith("rakip-"):
            data["assign"][f["name"]] = _ensure(data, COMPETITORS, True)
    data["backfilled"] = True


async def listing(tz: Any) -> dict[str, Any]:
    items = [f for f in storage.list_area("media") if f["kind"] in MEDIA_KINDS]
    async with storage.state_lock:
        data = _load()
        if not data.get("backfilled"):
            _backfill(data, tz)
            storage.write_state(STATE, data)
    ids = {f["id"] for f in data["folders"]}
    for f in items:
        fid = data["assign"].get(f["name"])
        f["folder"] = fid if fid in ids else ""
    folders = []
    for folder in data["folders"]:
        mine = [f for f in items if f["folder"] == folder["id"]]
        cover = next((f["name"] for f in mine if f["kind"] == "image"), mine[0]["name"] if mine else "")
        folders.append({**folder, "count": len(mine), "images": sum(f["kind"] == "image" for f in mine),
                        "videos": sum(f["kind"] == "video" for f in mine), "cover": cover,
                        "cover_kind": next((f["kind"] for f in mine if f["name"] == cover), ""),
                        "updated": mine[0]["modified"] if mine else folder["created"]})
    folders.sort(key=lambda f: f["updated"], reverse=True)
    return {"folders": folders, "items": items, "unfiled": sum(1 for f in items if not f["folder"])}


async def create(name: str) -> dict[str, Any]:
    async with storage.state_lock:
        data = _load()
        if _find(data, name):
            raise ValueError("Bu adda bir klasör zaten var")
        fid = _ensure(data, name, False)
        storage.write_state(STATE, data)
    return next(f for f in data["folders"] if f["id"] == fid)


async def rename(fid: str, name: str) -> dict[str, Any]:
    async with storage.state_lock:
        data = _load()
        folder = next((f for f in data["folders"] if f["id"] == fid), None)
        if folder is None:
            raise KeyError(fid)
        other = _find(data, name)
        if other and other["id"] != fid:
            raise ValueError("Bu adda bir klasör zaten var")
        folder["name"], folder["auto"] = re.sub(r"\s+", " ", name).strip()[:80], False
        storage.write_state(STATE, data)
    return folder


async def remove(fid: str) -> None:
    """Klasörü siler; içindeki dosyalar silinmez, klasörsüz kalır."""
    async with storage.state_lock:
        data = _load()
        if not any(f["id"] == fid for f in data["folders"]):
            raise KeyError(fid)
        data["folders"] = [f for f in data["folders"] if f["id"] != fid]
        data["assign"] = {n: f for n, f in data["assign"].items() if f != fid}
        storage.write_state(STATE, data)


async def move(names: list[str], fid: str) -> int:
    async with storage.state_lock:
        data = _load()
        if fid and not any(f["id"] == fid for f in data["folders"]):
            raise KeyError(fid)
        for name in names:
            storage.area_file("media", name)
            if fid:
                data["assign"][name] = fid
            else:
                data["assign"].pop(name, None)
        storage.write_state(STATE, data)
    return len(names)


def zip_folder(fid: str) -> tuple[Any, str]:
    """Klasördeki görsel ve videoları ZIP olarak hazırlar (büyük videolar için geçici dosyada)."""
    data = _load()
    folder = next((f for f in data["folders"] if f["id"] == fid), None)
    if folder is None:
        raise KeyError(fid)
    names = [n for n, f in data["assign"].items() if f == fid and (storage.DATA_DIR / "media" / n).is_file()]
    tmp = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:   # görsel/video zaten sıkıştırılmış
        for name in names:
            z.write(storage.DATA_DIR / "media" / name, name)
    tmp.seek(0)
    return tmp, f"{storage.slug(folder['name'])}.zip"
