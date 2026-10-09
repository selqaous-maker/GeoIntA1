from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web_static"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

app = FastAPI(title="GeoINT Web", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "image.jpg").suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"} else ".jpg"


def _run_meta(image_path: Path, out_dir: Path) -> dict:
    meta_py = ROOT / "meta.py"
    if not meta_py.exists():
        meta_py = ROOT / "scripts" / "meta.py"
    if not meta_py.exists():
        raise HTTPException(status_code=500, detail="GeoINT meta.py not found")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(meta_py.parent)
    proc = subprocess.run(
        [sys.executable, str(meta_py), str(image_path), "--out-dir", str(out_dir)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail={"error": "GeoINT metadata failed", "stderr": proc.stderr[-2000:]})

    meta_json = out_dir / "meta.json"
    meta_md = out_dir / "meta.md"
    return {
        "meta": json.loads(meta_json.read_text(encoding="utf-8")) if meta_json.exists() else {},
        "markdown": meta_md.read_text(encoding="utf-8") if meta_md.exists() else proc.stdout,
    }


def _basic_guess(meta: dict) -> dict:
    gps = meta.get("gps") or meta.get("GPS") or {}
    if isinstance(gps, dict) and gps.get("latitude") and gps.get("longitude"):
        return {
            "level": "gps",
            "country": "GPS coordinates available",
            "place": f"{gps.get('latitude')}, {gps.get('longitude')}",
            "confidence": "high",
            "note": "The file contains coordinates. Verify against the scene before relying on them.",
        }
    return {
        "level": "metadata-only",
        "country": "Unknown from metadata",
        "place": "No GPS in the file. Use visual investigation for country/place.",
        "confidence": "low",
        "note": "This web MVP runs the lightweight metadata pass. Deep visual geolocation still needs the agent workflow.",
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "meta_py": (ROOT / "meta.py").exists() or (ROOT / "scripts" / "meta.py").exists()}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)) -> JSONResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Max 12 MB.")

    run_id = uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix=f"geoint-{run_id}-") as tmp:
        tmp_dir = Path(tmp)
        image_path = tmp_dir / f"upload{_safe_suffix(file.filename)}"
        image_path.write_bytes(raw)
        out_dir = tmp_dir / "out"
        out_dir.mkdir()
        result = _run_meta(image_path, out_dir)
        return JSONResponse(
            {
                "id": run_id,
                "filename": file.filename,
                "bytes": len(raw),
                "guess": _basic_guess(result["meta"]),
                "markdown": result["markdown"],
                "meta": result["meta"],
            }
        )

