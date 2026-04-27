from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
app.mount("/map", StaticFiles(directory=settings.map_dir), name="map")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(Path(settings.static_dir) / "gis-studio" / "index.html")


@app.get("/calibrate")
async def calibrate():
    return RedirectResponse(url="/", status_code=307)


@app.get("/builder")
async def builder():
    return RedirectResponse(url="/", status_code=307)


@app.get("/gis")
async def gis():
    return FileResponse(Path(settings.static_dir) / "gis-studio" / "index.html")


@app.get("/login")
async def login():
    return FileResponse(Path(settings.static_dir) / "login" / "index.html")


@app.get("/admin")
async def admin():
    return FileResponse(Path(settings.static_dir) / "admin" / "index.html")
