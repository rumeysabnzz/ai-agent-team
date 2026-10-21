"""Ajan çalıştırma motoru.

- Her ajan Claude API'de kendi araç döngüsüyle çalışır (Claude Opus 5.5, uyarlanabilir düşünme).
- Sunucu araçları: web araması; Virale (ChatPlace MCP) izin listesiyle.
- Panel araçları: dosya okuma/yazma, rapor kaydı, medya indirme, onaya gönderme, uyarı.
- Olaylar canlı olarak arayüze (SSE) yayınlanır; çalıştırmalar diske kaydedilir.
- Aylık bütçe ve çalıştırma başına üretim sınırı uygulanır.
"""

from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import json
import mimetypes
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import anthropic
import httpx

from . import convert, folders, products, storage, studio
from .team import AGENTS, COMMON_RULES, DEFAULT_BRAND, DEFAULT_COMPETITORS, TEAMS, WORKFLOW_BY_ID, WORKFLOWS

VIRALE_MCP_URL = os.getenv("VIRALE_MCP_URL", "")
VIRALE_MCP_TOKEN = os.getenv("VIRALE_MCP_TOKEN", "")
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "50"))
MAX_TURNS = int(os.getenv("MAX_AGENT_TURNS", "30"))
MAX_GENERATIONS_PER_RUN = int(os.getenv("MAX_GENERATIONS_PER_RUN", "12"))
MAX_PARALLEL_RUNS = int(os.getenv("MAX_PARALLEL_RUNS", "2"))
TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Istanbul"))

# Seçilebilir modeller. Fiyatlar $ / 1M token; önbellek okuma 0.1x, yazma 1.25x girdi.
# Haiku 5.5'in iki fiyat kademesi var: istem 100K token'ı aşarsa "long" fiyatı uygulanır.
# Haiku 5.5'te sunucu tarafı yedek model (fallbacks) yok ve web aramasının dinamik filtreleme
# sürümü desteklenmiyor; temel sürüm kullanılır.
MODELS: dict[str, dict[str, Any]] = {
    "claude-haiku-5-5": {
        "label": "Haiku 5.5", "note": "en ucuz, veri toplama ve araç kullanımı",
        "price": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
        "long_price": {"input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625},
        "fallback": False, "web_search": "web_search_20250305", "web_fetch": None,
    },
    "claude-sonnet-5-5": {
        "label": "Sonnet 5.5", "note": "dengeli, analiz ve metin",
        "price": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5},
        "fallback": True, "web_search": "web_search_20260209", "web_fetch": "web_fetch_20260209",
    },
    "claude-opus-5-5": {
        "label": "Opus 5.5", "note": "en güçlü, en pahalı",
        "price": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
        "fallback": True, "web_search": "web_search_20260209", "web_fetch": "web_fetch_20260209",
    },
}
LONG_PROMPT_TOKENS = 100_000
WEB_SEARCH_PRICE = 0.01  # $10 / 1000 arama
GENERATE_TOOLS = {"virale_image_generate", "virale_carousel_generate", "virale_sequence_generate",
                  "virale_video_generate", "virale_multi_video_generate", "virale_video_montage_create"}
VIRALE_GENERATION = os.getenv("VIRALE_GENERATION", "") == "1"   # varsayılan kapalı: Virale yalnız rakip analizi
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MCP_BETA = "mcp-client-2025-11-20"
MAX_MEDIA_BYTES = 300 * 1024 * 1024

_client: anthropic.AsyncAnthropic | None = None
_semaphore = asyncio.Semaphore(MAX_PARALLEL_RUNS)
_background: set[asyncio.Task] = set()


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(max_retries=3)
    return _client


def api_ready() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------- canlı olay yayını


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self.live: dict[str, dict[str, Any]] = {}  # run_id -> anlık durum

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("time", storage.now())
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


bus = EventBus()

# ---------------------------------------------------------------- ayarlar ve bütçe


def get_settings() -> dict[str, Any]:
    s = storage.read_state("settings", {})
    s.setdefault("brand", DEFAULT_BRAND)
    s.setdefault("competitors", DEFAULT_COMPETITORS)
    s.setdefault("schedules", {w["id"]: {"enabled": True} for w in WORKFLOWS})
    for w in WORKFLOWS:
        s["schedules"].setdefault(w["id"], {"enabled": True})
    s.setdefault("models", {})
    return s


def model_for(agent_id: str, settings: dict[str, Any] | None = None) -> str:
    chosen = (settings or get_settings())["models"].get(agent_id)
    return chosen if chosen in MODELS else AGENTS[agent_id]["model"]


def model_summary(settings: dict[str, Any] | None = None) -> str:
    settings = settings or get_settings()
    counts: dict[str, int] = {}
    for agent_id in AGENTS:
        label = MODELS[model_for(agent_id, settings)]["label"]
        counts[label] = counts.get(label, 0) + 1
    return " · ".join(f"{n} {label}" for label, n in counts.items())


def month_key() -> str:
    return datetime.now(TZ).strftime("%Y-%m")


def month_spend() -> float:
    return float(storage.read_state("spend", {}).get(month_key(), 0.0))


async def add_spend(usd: float) -> None:
    async with storage.state_lock:
        spend = storage.read_state("spend", {})
        spend[month_key()] = round(float(spend.get(month_key(), 0.0)) + usd, 4)
        storage.write_state("spend", spend)


