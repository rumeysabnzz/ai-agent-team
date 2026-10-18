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
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import convert, runner, storage
from .team import AGENTS, REVISION_ROUTES, WORKFLOW_BY_ID, WORKFLOWS, public_management_view, public_team_view

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
        "management": public_management_view(),
        "live": list(runner.bus.live.values()),
        "integrations": {"claude": runner.api_ready(), "virale": bool(runner.VIRALE_MCP_URL), "model": runner.model_summary()},
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
    items = storage.list_area(area)
    if area == "uploads":
        notes = runner.file_notes()
        for f in items:
            f.update(notes.get(f["name"], {}))
    return items


@app.post("/api/files/{area}", dependencies=auth)
async def upload_files(area: str, files: list[UploadFile] = File(...), note: str = Form(""),
                       team: str = Form("genel")) -> list[dict[str, Any]]:
    if area not in storage.AREAS:
        raise HTTPException(404, "Alan bulunamadı")
    saved = [storage.file_info(area, await storage.save_upload(area, f)) for f in files]
    if area == "uploads" and (note.strip() or team != "genel"):
        await _set_file_notes([s["name"] for s in saved], note, team)
    await storage.log_activity(f"{len(saved)} dosya yüklendi ({area}): " + ", ".join(s["name"] for s in saved), "upload")
    return saved


@app.get("/api/files/{area}/{name}/markdown", dependencies=auth)
async def file_markdown(area: str, name: str) -> dict[str, Any]:
    """Dosyanın ajanların okuduğu Markdown hâli (PDF, Word, Excel, PowerPoint dönüştürülür)."""
    return {"name": name, "markdown": await asyncio.to_thread(convert.to_markdown, area, name, 200_000)}


@app.get("/api/files/{area}/{name}", dependencies=auth)
async def get_file(area: str, name: str, download: bool = False) -> FileResponse:
    path = storage.area_file(area, name)
    # HTML ve SVG panel kökeninde çalıştırılmaz; her zaman indirme olarak verilir
    as_attachment = download or path.suffix.lower() in {".html", ".htm", ".svg"}
    return FileResponse(path, filename=path.name, content_disposition_type="attachment" if as_attachment else "inline")


TEAM_IDS = {"genel", "takim-1", "takim-2", "takim-3", "takim-4"}


async def _set_file_notes(names: list[str], note: str, team: str) -> None:
    if team not in TEAM_IDS:
        raise HTTPException(400, "Geçersiz takım")
    async with storage.state_lock:
        notes = storage.read_state("file_notes", {})
        for name in names:
            notes[name] = {"note": note.strip()[:1000], "team": team}
        storage.write_state("file_notes", notes)


class FileNoteBody(BaseModel):
    note: str = Field("", max_length=1000)
    team: str = "genel"


@app.put("/api/files/uploads/{name}/note", dependencies=auth)
async def put_file_note(name: str, body: FileNoteBody) -> dict[str, Any]:
    storage.area_file("uploads", name)
    await _set_file_notes([name], body.note, body.team)
    return {"ok": True}


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
    items = storage.read_state("approvals", [])
    runs = {r["id"]: r for r in storage.read_state("runs_index", [])}
    for item in items:
        run = runs.get(item.get("revision_run", ""))
        if run:
            item["revision_status"], item["revision_error"] = run["status"], run.get("error")
    return items


