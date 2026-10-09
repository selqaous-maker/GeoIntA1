from __future__ import annotations

import json
import os
import struct
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
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(meta_py.parent)
        if not existing_pythonpath
        else os.pathsep.join([str(meta_py.parent), existing_pythonpath])
    )
    proc = subprocess.run(
        [sys.executable, str(meta_py), str(image_path), "--out-dir", str(out_dir)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if proc.returncode != 0:
        if "No module named 'PIL'" in proc.stderr:
            return _stdlib_metadata(image_path, proc.stderr)
        raise HTTPException(status_code=500, detail={"error": "GeoINT metadata failed", "stderr": proc.stderr[-2000:]})

    meta_json = out_dir / "meta.json"
    meta_md = out_dir / "meta.md"
    return {
        "meta": json.loads(meta_json.read_text(encoding="utf-8")) if meta_json.exists() else {},
        "markdown": meta_md.read_text(encoding="utf-8") if meta_md.exists() else proc.stdout,
    }


def _image_size(path: Path) -> tuple[int | None, int | None, str]:
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height, "PNG"
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            while i < len(data) and data[i] == 0xFF:
                i += 1
            marker = data[i] if i < len(data) else None
            i += 1
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height, width = struct.unpack(">HH", data[i + 3 : i + 7])
                return width, height, "JPEG"
            if i + 2 > len(data):
                break
            seg_len = struct.unpack(">H", data[i : i + 2])[0]
            if seg_len < 2:
                break
            i += seg_len
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return None, None, "WEBP"
    return None, None, "image"


def _stdlib_metadata(path: Path, stderr: str) -> dict:
    width, height, fmt = _image_size(path)
    filename = path.name
    lines = [
        f"# Metadata: {filename}",
        "",
        "GeoINT web fallback ran without Pillow on this host.",
        "",
        "- No GPS found by the lightweight fallback.",
        "- No capture time found by the lightweight fallback.",
        f"- **Image**: {fmt}" + (f" [{width}, {height}]" if width and height else ""),
        "- Deep EXIF parsing needs Pillow/exiftool support on the deployment host.",
    ]
    if "WhatsApp" in filename:
        lines.insert(4, "- **Filename**: WhatsApp export")
    return {
        "meta": {
            "fallback": True,
            "format": fmt,
            "width": width,
            "height": height,
            "filename": filename,
            "stderr_tail": stderr[-500:],
        },
        "markdown": "\n".join(lines),
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