def usage_cost(u: Any, model: str) -> float:
    tokens = {
        "input": getattr(u, "input_tokens", 0) or 0,
        "output": getattr(u, "output_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    info = MODELS[model]
    prompt = tokens["input"] + tokens["cache_read"] + tokens["cache_write"]
    price = info["long_price"] if "long_price" in info and prompt > LONG_PROMPT_TOKENS else info["price"]
    cost = sum(tokens[k] * price[k] for k in tokens) / 1e6
    stu = getattr(u, "server_tool_use", None)
    cost += (getattr(stu, "web_search_requests", 0) or 0) * WEB_SEARCH_PRICE
    return cost


# ---------------------------------------------------------------- panel araçları

CUSTOM_TOOLS: dict[str, dict[str, Any]] = {
    "list_files": {
        "description": "Paneldeki bir alanın dosyalarını listeler (en yeni önce). Alanlar: uploads (kullanıcının yüklediği veriler), reports (raporlar ve planlar), media (görsel ve videolar).",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["area"],
                         "properties": {"area": {"type": "string", "enum": ["uploads", "reports", "media"]}}},
    },
    "read_file": {
        "description": "uploads veya reports alanındaki bir dosyayı Markdown metni olarak okur: md, txt, csv, json ve ayrıca PDF, Word (.docx), "
                       "Excel (.xlsx, tüm sayfalar tablo olarak) ve PowerPoint (.pptx) otomatik dönüştürülür. Görseller için view_image kullan. "
                       "Dosya adını list_files çıktısından birebir kullan.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["area", "name"],
                         "properties": {"area": {"type": "string", "enum": ["uploads", "reports"]},
                                        "name": {"type": "string"}}},
    },
    "save_report": {
        "description": "Bir raporu veya planı Markdown olarak panelin Raporlar alanına kaydeder. Teslim edilecek nihai çıktı için kullan.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["title", "markdown"],
                         "properties": {"title": {"type": "string"}, "markdown": {"type": "string"}}},
    },
    "save_media_from_url": {
        "description": "Üretilmiş bir görseli veya videoyu https adresinden indirip panelin galerisine kaydeder. Kaydedilen dosya adını döndürür; onaya gönderirken bu adı kullan.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["url", "filename"],
                         "properties": {"url": {"type": "string"},
                                        "filename": {"type": "string", "description": "Uzantılı, Türkçe karaktersiz kısa ad. Ör. carousel-bariyer-01.png"}}},
    },
    "submit_for_approval": {
        "description": "Kullanıma hazır bir içeriği kullanıcının onayına gönderir. Görsel, carousel, reel, story veya metin için.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["kind", "title", "body", "media"],
                         "properties": {
                             "kind": {"type": "string", "enum": ["görsel", "story", "carousel", "reel", "metin"]},
                             "title": {"type": "string"},
                             "body": {"type": "string", "description": "Markdown: caption, hashtag'ler, ekran metinleri, paylaşım zamanı, notlar"},
                             "media": {"type": "array", "items": {"type": "string"}, "description": "Galerideki dosya adları (save_media_from_url çıktısı). Yoksa boş liste."}}},
    },
    "raise_alert": {
        "description": "Rakip tarafında hemen dikkat gerektiren bir gelişmeyi panelde uyarı olarak gösterir.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["level", "title", "detail"],
                         "properties": {"level": {"type": "string", "enum": ["kritik", "önemli", "bilgi"]},
                                        "title": {"type": "string"}, "detail": {"type": "string"}}},
    },
}
CUSTOM_TOOLS.update({
    "add_lesson": {
        "description": "Bir ajanın kalıcı hafızasına kısa bir çalışma dersi ekler; ajan sonraki tüm çalışmalarında bunu okur. "
                       "Ajanlar yalnız kendi kimliklerine, yöneticiler kendi takımlarındaki ajanlara, Ana Kontrolcü herkese ders ekleyebilir.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["agent_id", "text"],
                         "properties": {"agent_id": {"type": "string"}, "text": {"type": "string"}}},
    },
    "list_lessons": {
        "description": "Bir ajanın hafızasındaki dersleri kimlikleriyle listeler.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["agent_id"],
                         "properties": {"agent_id": {"type": "string"}}},
    },
    "remove_lesson": {
        "description": "Bir ajanın hafızasından eskimiş, çelişen veya tekrar eden bir dersi siler.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["agent_id", "lesson_id"],
                         "properties": {"agent_id": {"type": "string"}, "lesson_id": {"type": "string"}}},
    },
    "add_knowledge": {
        "description": "Takımın bilgi bankasına dış kaynaktan (web) edinilmiş kısa, kaynaklı bir bilgi notu ekler. Takımın tüm ajanları bunu okur.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["title", "text", "source_url"],
                         "properties": {"title": {"type": "string"}, "text": {"type": "string"}, "source_url": {"type": "string"}}},
    },
    "team_activity": {
        "description": "Takımın (Ana Kontrolcü için tüm ekibin) son günlerdeki çalıştırmalarını, hatalarını, maliyetini, "
                       "onay kararlarını ve kullanıcı notlarını özetler.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["days"],
                         "properties": {"days": {"type": "integer"}}},
    },
})
CUSTOM_TOOLS.update({
    "wait": {
        "description": "Belirtilen süre (10–120 sn) bekler. Virale üretimleri (özellikle video, 2–4 dk) bitmeden önce durumu art arda "
                       "sorgulama; her virale_generation_status kontrolünden önce wait ile 30–60 sn bekle.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["seconds"],
                         "properties": {"seconds": {"type": "integer"}}},
    },
    "view_image": {
        "description": "uploads veya media alanındaki bir görseli (jpg, png, webp, gif) görmeni sağlar. Kurucunun yüklediği ürün/marka "
                       "görsellerini ve daha önce üretilen görselleri incelemek için kullan.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["area", "name"],
                         "properties": {"area": {"type": "string", "enum": ["uploads", "media"]}, "name": {"type": "string"}}},
    },
    "search_archive": {
        "description": "Ekibin tüm geçmişinde arama yapar: kaydedilmiş raporlar, Kurucunun yüklediği metin dosyaları ve dosya notları, "
                       "geçmiş görevler ve Kurucunun talimatları, ajanların geçmiş çıktıları, onaya gönderilen içerikler ve notları, bilgi bankası. "
                       "Yeni bir işe başlamadan önce ilgili eski araştırmaları ve kararları bulmak için kullan.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["query"],
                         "properties": {"query": {"type": "string"}}},
    },
})
CUSTOM_TOOLS.update({
    "list_products": {
        "description": "BOLBÉ ürün kütüphanesindeki ürünleri kimlik kartlarıyla listeler (ad, kategori, hacim, renkler, ambalaj, etiket yazısı, referans görseller).",
        "input_schema": {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
    },
    "product_reference": {
        "description": "Bir ürünün kimlik kartını ve Virale'ye referans olarak verilecek herkese açık, süreli görsel bağlantılarını döndürür.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["product_id"],
                         "properties": {"product_id": {"type": "string"}}},
    },
    "extract_pdf_images": {
        "description": "uploads alanındaki bir PDF katalogdaki gömülü görselleri ayrı PNG dosyaları olarak uploads alanına çıkarır ve adlarını döndürür.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["name"],
                         "properties": {"name": {"type": "string"}}},
    },
    "crop_product": {
        "description": "Bir görseldeki ürünü ayırıp ürün kütüphanesine şeffaf zeminli referans görsel (dekupe) olarak ekler. "
                       "box = [x0, y0, x1, y1] ürünün sınırları; görselin genişlik ve yüksekliğine göre 0–1 arası oranlar, ürüne sıkı ama "
                       "ürünü kesmeyecek şekilde. clean_background true ise akıllı kesim ürünü renk ve doku farkıyla zeminden ayırır: "
                       "keep_boxes kesin ürün olan alanlar (zemine çok benzeyen beyaz gövde, şeffaf kısım, kapak/pompa), remove_boxes kesin "
                       "zemin olan alanlar (ürün sanılan gölge, yansıma, yandaki nesne); yoksa boş liste. rotate ürünün eğikliği (derece; "
                       "pozitif = üstü sağa yatık), ürün dikleştirilir; düzse 0. save false iken yalnız kontrol görseli döner, kütüphaneye "
                       "eklenmez. replace_image bir kütüphane görselinin adıysa yeni görsel açılmaz, o görselin üzerine yazılır "
                       "(eksik/bozuk dekupeyi düzeltmek için; yoksa \"\"). Sonuç damalı, koyu ve kırmızı zemin üzerinde kontrol "
                       "görseli olarak döner; zeminle karışıp otomatik tamamlanan kısımlar (cam, kapak, pompa) dördüncü karede pembe.",
        "input_schema": {"type": "object", "additionalProperties": False,
                         "required": ["area", "name", "box", "product_name", "clean_background", "keep_boxes", "remove_boxes",
                                      "rotate", "save", "primary", "replace_image"],
                         "properties": {"area": {"type": "string", "enum": ["uploads", "media"]}, "name": {"type": "string"},
                                        "box": {"type": "array", "items": {"type": "number"}},
                                        "product_name": {"type": "string"}, "clean_background": {"type": "boolean"},
                                        "keep_boxes": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                                        "remove_boxes": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                                        "rotate": {"type": "number"}, "save": {"type": "boolean"},
                                        "primary": {"type": "boolean"}, "replace_image": {"type": "string"}}},
    },
    "design_image": {
        "description": "Sistem içi tasarım stüdyosu: yazdığın SVG'yi PNG/JPEG görsele çevirir ve galeriye kaydeder (ücretsiz). "
                       "format: feed (1080×1350), square (1080×1080), story (1080×1920); SVG'nin width/height/viewBox değeri "
                       "bu ölçüde olmalı. Ürün dekupesi ve yüklenen fotoğraflar için <image href=\"media:DOSYA\"> veya "
                       "<image href=\"uploads:DOSYA\">; başka bağlantı açılmaz. Fontlar: " + studio.FONTS + ". "
                       "transparent true ise zemin şeffaf PNG olur (video yazı katmanı için). Sonucun önizlemesi döner.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["svg", "format", "filename", "transparent"],
                         "properties": {"svg": {"type": "string"}, "format": {"type": "string", "enum": list(studio.SIZES)},
                                        "filename": {"type": "string"}, "transparent": {"type": "boolean"}}},
    },
    "make_video": {
        "description": "Sistem içi video kurgusu (ffmpeg, ücretsiz): sahnelerden MP4 üretir ve galeriye kaydeder. Her sahne: image "
                       "(galerideki kare, ör. design_image çıktısı; \"uploads:ad\" ile yüklenen fotoğraf), seconds (1.2–10), motion "
                       "(still, zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down), overlay (şeffaf PNG yazı katmanı, yoksa \"\"), "
                       "overlay_delay (yazının belirme saniyesi). transition: fade, dissolve, slideleft, slideup, circleopen, "
                       "wipeleft, none. audio: uploads alanındaki müzik dosyası (yoksa \"\"). En fazla 12 sahne, 60 sn. "
                       "Sonuçtan eşit aralıklı kareler döner.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["scenes", "format", "transition", "audio", "filename"],
                         "properties": {"scenes": {"type": "array", "items": {
                             "type": "object", "additionalProperties": False,
                             "required": ["image", "seconds", "motion", "overlay", "overlay_delay"],
                             "properties": {"image": {"type": "string"}, "seconds": {"type": "number"},
                                            "motion": {"type": "string", "enum": list(studio.MOTIONS)},
                                            "overlay": {"type": "string"}, "overlay_delay": {"type": "number"}}}},
                             "format": {"type": "string", "enum": list(studio.SIZES)},
                             "transition": {"type": "string", "enum": list(studio.TRANSITIONS)},
                             "audio": {"type": "string"}, "filename": {"type": "string"}}},
    },
    "repair_product_image": {
        "description": "Bir ürün kütüphanesi görselini kontrol eder ve onarır: kapak, cam, pompa gibi zeminle karışıp silinmiş "
                       "kısımları geri getirir. Görselin kaynak fotoğrafı biliniyorsa yeni yöntemle yeniden keser, bilinmiyorsa "
                       "dosyanın kendi piksellerinden onarır. Önce/sonra kontrol görseli döner; geri getirilen kısımlar pembe.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["product_id", "image"],
                         "properties": {"product_id": {"type": "string"}, "image": {"type": "string"}}},
    },
    "remove_product_image": {
        "description": "Ürün kütüphanesinden hatalı bir ürün görselini (ör. delikli, lekeli eski dekupe) kaldırır.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["product_id", "image"],
                         "properties": {"product_id": {"type": "string"}, "image": {"type": "string"}}},
    },
    "compose_product": {
        "description": "Kütüphanedeki gerçek ürün dekupesini bir sahne görselinin üzerine yerleştirir (Photoshop'taki gibi): ürün "
                       "box = [x0, y0, x1, y1] alanına oranı korunarak sığdırılır ve tabanı kutunun alt kenarına oturur; ton sahnenin "
                       "ışığına hafifçe uydurulur, shadow true ise temas ve düşen gölge eklenir. scene media alanındaki sahne görseli "
                       "(save_media_from_url çıktısı), image boşsa ürünün ana görseli kullanılır. Etiket birebir korunur. Sonuç media "
                       "alanına kaydedilir; kontrol görseli ve Virale'ye verilebilecek herkese açık bağlantı döner. Birden fazla ürün "
                       "için çıktıyı sahne olarak verip tekrar çağır.",
        "input_schema": {"type": "object", "additionalProperties": False,
                         "required": ["scene", "product_id", "image", "box", "shadow", "filename"],
                         "properties": {"scene": {"type": "string"}, "product_id": {"type": "string"}, "image": {"type": "string"},
                                        "box": {"type": "array", "items": {"type": "number"}}, "shadow": {"type": "boolean"},
                                        "filename": {"type": "string"}}},
    },
    "save_product": {
        "description": "Ürün kimlik kartını oluşturur veya günceller (ada göre eşleşir). Bilinmeyen alanları boş bırak.",
        "input_schema": {"type": "object", "additionalProperties": False,
                         "required": ["name", "category", "size", "price", "colors", "packaging", "label_text", "key_ingredients", "description"],
                         "properties": {"name": {"type": "string"}, "category": {"type": "string"}, "size": {"type": "string"},
                                        "price": {"type": "string"}, "colors": {"type": "array", "items": {"type": "string"}},
                                        "packaging": {"type": "string"}, "label_text": {"type": "string"},
                                        "key_ingredients": {"type": "string"}, "description": {"type": "string"}}},
    },
})
BASE_TOOLS = ["list_files", "read_file", "save_report", "add_lesson", "view_image", "search_archive"]
LIBRARY_TOOLS = ["extract_pdf_images", "crop_product", "repair_product_image", "remove_product_image", "save_product", "list_products"]
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 3_500_000
MAX_IMAGE_SIDE = 2000