@app.get("/api/approvals/{item_id}/history", dependencies=auth)
async def approval_history(item_id: str) -> dict[str, Any]:
    """Bir içeriğin tüm sürüm zinciri: ilk çıktı → revizeler → son sürüm; her sürümün çalıştırması ve Kurucu notu."""
    items = {i["id"]: i for i in storage.read_state("approvals", [])}
    if item_id not in items:
        raise HTTPException(404, "Kayıt bulunamadı")
    first = items[item_id]
    seen = {first["id"]}
    while first.get("revision_of") in items and first["revision_of"] not in seen:
        first = items[first["revision_of"]]
        seen.add(first["id"])
    chain, cur = [], first
    while cur:
        chain.append(cur)
        nxt = items.get(cur.get("revised_by", ""))
        cur = nxt if nxt and nxt["id"] not in {c["id"] for c in chain} else None
    runs = {r["id"]: r for r in storage.read_state("runs_index", [])}
    versions = []
    for n, item in enumerate(chain, 1):
        made_by, revision = runs.get(item.get("run_id", "")), runs.get(item.get("revision_run", ""))
        versions.append({**item, "version": n,
                         "made_by_run": {k: made_by[k] for k in ("id", "title", "status", "created", "finished", "cost", "steps")} if made_by else None,
                         "revision_run_info": {k: revision[k] for k in ("id", "title", "status", "created", "finished", "error", "steps")} if revision else None})
    return {"id": item_id, "title": chain[0]["title"], "versions": versions}


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
    # Kullanıcının kararı ve notu, içeriği üreten ajanların hafızasına ders olarak yazılır.
    agent = AGENTS.get(item["agent"], {})
    steps = [item["agent"]] if agent.get("media") else REVISION_ROUTES.get(item["kind"], [item["agent"]])
    if body.note.strip() or body.action == "reject":
        verdict = {"approve": "onayladı", "reject": "reddetti", "revise": "revize istedi"}[body.action]
        text = f"Kullanıcı {item['kind']} içeriğini {verdict} (\"{item['title'][:80]}\")" + (f": {body.note.strip()[:400]}" if body.note.strip() else ".")
        for agent_id in dict.fromkeys(steps + [item["agent"]]):
            await runner.add_lesson(agent_id, text, "Kurucu geri bildirimi")
    if body.action == "revise":
        _guard_budget()
        names = " → ".join(AGENTS[a]["name"] for a in steps)
        prompt = (f"Kullanıcı şu içeriğin revizyonunu istedi. Revizyon sırası: {names}.\n\nTür: {item['kind']}\nBaşlık: {item['title']}\n"
                  f"Önceki sürümü hazırlayan: {item['agent_name']}\nMevcut medya: {', '.join(item['media']) or 'yok'}\n\n"
                  f"Mevcut içerik:\n{item['body']}\n\n"
                  f"Kullanıcının geri bildirimi:\n{body.note or '(not yok, kaliteyi artır)'}\n\n"
                  "Geri bildirime göre içeriği baştan yenile. Görsel veya video içeriyorsa yeni medyayı Virale ile üret, üretim bitene kadar "
                  "durumunu kontrol et ve panele kaydet. Son adımda yeni sürümü submit_for_approval ile onaya gönder; "
                  "üretim yapılamazsa nedenini gövdeye yazarak yine onaya gönder.")
        run = runner.start_revision(item, steps, prompt)
        async with storage.state_lock:
            items = storage.read_state("approvals", [])
            for i in items:
                if i["id"] == item_id:
                    i["revision_run"] = run.id
            storage.write_state("approvals", items)
        result["run"] = run.summary()
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


# ---------------------------------------------------------------- takvim


def _day_of(iso: str | None) -> str:
    return runner._local_day(iso or "")


@app.get("/api/calendar", dependencies=auth)
async def calendar(month: str = "") -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = runner.month_key()
    days: dict[str, dict[str, int]] = {}

    def bump(day: str, key: str) -> None:
        if day.startswith(month):
            days.setdefault(day, {}).setdefault(key, 0)
            days[day][key] += 1

    for r in storage.read_state("runs_index", []):
        bump(_day_of(r["created"]), "runs")
    for area, key in (("reports", "reports"), ("media", "media")):
        for f in storage.list_area(area):
            bump(_day_of(f["modified"]), key)
    for a in storage.read_state("approvals", []):
        bump(_day_of(a["created"]), "approvals")
    for a in storage.read_state("alerts", []):
        bump(_day_of(a["time"]), "alerts")
    return {"month": month, "today": datetime.now(runner.TZ).strftime("%Y-%m-%d"), "days": days}


