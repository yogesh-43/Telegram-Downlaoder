"""Local web app for bulk Telegram media downloads via Telethon."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import TelegramEngine

WEB = Path(__file__).resolve().parent / "web"
engine = TelegramEngine()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await engine.connect_existing()
    except Exception:
        pass
    yield
    await engine.close()


app = FastAPI(title="TG Saver", lifespan=lifespan)


class SetupBody(BaseModel):
    api_id: int
    api_hash: str


class PhoneBody(BaseModel):
    phone: str


class CodeBody(BaseModel):
    code: str


class PasswordBody(BaseModel):
    password: str


class ScanBody(BaseModel):
    chat: str
    kinds: list[str] = Field(default_factory=list)
    limit: int | None = None


class DownloadBody(BaseModel):
    chat: str
    message_ids: list[int]


class SettingsBody(BaseModel):
    download_dir: str | None = None
    concurrency: int | None = None
    prefix_date: bool | None = None
    scan_limit: int | None = None


class CancelBody(BaseModel):
    job_id: str


@app.get("/api/status")
async def status():
    return await engine.status()


@app.post("/api/setup")
async def setup(body: SetupBody):
    await engine.replace_api(body.api_id, body.api_hash.strip())
    return {"ok": True}


@app.post("/api/login/send")
async def login_send(body: PhoneBody):
    try:
        step = await engine.send_code(body.phone)
        return {"step": step}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/login/code")
async def login_code(body: CodeBody):
    try:
        step = await engine.verify_code(body.code)
        return {"step": step, "user": engine.me}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/login/2fa")
async def login_2fa(body: PasswordBody):
    try:
        step = await engine.verify_password(body.password)
        return {"step": step, "user": engine.me}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/logout")
async def logout():
    await engine.logout()
    return {"ok": True}


@app.get("/api/chats")
async def chats(q: str = ""):
    try:
        return {"chats": await engine.list_chats(q)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/scan")
async def scan(body: ScanBody):
    limit = body.limit or int(engine.config.get("scan_limit") or 300)
    limit = max(1, min(limit, 2000))
    try:
        return await engine.scan(body.chat, body.kinds, limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/download")
async def download(body: DownloadBody):
    try:
        job_ids, skipped = await engine.enqueue(body.chat, body.message_ids)
        return {"job_ids": job_ids, "skipped": skipped}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/queue")
async def queue():
    return engine.queue_view()


@app.post("/api/local/open")
async def open_local_folder():
    return {"ok": True, "folder": engine.open_download_folder()}


@app.post("/api/queue/cancel")
async def cancel(body: CancelBody):
    await engine.cancel(body.job_id)
    return {"ok": True}


@app.post("/api/settings")
async def settings(body: SettingsBody):
    if body.concurrency is not None:
        body.concurrency = max(1, min(8, body.concurrency))
    if body.scan_limit is not None:
        body.scan_limit = max(20, min(2000, body.scan_limit))
    engine.save_settings(
        download_dir=body.download_dir,
        concurrency=body.concurrency,
        prefix_date=body.prefix_date,
        scan_limit=body.scan_limit,
    )
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=7860, reload=False)