def sniff_image(head: bytes) -> str | None:
    """Dosyanın gerçek görsel türü (uzantıya değil içeriğe bakar; Virale png adıyla jpeg verebiliyor)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_block(path: Path) -> dict[str, Any]:
    """Görseli Claude'a gönderilecek blok yapar; tür içerikten belirlenir, büyük veya desteklenmeyen görsel JPEG'e küçültülür."""
    raw = path.read_bytes()
    media_type = sniff_image(raw[:16])
    if media_type is None or len(raw) > MAX_IMAGE_BYTES or media_type == "image/gif" and len(raw) > 1_000_000:
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Görsel açılamadı ({path.name}): {exc}") from exc
        img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        raw, media_type = buf.getvalue(), "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(raw).decode()}}
MANAGER_TOOLS = ["list_lessons", "remove_lesson", "add_knowledge", "team_activity"]
MAX_LESSONS = 25


# ---------------------------------------------------------------- hafıza ve bilgi bankası

def lessons_for(agent_id: str) -> list[dict[str, Any]]:
    return storage.read_state("memory", {}).get(agent_id, [])


async def add_lesson(agent_id: str, text: str, source: str) -> dict[str, Any]:
    lesson = {"id": uuid.uuid4().hex[:8], "time": storage.now(), "source": source, "text": text.strip()[:600]}
    async with storage.state_lock:
        memory = storage.read_state("memory", {})
        items = memory.setdefault(agent_id, [])
        items.append(lesson)
        memory[agent_id] = items[-MAX_LESSONS:]
        storage.write_state("memory", memory)
    return lesson


async def remove_lesson(agent_id: str, lesson_id: str) -> bool:
    async with storage.state_lock:
        memory = storage.read_state("memory", {})
        before = memory.get(agent_id, [])
        memory[agent_id] = [x for x in before if x["id"] != lesson_id]
        storage.write_state("memory", memory)
    return len(before) != len(memory[agent_id])


def knowledge_for(team_id: str) -> list[dict[str, Any]]:
    kb = storage.read_state("knowledge", {})
    return kb.get("genel", []) + (kb.get(team_id, []) if team_id != "genel" else [])


async def add_knowledge(team_id: str, title: str, text: str, source_url: str, added_by: str) -> dict[str, Any]:
    note = {"id": uuid.uuid4().hex[:8], "time": storage.now(), "title": title.strip()[:160], "text": text.strip()[:1500],
            "source_url": source_url.strip()[:400], "added_by": added_by}
    async with storage.state_lock:
        kb = storage.read_state("knowledge", {})
        kb.setdefault(team_id, []).append(note)
        kb[team_id] = kb[team_id][-40:]
        storage.write_state("knowledge", kb)
    return note


async def remove_knowledge(team_id: str, note_id: str) -> bool:
    async with storage.state_lock:
        kb = storage.read_state("knowledge", {})
        before = kb.get(team_id, [])
        kb[team_id] = [x for x in before if x["id"] != note_id]
        storage.write_state("knowledge", kb)
    return len(before) != len(kb[team_id])


def team_members(team_id: str) -> list[str]:
    return [a["id"] for t in TEAMS if t["id"] == team_id for a in t["agents"]]


def _can_coach(agent: dict[str, Any], target: str) -> bool:
    if target not in AGENTS:
        return False
    if agent.get("controller"):
        return True
    if agent.get("manager"):
        return target in team_members(agent["team"]) or target == agent["id"]
    return target == agent["id"]


def _fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def file_notes() -> dict[str, dict[str, Any]]:
    return storage.read_state("file_notes", {})


def _recent_runs(limit: int = 40) -> list[dict[str, Any]]:
    return [r for r in (load_run(x["id"]) for x in storage.read_state("runs_index", [])[:limit]) if r]


def search_archive(query: str, limit: int = 8) -> list[dict[str, Any]]:
    terms = [t for t in _fold(query).split() if len(t) > 2] or [_fold(query).strip()]
    docs: list[tuple[str, str]] = []
    notes = file_notes()
    for area in ("reports", "uploads"):
        for f in storage.list_area(area)[:80]:
            text = ""
            if Path(f["name"]).suffix.lower() in storage.TEXT_EXT | convert.CONVERTIBLE:
                try:
                    text = convert.to_markdown(area, f["name"], 40_000)
                except Exception:  # noqa: BLE001 — okunamayan dosya atlanır
                    text = ""
            note = notes.get(f["name"], {}).get("note", "") if area == "uploads" else ""
            docs.append((f"{'rapor' if area == 'reports' else 'yüklenen dosya'}: {f['name']} ({_local_day(f['modified'])})",
                         f"{f['name']}\n{note}\n{text}"))
    for r in _recent_runs(60):
        docs.append((f"görev/talimat: {r['title']} ({_local_day(r['created'])})", r.get("brief", "")))
        for st in r.get("step_results", []):
            docs.append((f"{st['agent_name']} çıktısı: {r['title']} ({_local_day(r['created'])})", st.get("text", "")))
    for a in storage.read_state("approvals", [])[:200]:
        docs.append((f"onay içeriği ({a['status']}): {a['title']} ({_local_day(a['created'])})",
                     f"{a['title']}\n{a['body']}\nKurucu notu: {a.get('note', '')}"))
    for team, items in storage.read_state("knowledge", {}).items():
        for n in items:
            docs.append((f"bilgi notu: {n['title']}", f"{n['title']}\n{n['text']}\n{n['source_url']}"))
    hits = []
    for ref, text in docs:
        folded = _fold(text)
        score = sum(folded.count(t) for t in terms)
        if not score:
            continue
        pos = min((folded.find(t) for t in terms if t in folded), default=0)
        snippet = text[max(0, pos - 200): pos + 500].replace("\n", " ").strip()
        hits.append({"kaynak": ref, "puan": score, "alinti": snippet})
    hits.sort(key=lambda h: h["puan"], reverse=True)
    return hits[:limit]


def _archive_prompt(agent: dict[str, Any]) -> str:
    """Ajanın her çalışmada gördüğü arşiv özeti: Kurucunun dosyaları ve talimatları, kendi geçmiş işleri, son raporlar."""
    parts = []
    notes = file_notes()
    uploads = [f for f in storage.list_area("uploads")
               if notes.get(f["name"], {}).get("team", "genel") in ("genel", agent["team"]) or agent.get("controller")][:15]
    if uploads:
        parts.append("## Kurucunun yüklediği dosyalar (read_file / view_image ile aç)\n" + "\n".join(
            f"- {f['name']} ({f['kind']}, {_local_day(f['modified'])})"
            + (f": {notes[f['name']]['note']}" if notes.get(f["name"], {}).get("note") else "") for f in uploads))
    runs = _recent_runs(30)
    mine = []
    for r in runs:
        for st in r.get("step_results", []):
            if st["agent"] == agent["id"] and len(mine) < 3:
                mine.append(f"- {_local_day(r['created'])} · {r['title']}: {st['text'][:600]}")
    if mine:
        parts.append("## Senin son işlerin (tekrar etme, üzerine inşa et)\n" + "\n".join(mine))
    orders = [f"- {_local_day(r['created'])} · {r['title']}: {r.get('brief', '')[:400]}"
              for r in runs if r["kind"] == "task" or "Kullanıcı notu:" in r.get("brief", "")][:6]
    orders += [f"- {_local_day(a['decided'])} · {a['title']} ({a['status']}): {a['note'][:300]}"
               for a in storage.read_state("approvals", []) if a.get("note") and a.get("decided")][:6]
    if orders:
        parts.append("## Kurucunun son talimatları ve notları\n" + "\n".join(orders))
    reports = storage.list_area("reports")[:8]
    if reports:
        parts.append("## Son raporlar (read_file ile oku)\n" + "\n".join(f"- {f['name']}" for f in reports))
    parts.append("Daha eski araştırma, karar ve çıktılar için search_archive aracını kullan.")
    return "\n\n".join(parts)[:9000]


def _local_day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def team_activity(team_id: str | None, days: int) -> dict[str, Any]:
    """Yöneticiler için özet. team_id None ise tüm ekip (Ana Kontrolcü)."""
    since = (datetime.now(TZ) - timedelta(days=max(1, min(days, 31)))).astimezone(timezone.utc).isoformat()
    members = set(team_members(team_id)) if team_id else set(AGENTS)
    runs = [r for r in storage.read_state("runs_index", []) if r["created"] >= since and members & set(r["steps"])]
    approvals = [a for a in storage.read_state("approvals", []) if a["created"] >= since and a["agent"] in members]
    stats: dict[str, int] = {}
    for a in approvals:
        stats[a["status"]] = stats.get(a["status"], 0) + 1
    return {
        "donem_gun": days,
        "calistirmalar": [{k: r.get(k) for k in ("title", "status", "created", "cost", "error")}
                          | {"ciktilar": [o.get("name") or o.get("title") for o in r.get("outputs", [])][:12]} for r in runs[:40]],
        "toplam_maliyet_usd": round(sum(r.get("cost", 0) for r in runs), 4),
        "hatali_calistirma": sum(1 for r in runs if r["status"] == "failed"),
        "onay_durumlari": stats,
        "kullanici_notlari": [{"ajan": a["agent_name"], "tur": a["kind"], "baslik": a["title"], "karar": a["status"], "not": a["note"]}
                              for a in approvals if a.get("note")][:30],
        "hafiza_ders_sayisi": {m: len(lessons_for(m)) for m in sorted(members)},
        "aylik_harcama_usd": month_spend(),
    }


def tool_names_for(agent: dict[str, Any]) -> list[str]:
    names = list(BASE_TOOLS)
    if agent.get("media"):
        names += ["save_media_from_url", "submit_for_approval"]
    elif agent["id"] in ("video-editor", "copywriter"):
        names += ["submit_for_approval"]
    if agent.get("alerts"):
        names.append("raise_alert")
    if agent.get("manager"):
        names += MANAGER_TOOLS
    if "virale_generation_status" in agent.get("virale", []):
        names.append("wait")
    if agent.get("library"):
        names += LIBRARY_TOOLS
    if agent.get("products_read"):
        names += [n for n in ("list_products", "product_reference") if n not in names]
    if agent.get("compose"):
        names.append("compose_product")
    if agent.get("studio"):
        names += ["design_image"] + [n for n in ("crop_product", "repair_product_image") if n not in names]
    if agent.get("video"):
        names += ["make_video"]
    if agent.get("product_images") and "save_media_from_url" not in names:
        names.append("save_media_from_url")
    return names


def _blocked_host(host: str) -> bool:
    if not host or host == "localhost" or host.endswith(".internal") or host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, 443)
    except socket.gaierror:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


async def download_media(url: str, filename: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or await asyncio.to_thread(_blocked_host, parsed.hostname or ""):
        raise ValueError("Yalnız herkese açık https adresleri indirilebilir")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
        async with http.stream("GET", url) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0]
            if not (ctype.startswith("image/") or ctype.startswith("video/")):
                raise ValueError(f"Görsel/video değil: {ctype or 'bilinmiyor'}")
            name = storage.safe_name(filename)
            if not Path(name).suffix:
                name += mimetypes.guess_extension(ctype) or ""
            tmp = storage.unique_path("media", name + ".part")
            size, head = 0, b""
            try:
                with tmp.open("wb") as fh:
                    async for chunk in r.aiter_bytes(1024 * 1024):
                        head = head or chunk[:16]
                        size += len(chunk)
                        if size > MAX_MEDIA_BYTES:
                            raise ValueError("Dosya çok büyük")
                        fh.write(chunk)
                # uzantı içerikle uyuşmuyorsa düzelt (ör. jpeg içerikli "x.png")
                real = sniff_image(head)
                if real and IMAGE_TYPES.get(Path(name).suffix.lower()) != real:
                    name = Path(name).stem + IMAGE_EXT[real]
                target = storage.unique_path("media", name)
                tmp.replace(target)
            finally:
                tmp.unlink(missing_ok=True)
    return target.name


class ToolContext:
    def __init__(self, run: "Run", agent: dict[str, Any]) -> None:
        self.run, self.agent = run, agent


async def execute_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
    run, agent = ctx.run, ctx.agent
    if name == "list_files":
        items = storage.list_area(args["area"])[:60]
        return json.dumps([{k: f[k] for k in ("name", "kind", "size", "modified")} for f in items], ensure_ascii=False) or "[]"
    if name == "read_file":
        return convert.to_markdown(args["area"], args["name"])
    if name == "save_report":
        stamp = datetime.now(TZ).strftime("%Y-%m-%d-%H%M")
        path = storage.unique_path("reports", f"{stamp}-{storage.slug(args['title'])}.md")
        header = f"# {args['title']}\n\n> {agent['name']} · {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}\n\n"
        path.write_text(header + args["markdown"], "utf-8")
        run.outputs.append({"type": "report", "name": path.name})
        await storage.log_activity(f"{agent['name']} rapor kaydetti: {args['title']}", "report")
        return f"Kaydedildi: {path.name}"
    if name == "save_media_from_url":
        saved = await download_media(args["url"], args["filename"])
        run.outputs.append({"type": "media", "name": saved})
        await folders.place([saved], folders.folder_for_media(saved, agent, run, TZ))
        await storage.log_activity(f"{agent['name']} medya kaydetti: {saved}", "media")
        return f"Kaydedildi: {saved}"
    if name == "submit_for_approval":
        media = [m for m in args.get("media", []) if (storage.DATA_DIR / "media" / storage.safe_name(m)).is_file()]
        item = {"id": uuid.uuid4().hex[:12], "created": storage.now(), "run_id": run.id, "agent": agent["id"],
                "agent_name": agent["name"], "team": agent["team"], "kind": args["kind"], "title": args["title"][:200],
                "body": args["body"], "media": media, "status": "pending", "note": ""}
        if run.revision_of:
            item["revision_of"] = run.revision_of
        async with storage.state_lock:
            items = storage.read_state("approvals", [])
            for old in items:
                if run.revision_of and old["id"] == run.revision_of:
                    old["revised_by"] = item["id"]
            items.insert(0, item)
            storage.write_state("approvals", items[:500])
        run.outputs.append({"type": "approval", "id": item["id"], "title": item["title"]})
        await storage.log_activity(f"Onaya gönderildi ({args['kind']}): {args['title']}", "approval")
        bus.publish({"type": "approval", "item": {k: item[k] for k in ("id", "kind", "title", "agent_name")}})
        missing = set(args.get("media", [])) - set(media)
        return f"Onaya gönderildi (kimlik {item['id']})." + (f" Bulunamayan medya: {', '.join(missing)}" if missing else "")
    if name == "list_products":
        items = [{**{k: v for k, v in p.items() if k != "meta"},
                  "image_status": {n: products.image_status(p, n) for n in p.get("images", [])}} for p in products.all_products()]
        return json.dumps(items, ensure_ascii=False) if items else "Ürün kütüphanesi boş. Ürün görseli kullanma; Kurucudan ürün fotoğrafı yüklemesini iste."
    if name == "product_reference":
        return json.dumps(products.reference(args["product_id"]), ensure_ascii=False)
    if name == "extract_pdf_images":
        saved = await asyncio.to_thread(products.extract_pdf_images, args["name"])
        await storage.log_activity(f"{agent['name']} katalogdan {len(saved)} görsel çıkardı: {args['name']}", "upload")
        return json.dumps({"cikarilan": saved}, ensure_ascii=False) if saved else "PDF'te yeterli boyutta görsel bulunamadı."
    if name == "crop_product":
        for b in [args["box"], *args["keep_boxes"], *args["remove_boxes"]]:
            if len(b) != 4:
                raise ValueError("Her kutu dört sayı olmalı: [x0, y0, x1, y1]")
        replace = (args.get("replace_image") or "").strip()
        if replace and not any(replace in p.get("images", []) for p in products.all_products()):
            raise ValueError(f"{replace} bir ürün kütüphanesi görseli değil")
        info = await asyncio.to_thread(products.crop_to_library, args["area"], args["name"], args["box"], args["product_name"],
                                       bool(args["clean_background"]), args["keep_boxes"], args["remove_boxes"],
                                       float(args["rotate"]), bool(args["save"]), replace if args["save"] else "")
        preview = base64.b64encode(info.pop("preview")).decode()
        report = (f"Yöntem: {info['method']}. Boyut: {info['size'][0]}×{info['size'][1]} px. Renkler: {', '.join(info['colors'])}."
                  + ("".join(f"\nUyarı: {w}" for w in info["warnings"]) or "\nUyarı yok.")
                  + "\nKontrol görselinde delik, leke, kesik kapak/pompa, zemin artığı veya hale var mı bak.")
        if info["name"] and replace:
            owner = next(p for p in products.all_products() if replace in p.get("images", []))
            await products.set_meta(owner["id"], replace, {"source": info["source"], "warnings": info["warnings"],
                                                           "restored": info.get("restored", 0)})
            await storage.log_activity(f"Ürün görseli yeniden kesildi: {replace}", "media")
            report = f"Güncellendi: media/{replace} (ürün {owner['id']}). " + report
        elif info["name"]:
            existing = next((p for p in products.all_products() if storage.slug(p["name"]) == storage.slug(args["product_name"])), None)
            item = await products.save({"name": args["product_name"],
                                        **({} if existing and existing.get("colors") else {"colors": info["colors"]})}, agent["name"])
            await products.add_image(item["id"], info["name"], bool(args["primary"]))
            await products.set_meta(item["id"], info["name"], {"source": info["source"], "warnings": info["warnings"],
                                                               "restored": info.get("restored", 0)})
            run.outputs.append({"type": "media", "name": info["name"]})
            await folders.place([info["name"]], folders.PRODUCTS)
            await storage.log_activity(f"Ürün kütüphanesine görsel eklendi: {args['product_name']}", "media")
            report = f"Kaydedildi: media/{info['name']} (ürün {item['id']}). " + report
        if not info["name"]:
            report = "Önizleme (kaydedilmedi; beğenirsen save true ile tekrar çağır). " + report
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": preview}},
                {"type": "text", "text": report}]
    if name == "design_image":
        res = await asyncio.to_thread(studio.design_to_media, args["svg"], args["format"], args["filename"], bool(args["transparent"]))
        run.outputs.append({"type": "media", "name": res["name"]})
        await folders.place([res["name"]], folders.folder_for_media(res["name"], agent, run, TZ))
        await storage.log_activity(f"{agent['name']} tasarım üretti: {res['name']}", "media")
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(res["preview"]).decode()}},
                {"type": "text", "text": f"Kaydedildi: media/{res['name']} ({res['size'][0]}×{res['size'][1]}). Önizlemede taşan veya "
                                         "kesilen yazı, okunmayan kontrast, ürünün boyutu ve hizası, boşluk dengesi var mı bak; "
                                         "gerekirse SVG'yi düzeltip yeni adla tekrar üret (en fazla 2 düzeltme)."}]
    if name == "make_video":
        res = await studio.make_video(args["scenes"], args["format"], args["transition"], args["audio"], args["filename"])
        run.outputs.append({"type": "media", "name": res["name"]})
        await folders.place([res["name"]], folders.folder_for_media(res["name"], agent, run, TZ))
        await storage.log_activity(f"{agent['name']} video kurguladı: {res['name']} ({res['seconds']} sn)", "media")
        sheet = await asyncio.to_thread(studio.contact_sheet, storage.DATA_DIR / "media" / res["name"])
        text = f"Kaydedildi: media/{res['name']} ({res['seconds']} sn, {res['size'][0]}×{res['size'][1]})."
        if not sheet:
            return text
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(sheet).decode()}},
                {"type": "text", "text": text + " Kareler soldan sağa zaman sırasıyla; akış ve okunabilirliği kontrol et."}]
    if name == "repair_product_image":
        res = await products.repair(args["product_id"], args["image"])
        await storage.log_activity(f"{agent['name']} ürün görselini onardı: {args['image']}", "media")
        text = (f"{args['image']}: {res['message']} Durum: {res['status']}."
                + "".join(f"\nUyarı: {w}" for w in res["warnings"])
                + "\nKontrol görselinde kapak, pompa, cam ve etiket tam mı bak. Hâlâ eksikse Dosyalar'daki orijinal fotoğraftan "
                  "crop_product ile (replace_image bu görsel) yeniden kes; kapak/cam için keep_boxes ver.")
        if not res.get("preview"):
            return text
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(res["preview"]).decode()}},
                {"type": "text", "text": text}]
    if name == "remove_product_image":
        item = await products.remove_image(args["product_id"], args["image"])
        return f"Kaldırıldı. Ürünün kalan görselleri: {', '.join(item['images']) or 'yok'}; ana görsel: {item.get('primary') or 'yok'}."
    if name == "compose_product":
        if len(args["box"]) != 4:
            raise ValueError("box dört sayı olmalı: [x0, y0, x1, y1]")
        res = await asyncio.to_thread(products.compose_to_media, args["scene"], args["product_id"], args["image"], args["box"],
                                      bool(args["shadow"]), args["filename"])
        run.outputs.append({"type": "media", "name": res["name"]})
        await folders.place([res["name"]], folders.folder_for_media(res["name"], agent, run, TZ))
        await storage.log_activity(f"{agent['name']} ürünü sahneye yerleştirdi: {res['name']}", "media")
        text = (f"Kaydedildi: media/{res['name']}." + (f" Herkese açık bağlantı: {res['url']}" if res["url"] else "")
                + (f" Not: {res['note']}" if res["note"] else "")
                + " Ürünün boyutu, konumu, gölgesi ve sahneyle uyumu doğru mu bak; değilse kutuyu düzeltip tekrar yerleştir.")
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(res["preview"]).decode()}},
                {"type": "text", "text": text}]
    if name == "save_product":
        item = await products.save({k: args.get(k) for k in products.FIELDS}, agent["name"])
        return f"Ürün kaydedildi: {item['id']} · {item['name']}"
    if name == "wait":
        seconds = max(10, min(int(args["seconds"]), 120))
        run.emit("agent_note", agent=agent["id"], text=f"Üretimin bitmesi için {seconds} sn bekliyor")
        await asyncio.sleep(seconds)
        return f"{seconds} sn beklendi. Şimdi durumu kontrol et."
    if name == "view_image":
        path = storage.area_file(args["area"], args["name"])
        if path.suffix.lower() not in IMAGE_TYPES:
            raise ValueError("Yalnız jpg, png, webp, gif görseller açılabilir")
        return [await asyncio.to_thread(image_block, path), {"type": "text", "text": f"{args['area']}/{path.name}"}]
    if name == "search_archive":
        hits = search_archive(args["query"])
        return json.dumps(hits, ensure_ascii=False) if hits else "Arşivde eşleşme bulunamadı."
    if name == "add_lesson":
        if not _can_coach(agent, args["agent_id"]):
            raise ValueError(f"{args['agent_id']} için ders ekleme yetkin yok")
        source = "öz değerlendirme" if args["agent_id"] == agent["id"] else agent["name"]
        lesson = await add_lesson(args["agent_id"], args["text"], source)
        run.emit("lesson", agent=agent["id"], target=args["agent_id"], text=_short(lesson["text"], 300))
        return f"Ders eklendi ({lesson['id']})."
    if name == "list_lessons":
        if not _can_coach(agent, args["agent_id"]):
            raise ValueError(f"{args['agent_id']} için yetkin yok")
        return json.dumps(lessons_for(args["agent_id"]), ensure_ascii=False)
    if name == "remove_lesson":
        if not _can_coach(agent, args["agent_id"]):
            raise ValueError(f"{args['agent_id']} için yetkin yok")
        ok = await remove_lesson(args["agent_id"], args["lesson_id"])
        return "Silindi." if ok else "Ders bulunamadı."
    if name == "add_knowledge":
        team_id = "genel" if agent.get("controller") else agent["team"]
        note = await add_knowledge(team_id, args["title"], args["text"], args["source_url"], agent["name"])
        run.emit("knowledge", agent=agent["id"], text=_short(note["title"], 200))
        return f"Bilgi notu eklendi ({note['id']})."
    if name == "team_activity":
        data = team_activity(None if agent.get("controller") else agent["team"], int(args["days"]))
        return json.dumps(data, ensure_ascii=False)[:15000]
    if name == "raise_alert":
        alert = {"id": uuid.uuid4().hex[:10], "time": storage.now(), "level": args["level"], "title": args["title"][:200],
                 "detail": args["detail"], "agent": agent["name"], "run_id": run.id, "read": False}
        async with storage.state_lock:
            alerts = storage.read_state("alerts", [])
            alerts.insert(0, alert)
            storage.write_state("alerts", alerts[:200])
        await storage.log_activity(f"⚠ {args['level'].upper()}: {args['title']}", "alert")
        bus.publish({"type": "alert", "alert": alert})
        return "Uyarı panelde gösterildi."
    raise ValueError(f"Bilinmeyen araç: {name}")


# ---------------------------------------------------------------- çalıştırma kaydı


class Run:
    def __init__(self, kind: str, title: str, steps: list[str], brief: str, trigger: str, workflow: str | None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.title, self.steps, self.brief = kind, title, steps, brief
        self.trigger, self.workflow = trigger, workflow
        self.status = "queued"
        self.created = storage.now()
        self.finished: str | None = None
        self.cost = 0.0
        self.tokens = {"input": 0, "output": 0, "cache_read": 0}
        self.events: list[dict[str, Any]] = []
        self.step_results: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.generations = 0
        self.error: str | None = None
        self.current_agent: str | None = None
        self.revision_of: str | None = None

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title, "workflow": self.workflow, "trigger": self.trigger,
                "status": self.status, "created": self.created, "finished": self.finished, "steps": self.steps,
                "current_agent": self.current_agent, "cost": round(self.cost, 4), "outputs": self.outputs, "error": self.error,
                "revision_of": self.revision_of}

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "brief": self.brief, "tokens": self.tokens, "events": self.events[-600:],
                "step_results": self.step_results}

    def emit(self, type_: str, **data: Any) -> None:
        event = {"type": type_, "run_id": self.id, "time": storage.now(), **data}
        self.events.append(event)
        bus.publish(event)

    async def save(self) -> None:
        async with storage.state_lock:
            storage.write_state(f"runs/{self.id}", self.to_dict())
            index = [r for r in storage.read_state("runs_index", []) if r["id"] != self.id]
            index.insert(0, self.summary())
            storage.write_state("runs_index", index[:200])


def load_run(run_id: str) -> dict[str, Any] | None:
    if not run_id.isalnum():
        return None
    return storage.read_state(f"runs/{run_id}", None)


# ---------------------------------------------------------------- ajan döngüsü


def _system_prompt(agent: dict[str, Any], settings: dict[str, Any]) -> str:
    comps = "\n".join(f"- {c['name']}" + (f" (@{c['handle']})" if c.get("handle") else " (kullanıcı adı doğrulanmalı)")
                      + (f", ~{c['followers']} takipçi" if c.get("followers") else "") for c in settings["competitors"])
    virale = ("Virale (ChatPlace) araçların açık: Instagram hesap ve içerik verisi"
              + (" ile görsel/video üretimi" if set(agent.get("virale", [])) & GENERATE_TOOLS else "") + "."
              if agent.get("virale") and VIRALE_MCP_URL else
              "Virale bağlantısı bu ortamda kapalı; Instagram verisi için yüklenen dosyaları ve web aramasını kullan, eksikliği raporda belirt."
              if agent.get("virale") else "")
    return (f"{agent['role']}\n\n{COMMON_RULES}\n\n## Marka profili\n{settings['brand']}\n\n## Takip edilen rakipler\n{comps}"
            + (f"\n\n## Araçlar\n{virale}" if virale else ""))


def _memory_prompt(agent: dict[str, Any]) -> str:
    """Değişken kısım: sistem isteminin önbelleğe alınan sabit bölümünden sonra ilk kullanıcı mesajına eklenir."""
    parts = [f"Kimliğin: {agent['id']}."]
    lessons = lessons_for(agent["id"])[-15:]
    if lessons:
        parts.append("## Hafızan (geçmiş çalışmalardan ve geri bildirimlerden öğrendiklerin; bunlara uy)\n"
                     + "\n".join(f"- {x['text']} ({x['source']}, {_local_day(x['time'])})" for x in lessons)[:3500])
    notes = knowledge_for(agent["team"])[-12:]
    if notes:
        parts.append("## Takım bilgi bankası (dış kaynaklı notlar)\n"
                     + "\n".join(f"- **{n['title']}**: {n['text']}" + (f" [{n['source_url']}]" if n["source_url"] else "")
                                  for n in notes)[:4000])
    if agent.get("products_read") or agent.get("library"):
        summary = products.summary_for_prompt()
        parts.append("## BOLBÉ ürün kütüphanesi (ayrıntı: list_products / product_reference)\n"
                     + (summary or "Kütüphane henüz boş."))
    parts.append(_archive_prompt(agent))
    parts.append("İşin sonunda, gelecekte işine yarayacak yeni bir ders çıkardıysan add_lesson ile kendi hafızana en fazla 1 kısa ders ekle.")
    return "\n\n".join(parts)


def _tools_for(agent: dict[str, Any], model: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tools: list[dict[str, Any]] = [
        {"name": n, "description": CUSTOM_TOOLS[n]["description"], "input_schema": CUSTOM_TOOLS[n]["input_schema"], "strict": True}
        for n in tool_names_for(agent)
    ]
    if agent.get("web"):
        tools.append({"type": MODELS[model]["web_search"], "name": "web_search", "max_uses": 8})
    if agent.get("fetch") and MODELS[model]["web_fetch"]:
        tools.append({"type": MODELS[model]["web_fetch"], "name": "web_fetch", "max_uses": 10})
    mcp_servers: list[dict[str, Any]] = []
    if agent.get("virale") and VIRALE_MCP_URL:
        server = {"type": "url", "url": VIRALE_MCP_URL, "name": "virale"}
        if VIRALE_MCP_TOKEN:
            server["authorization_token"] = VIRALE_MCP_TOKEN
        mcp_servers.append(server)
        # Görsel/video üretimi sistem içinde yapılır; Virale'nin ücretli üretim araçları hiçbir ajana açılmaz
        allowed = [n for n in agent["virale"] if n not in GENERATE_TOOLS or VIRALE_GENERATION]
        tools.append({"type": "mcp_toolset", "mcp_server_name": "virale", "default_config": {"enabled": False},
                      "configs": {name: {"enabled": True} for name in allowed}})
    return tools, mcp_servers


def _plain(obj: Any) -> Any:
    """SDK içerik blokları (ör. MCP araç hatasındaki BetaTextBlock) JSON'a çevrilemez; metnine indirger."""
    text = getattr(obj, "text", None)
    if isinstance(text, str):
        return text
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def _short(value: Any, n: int = 220) -> str:
    if isinstance(value, list) and all(isinstance(getattr(v, "text", None), str) for v in value):
        value = "\n".join(v.text for v in value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=_plain)
    return text if len(text) <= n else text[: n - 1] + "…"


