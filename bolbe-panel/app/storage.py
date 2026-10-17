"""Dosya alanları ve JSON durum kayıtları (Railway diski: DATA_DIR)."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))
AREAS = ("uploads", "reports", "media")
TEXT_EXT = {".md", ".txt", ".csv", ".json", ".html", ".htm", ".tsv", ".xml", ".yaml", ".yml"}

state_lock = asyncio.Lock()


def ensure_dirs() -> None:
    for area in AREAS:
        (DATA_DIR / area).mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "state" / "runs").mkdir(parents=True, exist_ok=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(name: str) -> Path:
    return DATA_DIR / "state" / f"{name}.json"


def read_state(name: str, default: Any) -> Any:
    try:
        return json.loads(_state_path(name).read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_state(name: str, value: Any) -> None:
    path = _state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


async def log_activity(message: str, kind: str = "info") -> None:
    async with state_lock:
        items = read_state("activity", [])
        items.insert(0, {"time": now(), "kind": kind, "message": message[:500]})
        write_state("activity", items[:300])


def safe_name(name: str) -> str:
    """Dizin ayırıcıları ve kontrol karakterleri atılmış, güvenli dosya adı."""
    name = unicodedata.normalize("NFC", Path(name or "").name)
    name = re.sub(r"[\x00-\x1f/\\]", "", name).strip().lstrip(".")
    name = re.sub(r"\s+", " ", name)
    return (name or f"dosya-{uuid.uuid4().hex[:8]}")[:180]


_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iIsSgGuUoOcC")


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_TR)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:60] or "dosya"


def unique_path(area: str, name: str) -> Path:
    base = DATA_DIR / area
    target = base / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while target.exists():
        target = base / f"{stem} ({n}){suffix}"
        n += 1
    return target


def area_file(area: str, name: str) -> Path:
    if area not in AREAS:
        raise HTTPException(404, "Alan bulunamadı")
    if safe_name(name) != name:
        raise HTTPException(400, "Geçersiz dosya adı")
    path = DATA_DIR / area / name
    if not path.is_file():
        raise HTTPException(404, "Dosya bulunamadı")
    return path


def file_kind(path: Path, mime: str) -> str:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if path.suffix.lower() in TEXT_EXT:
        return "text"
    return "pdf" if mime == "application/pdf" else "other"


def file_info(area: str, path: Path) -> dict[str, Any]:
    st = path.stat()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "area": area,
        "name": path.name,
        "size": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "mime": mime,
        "kind": file_kind(path, mime),
    }


def list_area(area: str) -> list[dict[str, Any]]:
    items = [file_info(area, p) for p in (DATA_DIR / area).iterdir() if p.is_file() and not p.name.endswith(".part")]
    items.sort(key=lambda x: x["modified"], reverse=True)
    return items


async def save_upload(area: str, upload: UploadFile) -> Path:
    target = unique_path(area, safe_name(upload.filename or ""))
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    tmp = target.with_name(target.name + ".part")
    try:
        with tmp.open("wb") as fh:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"Dosya {MAX_UPLOAD_MB} MB sınırını aşıyor")
                fh.write(chunk)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def read_text_file(area: str, name: str, limit: int = 60_000) -> str:
    path = area_file(area, name)
    if path.suffix.lower() not in TEXT_EXT:
        raise HTTPException(400, "Metin dosyası değil")
    return path.read_text("utf-8", errors="replace")[:limit]
