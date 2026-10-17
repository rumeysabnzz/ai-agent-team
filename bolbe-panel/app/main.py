"""BOLBÉ AI Komuta Merkezi: web sunucusu.

Şifreli giriş, dosyalar, ajan ekibi, iş akışları, onay kuyruğu, uyarılar,
ayarlar ve canlı olay akışı (SSE).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import runner, storage
from .team import AGENTS, WORKFLOW_BY_ID, WORKFLOWS, public_team_view

PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
COOKIE_NAME = "bolbe_session"
STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------- oturum


def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{_sign(expires)}"


def valid_session(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    return hmac.compare_digest(sig, _sign(expires)) and expires.isdigit() and int(expires) > time.time()


def require_user(request: Request) -> None:
    if not valid_session(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(401, "Giriş gerekli")


_login_attempts: dict[str, list[float]] = {}


def _rate_limited(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _login_attempts.get(ip, []) if now - t < 600]
    _login_attempts[ip] = recent
    return len(recent) >= 10


# ---------------------------------------------------------------- uygulama


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = [n for n, v in (("PANEL_PASSWORD", PANEL_PASSWORD), ("SESSION_SECRET", SESSION_SECRET)) if len(v) < 8]
    if missing:
        raise RuntimeError(f"Eksik veya çok kısa ortam değişkeni: {', '.join(missing)} (en az 8 karakter)")
    storage.ensure_dirs()
    # Yeniden başlatmada yarım kalan çalıştırmaları işaretle
    index = storage.read_state("runs_index", [])
    for r in index:
        if r.get("status") in ("queued", "running"):
            r["status"], r["error"] = "failed", "Panel yeniden başladı, çalıştırma yarıda kaldı."
    storage.write_state("runs_index", index)
    task = asyncio.create_task(runner.scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="BOLBÉ AI Komuta Merkezi", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
auth = [Depends(require_user)]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody, request: Request) -> JSONResponse:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    if _rate_limited(ip):
        raise HTTPException(429, "Çok fazla deneme. 10 dakika sonra tekrar deneyin.")
    if not hmac.compare_digest(body.password, PANEL_PASSWORD):
        _login_attempts.setdefault(ip, []).append(time.time())
        await asyncio.sleep(1)
        raise HTTPException(401, "Parola hatalı")
    resp = JSONResponse({"ok": True})
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(COOKIE_NAME, make_session(), max_age=SESSION_DAYS * 86400, httponly=True, secure=secure, samesite="lax")
    return resp


@app.post("/api/logout")
async def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/api/me", dependencies=auth)
async def me() -> dict[str, Any]:
    return {"ok": True}


# ---------------------------------------------------------------- genel görünüm ve canlı akış


@app.get("/api/overview", dependencies=auth)
async def overview() -> dict[str, Any]:
    approvals = storage.read_state("approvals", [])
    alerts = storage.read_state("alerts", [])
    return {
        "teams": public_team_view(),
        "live": list(runner.bus.live.values()),
        "integrations": {"claude": runner.api_ready(), "virale": bool(runner.VIRALE_MCP_URL), "model": runner.MODEL},
        "budget": {"month": runner.month_key(), "spent": round(runner.month_spend(), 2), "limit": runner.MONTHLY_BUDGET_USD},
        "stats": {
            "pending": sum(1 for a in approvals if a["status"] == "pending"),
            "reports": len(storage.list_area("reports")),
            "media": len(storage.list_area("media")),
            "alerts": sum(1 for a in alerts if not a.get("read")),
        },
        "alerts": alerts[:5],
        "activity": storage.read_state("activity", [])[:40],
        "runs": storage.read_state("runs_index", [])[:6],
    }


@app.get("/api/stream", dependencies=auth)
async def stream(request: Request) -> StreamingResponse:
    queue = runner.bus.subscribe()

    async def events():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            runner.bus.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- dosyalar


@app.get("/api/files/{area}", dependencies=auth)
async def files(area: str) -> list[dict[str, Any]]:
    if area not in storage.AREAS:
        raise HTTPException(404, "Alan bulunamadı")
    return storage.list_area(area)


@app.post("/api/files/{area}", dependencies=auth)
async def upload_files(area: str, files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
    if area not in storage.AREAS:
        raise HTTPException(404, "Alan bulunamadı")
    saved = [storage.file_info(area, await storage.save_upload(area, f)) for f in files]
    await storage.log_activity(f"{len(saved)} dosya yüklendi ({area}): " + ", ".join(s["name"] for s in saved), "upload")
    return saved


@app.get("/api/files/{area}/{name}", dependencies=auth)
async def get_file(area: str, name: str, download: bool = False) -> FileResponse:
    path = storage.area_file(area, name)
    # HTML ve SVG panel kökeninde çalıştırılmaz; her zaman indirme olarak verilir
    as_attachment = download or path.suffix.lower() in {".html", ".htm", ".svg"}
    return FileResponse(path, filename=path.name, content_disposition_type="attachment" if as_attachment else "inline")


@app.delete("/api/files/{area}/{name}", dependencies=auth)
async def delete_file(area: str, name: str) -> dict[str, Any]:
    storage.area_file(area, name).unlink()
    await storage.log_activity(f"Dosya silindi ({area}): {name}", "delete")
    return {"ok": True}


# ---------------------------------------------------------------- iş akışları ve görevler


class RunBody(BaseModel):
    note: str = Field("", max_length=4000)


class ScheduleBody(BaseModel):
    enabled: bool


class AgentTaskBody(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    prompt: str = Field(min_length=5, max_length=20_000)
    attachments: list[str] = Field(default_factory=list)


def _guard_budget() -> None:
    if not runner.api_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY tanımlı değil. Railway değişkenlerine ekleyin.")
    if runner.month_spend() >= runner.MONTHLY_BUDGET_USD:
        raise HTTPException(400, f"Bu ayın bütçesi ({runner.MONTHLY_BUDGET_USD:.0f} $) doldu.")


@app.get("/api/workflows", dependencies=auth)
async def workflows() -> list[dict[str, Any]]:
    settings = runner.get_settings()
    last = storage.read_state("schedule_last", {})
    index = storage.read_state("runs_index", [])
    out = []
    for wf in WORKFLOWS:
        recent = next((r for r in index if r.get("workflow") == wf["id"]), None)
        out.append({
            "id": wf["id"], "team": wf["team"], "name": wf["name"], "brief": wf["brief"],
            "steps": [{"id": s, "name": AGENTS[s]["name"]} for s in wf["steps"]],
            "schedule": runner.next_run_label(wf["schedule"]),
            "enabled": settings["schedules"].get(wf["id"], {}).get("enabled", True),
            "last_scheduled": last.get(wf["id"]), "last_run": recent,
        })
    return out


@app.post("/api/workflows/{workflow_id}/run", dependencies=auth)
async def run_workflow(workflow_id: str, body: RunBody) -> dict[str, Any]:
    if workflow_id not in WORKFLOW_BY_ID:
        raise HTTPException(404, "Akış bulunamadı")
    _guard_budget()
    return runner.start_workflow(workflow_id, "manual", body.note.strip()).summary()


@app.put("/api/workflows/{workflow_id}/schedule", dependencies=auth)
async def set_schedule(workflow_id: str, body: ScheduleBody) -> dict[str, Any]:
    if workflow_id not in WORKFLOW_BY_ID:
        raise HTTPException(404, "Akış bulunamadı")
    async with storage.state_lock:
        settings = runner.get_settings()
        settings["schedules"][workflow_id] = {"enabled": body.enabled}
        storage.write_state("settings", settings)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/task", dependencies=auth)
async def agent_task(agent_id: str, body: AgentTaskBody) -> dict[str, Any]:
    if agent_id not in AGENTS:
        raise HTTPException(404, "Ajan bulunamadı")
    _guard_budget()
    prompt = body.prompt.strip()
    names = [storage.safe_name(a) for a in body.attachments]
    if names:
        prompt += "\n\nEk dosyalar (uploads alanında, read_file ile oku): " + ", ".join(names)
    return runner.start_agent_task(agent_id, body.title.strip(), prompt).summary()


@app.get("/api/runs", dependencies=auth)
async def runs() -> list[dict[str, Any]]:
    return storage.read_state("runs_index", [])


@app.get("/api/runs/{run_id}", dependencies=auth)
async def run_detail(run_id: str) -> dict[str, Any]:
    data = runner.load_run(run_id)
    if not data:
        raise HTTPException(404, "Çalıştırma bulunamadı")
    return data


# ---------------------------------------------------------------- onay kuyruğu ve uyarılar


class ApprovalAction(BaseModel):
    action: str = Field(pattern="^(approve|reject|revise)$")
    note: str = Field("", max_length=4000)


@app.get("/api/approvals", dependencies=auth)
async def approvals() -> list[dict[str, Any]]:
    return storage.read_state("approvals", [])


@app.post("/api/approvals/{item_id}", dependencies=auth)
async def act_on_approval(item_id: str, body: ApprovalAction) -> dict[str, Any]:
    async with storage.state_lock:
        items = storage.read_state("approvals", [])
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            raise HTTPException(404, "Kayıt bulunamadı")
        item["status"] = {"approve": "approved", "reject": "rejected", "revise": "revision"}[body.action]
        item["note"], item["decided"] = body.note.strip(), storage.now()
        storage.write_state("approvals", items)
    label = {"approve": "Onaylandı", "reject": "Reddedildi", "revise": "Revize istendi"}[body.action]
    await storage.log_activity(f"{label}: {item['title']}", "approval")
    result: dict[str, Any] = {"ok": True, "item": item}
    if body.action == "revise":
        _guard_budget()
        prompt = (f"Kullanıcı şu içeriğin revizyonunu istedi.\n\nTür: {item['kind']}\nBaşlık: {item['title']}\n"
                  f"Medya: {', '.join(item['media']) or 'yok'}\n\nMevcut içerik:\n{item['body']}\n\n"
                  f"Kullanıcının geri bildirimi:\n{body.note or '(not yok, kaliteyi artır)'}\n\n"
                  "Geri bildirime göre içeriği yenile ve yeni sürümü onaya gönder.")
        result["run"] = runner.start_agent_task(item["agent"], f"Revizyon: {item['title']}", prompt).summary()
    return result


@app.get("/api/alerts", dependencies=auth)
async def alerts() -> list[dict[str, Any]]:
    return storage.read_state("alerts", [])


@app.post("/api/alerts/read", dependencies=auth)
async def alerts_read() -> dict[str, Any]:
    async with storage.state_lock:
        items = storage.read_state("alerts", [])
        for a in items:
            a["read"] = True
        storage.write_state("alerts", items)
    return {"ok": True}


# ---------------------------------------------------------------- ayarlar


class Competitor(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    handle: str = Field("", max_length=60, pattern=r"^[A-Za-z0-9._]*$")
    followers: str = Field("", max_length=20)


class SettingsBody(BaseModel):
    brand: str = Field(min_length=20, max_length=8000)
    competitors: list[Competitor] = Field(max_length=15)


@app.get("/api/settings", dependencies=auth)
async def get_settings() -> dict[str, Any]:
    s = runner.get_settings()
    return {"brand": s["brand"], "competitors": s["competitors"],
            "integrations": {"claude": runner.api_ready(), "virale": bool(runner.VIRALE_MCP_URL), "model": runner.MODEL},
            "budget": {"spent": round(runner.month_spend(), 2), "limit": runner.MONTHLY_BUDGET_USD},
            "limits": {"generations_per_run": runner.MAX_GENERATIONS_PER_RUN, "turns": runner.MAX_TURNS}}


@app.put("/api/settings", dependencies=auth)
async def put_settings(body: SettingsBody) -> dict[str, Any]:
    async with storage.state_lock:
        s = runner.get_settings()
        s["brand"] = body.brand.strip()
        s["competitors"] = [c.model_dump() for c in body.competitors]
        storage.write_state("settings", s)
    await storage.log_activity("Ayarlar güncellendi (marka profili / rakipler)", "info")
    return {"ok": True}


# ---------------------------------------------------------------- arayüz


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/manifest.webmanifest")
async def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