def _drop_tool_images(messages: list[dict[str, Any]]) -> bool:
    changed = False
    for msg in messages:
        if msg["role"] != "user" or not isinstance(msg["content"], list):
            continue
        for block in msg["content"]:
            if block.get("type") != "tool_result" or not isinstance(block.get("content"), list):
                continue
            for i, part in enumerate(block["content"]):
                if part.get("type") == "image":
                    block["content"][i] = {"type": "text", "text": "(Görsel modele gönderilemedi; bu görseli inceleyemezsin, "
                                                                   "durumu raporda belirt.)"}
                    changed = True
    return changed


async def run_agent(run: Run, agent_id: str, task: str) -> str:
    agent = AGENTS[agent_id]
    settings = get_settings()
    model = model_for(agent_id, settings)
    tools, mcp_servers = _tools_for(agent, model)
    betas = ([FALLBACK_BETA] if MODELS[model]["fallback"] else []) + ([MCP_BETA] if mcp_servers else [])
    today = datetime.now(TZ).strftime("%d.%m.%Y %A")
    messages: list[dict[str, Any]] = [{"role": "user", "content": f"Bugün: {today}\n\n{_memory_prompt(agent)}\n\n{task}"}]
    ctx = ToolContext(run, agent)
    run.current_agent = agent_id
    run.emit("agent_start", agent=agent_id, agent_name=agent["name"], team=agent["team"], model=MODELS[model]["label"])
    final_text = ""

    for turn in range(MAX_TURNS):
        if month_spend() + run.cost >= MONTHLY_BUDGET_USD:
            raise RuntimeError(f"Aylık bütçe ({MONTHLY_BUDGET_USD:.0f} $) doldu")
        kwargs: dict[str, Any] = dict(
            model=model, max_tokens=16000, betas=betas, system=_system_prompt(agent, settings),
            tools=tools, messages=messages, thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": agent.get("effort", "medium")}, cache_control={"type": "ephemeral"},
        )
        if MODELS[model]["fallback"]:
            kwargs["fallbacks"] = "default"
        if mcp_servers:
            kwargs["mcp_servers"] = mcp_servers
        try:
            response = await client().beta.messages.create(**kwargs)
        except anthropic.RateLimitError:
            run.emit("agent_note", agent=agent_id, text="Hız sınırı; 30 sn bekleniyor")
            await asyncio.sleep(30)
            continue
        except anthropic.BadRequestError as exc:
            # bozuk bir görsel tüm adımı düşürmesin: araç sonuçlarındaki görselleri metne çevirip bir kez daha dene
            if "image" not in str(exc) or not _drop_tool_images(messages):
                raise
            run.emit("agent_note", agent=agent_id, text="Bir görsel modele gönderilemedi; görseller metin notuyla değiştirilip devam ediliyor")
            continue

        u = response.usage
        cost = usage_cost(u, model)
        run.cost += cost
        run.tokens["input"] += u.input_tokens or 0
        run.tokens["output"] += u.output_tokens or 0
        run.tokens["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        await add_spend(cost)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(f"Model isteği reddetti ({getattr(details, 'category', None) or 'kategori yok'})")

        for block in response.content:
            btype = getattr(block, "type", "")
            if btype == "thinking" and getattr(block, "thinking", ""):
                run.emit("thinking", agent=agent_id, text=_short(block.thinking, 400))
            elif btype == "text" and block.text.strip():
                run.emit("text", agent=agent_id, text=_short(block.text, 600))
            elif btype == "server_tool_use":
                run.emit("tool", agent=agent_id, tool=block.name, detail=_short(getattr(block, "input", {})))
            elif btype == "mcp_tool_use":
                run.emit("tool", agent=agent_id, tool=block.name, detail=_short(getattr(block, "input", {})))
                if block.name in GENERATE_TOOLS:
                    run.generations += 1
            elif btype == "mcp_tool_result" and getattr(block, "is_error", False):
                run.emit("tool_error", agent=agent_id, detail=_short(getattr(block, "content", ""), 300))

        messages.append({"role": "assistant", "content": response.content})
        texts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        if texts:
            final_text = "\n".join(texts)

        if run.generations > MAX_GENERATIONS_PER_RUN:
            run.emit("agent_note", agent=agent_id, text=f"Üretim sınırı ({MAX_GENERATIONS_PER_RUN}) aşıldı; ajan durduruldu")
            break
        if response.stop_reason in ("end_turn", "stop_sequence"):
            break
        if response.stop_reason in ("pause_turn",):
            continue
        if response.stop_reason == "max_tokens":
            messages.append({"role": "user", "content": "Kaldığın yerden devam et."})
            continue

        tool_results = []
        for block in response.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            run.emit("tool", agent=agent_id, tool=block.name, detail=_short(block.input))
            try:
                result, is_error = await execute_tool(ctx, block.name, block.input), False
            except Exception as exc:  # noqa: BLE001 — hata modele araç sonucu olarak döner
                result, is_error = f"Hata: {exc}", True
                run.emit("tool_error", agent=agent_id, detail=_short(result, 300))
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result, "is_error": is_error})
        if not tool_results:
            break
        messages.append({"role": "user", "content": tool_results})
    else:
        run.emit("agent_note", agent=agent_id, text=f"Tur sınırına ({MAX_TURNS}) ulaşıldı")

    run.emit("agent_done", agent=agent_id, agent_name=agent["name"], text=_short(final_text, 800))
    await run.save()
    return final_text