@app.get("/api/day/{day}", dependencies=auth)
async def day_detail(day: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(400, "Tarih YYYY-AA-GG olmalı")
    return {
        "day": day,
        "runs": [r for r in storage.read_state("runs_index", []) if _day_of(r["created"]) == day],
        "reports": [f for f in storage.list_area("reports") if _day_of(f["modified"]) == day],
        "media": [f for f in storage.list_area("media") if _day_of(f["modified"]) == day],
        "approvals": [a for a in storage.read_state("approvals", []) if _day_of(a["created"]) == day],
        "alerts": [a for a in storage.read_state("alerts", []) if _day_of(a["time"]) == day],
        "activity": [a for a in storage.read_state("activity", []) if _day_of(a["time"]) == day],
    }


# ---------------------------------------------------------------- gelişim: hafıza ve bilgi bankası


@app.get("/api/development", dependencies=auth)
async def development() -> dict[str, Any]:
    memory = storage.read_state("memory", {})
    reports = [f for f in storage.list_area("reports") if "yonetim" in f["name"]][:12]
    return {"memory": {aid: memory.get(aid, []) for aid in AGENTS},
            "knowledge": storage.read_state("knowledge", {}),
            "management_reports": reports,
            "last_management_run": next((r for r in storage.read_state("runs_index", [])
                                         if r.get("workflow") == "haftalik-yonetim"), None)}


class LessonBody(BaseModel):
    text: str = Field(min_length=3, max_length=600)


@app.post("/api/development/lessons/{agent_id}", dependencies=auth)
async def post_lesson(agent_id: str, body: LessonBody) -> dict[str, Any]:
    if agent_id not in AGENTS:
        raise HTTPException(404, "Ajan bulunamadı")
    lesson = await runner.add_lesson(agent_id, body.text, "Kurucu")
    await storage.log_activity(f"Ders eklendi → {AGENTS[agent_id]['name']}", "info")
    return lesson


@app.delete("/api/development/lessons/{agent_id}/{lesson_id}", dependencies=auth)
async def delete_lesson(agent_id: str, lesson_id: str) -> dict[str, Any]:
    if agent_id not in AGENTS or not await runner.remove_lesson(agent_id, lesson_id):
        raise HTTPException(404, "Ders bulunamadı")
    return {"ok": True}


class KnowledgeBody(BaseModel):
    team: str = Field(pattern=r"^(genel|takim-[1-4])$")
    title: str = Field(min_length=2, max_length=160)
    text: str = Field(min_length=3, max_length=1500)
    source_url: str = Field("", max_length=400)


@app.post("/api/development/knowledge", dependencies=auth)
async def post_knowledge(body: KnowledgeBody) -> dict[str, Any]:
    note = await runner.add_knowledge(body.team, body.title, body.text, body.source_url, "Kurucu")
    await storage.log_activity(f"Bilgi bankasına not eklendi: {body.title}", "info")
    return note


@app.delete("/api/development/knowledge/{team}/{note_id}", dependencies=auth)
async def delete_knowledge(team: str, note_id: str) -> dict[str, Any]:
    if not await runner.remove_knowledge(team, note_id):
        raise HTTPException(404, "Not bulunamadı")
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
            "models": {aid: runner.model_for(aid, s) for aid in AGENTS},
            "model_defaults": {aid: a["model"] for aid, a in AGENTS.items()},
            "model_choices": [{"id": mid, "label": m["label"], "note": m["note"], "price": m["price"]}
                              for mid, m in runner.MODELS.items()],
            "integrations": {"claude": runner.api_ready(), "virale": bool(runner.VIRALE_MCP_URL), "model": runner.model_summary()},
            "budget": {"spent": round(runner.month_spend(), 2), "limit": runner.MONTHLY_BUDGET_USD},
            "limits": {"generations_per_run": runner.MAX_GENERATIONS_PER_RUN, "turns": runner.MAX_TURNS},
            "version": BUILD_VERSION}


@app.put("/api/settings", dependencies=auth)
async def put_settings(body: SettingsBody) -> dict[str, Any]:
    async with storage.state_lock:
        s = runner.get_settings()
        s["brand"] = body.brand.strip()
        s["competitors"] = [c.model_dump() for c in body.competitors]
        storage.write_state("settings", s)
    await storage.log_activity("Ayarlar güncellendi (marka profili / rakipler)", "info")
    return {"ok": True}


class ModelsBody(BaseModel):
    models: dict[str, str] = Field(max_length=40)


@app.put("/api/settings/models", dependencies=auth)
async def put_models(body: ModelsBody) -> dict[str, Any]:
    for agent_id, model in body.models.items():
        if agent_id not in AGENTS or model not in runner.MODELS:
            raise HTTPException(400, f"Geçersiz ajan veya model: {agent_id} → {model}")
    async with storage.state_lock:
        s = runner.get_settings()
        s["models"].update(body.models)
        storage.write_state("settings", s)
    await storage.log_activity(f"Ajan modelleri güncellendi: {runner.model_summary()}", "info")
    return {"ok": True, "summary": runner.model_summary()}


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


def _build_version() -> str:
    sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")[:7]
    if sha:
        return sha
    latest = max(p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file())
    return hashlib.sha1(str(latest).encode()).hexdigest()[:7]


BUILD_VERSION = _build_version()


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):  # type: ignore[no-untyped-def]
    # Tarayıcı eski arayüz dosyalarını önbellekten kullanmasın; her yüklemede ETag ile doğrulanır.
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/sw.js", "/manifest.webmanifest"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def index() -> Response:
    # Sürüm etiketi değişince tarayıcı arayüz dosyalarını yeni adresten ister.
    html = (STATIC_DIR / "index.html").read_text("utf-8")
    for asset in ("/static/app.js", "/static/app.css"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={BUILD_VERSION}"')
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})