# ---------------------------------------------------------------- iş akışı


def _step_task(run: Run, index: int, agent_id: str) -> str:
    agent = AGENTS[agent_id]
    parts = [f"## Görev ({run.title})\n{run.brief}"]
    if len(run.steps) > 1:
        names = " → ".join(AGENTS[s]["name"] for s in run.steps)
        parts.append(f"## Akış\nBu akışta sıra: {names}. Sen {index + 1}. adımsın ({agent['name']}).")
    for prev in run.step_results:
        parts.append(f"## {prev['agent_name']} çıktısı\n{prev['text'][:30000]}")
    return "\n\n".join(parts)


async def execute_run(run: Run) -> None:
    async with _semaphore:
        run.status = "running"
        bus.live[run.id] = run.summary()
        await run.save()
        run.emit("run_start", title=run.title, steps=run.steps)
        await storage.log_activity(f"Başladı: {run.title}", "run")
        try:
            if not api_ready():
                raise RuntimeError("ANTHROPIC_API_KEY tanımlı değil")
            for i, agent_id in enumerate(run.steps):
                text = await run_agent(run, agent_id, _step_task(run, i, agent_id))
                run.step_results.append({"agent": agent_id, "agent_name": AGENTS[agent_id]["name"], "text": text})
                bus.live[run.id] = run.summary()
            run.status = "done"
            await storage.log_activity(f"Tamamlandı: {run.title} ({run.cost:.2f} $)", "done")
        except Exception as exc:  # noqa: BLE001 — çalıştırma hatası panelde gösterilir
            run.status, run.error = "failed", str(exc)[:500]
            await storage.log_activity(f"Hata: {run.title} — {run.error}", "error")
        finally:
            run.finished, run.current_agent = storage.now(), None
            run.emit("run_end", status=run.status, error=run.error, cost=round(run.cost, 4))
            bus.live.pop(run.id, None)
            await run.save()


def _launch(run: Run) -> Run:
    task = asyncio.create_task(run.save())
    _background.add(task)
    task.add_done_callback(_background.discard)
    job = asyncio.create_task(execute_run(run))
    _background.add(job)
    job.add_done_callback(_background.discard)
    return run


def start_workflow(workflow_id: str, trigger: str, note: str = "") -> Run:
    wf = WORKFLOW_BY_ID[workflow_id]
    brief = wf["brief"] + (f"\n\nKullanıcı notu: {note}" if note else "")
    return _launch(Run("workflow", wf["name"], list(wf["steps"]), brief, trigger, workflow_id))


def start_agent_task(agent_id: str, title: str, prompt: str, trigger: str = "manual") -> Run:
    return _launch(Run("task", title, [agent_id], prompt, trigger, None))


def start_revision(item: dict[str, Any], steps: list[str], prompt: str) -> Run:
    run = Run("task", f"Revizyon: {item['title']}", steps, prompt, "manual", None)
    run.revision_of = item["id"]
    return _launch(run)


# ---------------------------------------------------------------- zamanlayıcı


def _due(schedule: dict[str, Any], last: str | None, now: datetime) -> bool:
    if schedule["kind"] == "manual" or last == now.strftime("%Y-%m-%d"):
        return False
    if (now.hour, now.minute) < (schedule["hour"], schedule["minute"]):
        return False
    if schedule["kind"] == "weekly":
        return now.weekday() == schedule["weekday"]
    if schedule["kind"] == "monthly":
        return now.day == schedule["day"]
    return False


def next_run_label(schedule: dict[str, Any]) -> str:
    if schedule["kind"] == "manual":
        return "Elle çalıştırılır"
    days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    t = f"{schedule['hour']:02d}:{schedule['minute']:02d}"
    return f"Her {days[schedule['weekday']]} {t}" if schedule["kind"] == "weekly" else f"Her ayın {schedule['day']}. günü {t}"


async def scheduler_loop() -> None:
    while True:
        try:
            now = datetime.now(TZ)
            settings = get_settings()
            last_runs = storage.read_state("schedule_last", {})
            for wf in WORKFLOWS:
                if not settings["schedules"].get(wf["id"], {}).get("enabled"):
                    continue
                if not _due(wf["schedule"], last_runs.get(wf["id"]), now):
                    continue
                last_runs[wf["id"]] = now.strftime("%Y-%m-%d")
                storage.write_state("schedule_last", last_runs)
                if not api_ready() or month_spend() >= MONTHLY_BUDGET_USD:
                    await storage.log_activity(f"Zamanlanmış akış atlandı (API anahtarı/bütçe): {wf['name']}", "error")
                    continue
                start_workflow(wf["id"], "schedule")
        except Exception as exc:  # noqa: BLE001 — zamanlayıcı hiçbir hatada durmamalı
            await storage.log_activity(f"Zamanlayıcı hatası: {exc}", "error")
        await asyncio.sleep(60)
