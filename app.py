from __future__ import annotations

import os
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


APP_ORIGIN = os.getenv("APP_ORIGIN", "https://playlist-player-32p.pages.dev").rstrip("/")
WORK_ROOT = Path(os.getenv("DOWNLOAD_TMP_DIR", "/tmp/playlist-player-downloads"))
POT_PROVIDER_HOME = os.getenv("POT_PROVIDER_HOME", "").strip()
MAX_DURATION_SECONDS = 2 * 60 * 60
MAX_FILE_BYTES = 1_500_000_000
JOB_TTL_SECONDS = 30 * 60
MAX_JOBS_PER_IP_PER_HOUR = 3
MAX_INFO_PER_IP_PER_10_MIN = 20
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ALLOWED_QUALITIES = {144, 240, 360, 480, 720, 1080, 1440, 2160}
ALLOWED_AUDIO_FORMATS = {"mp3", "m4a"}

jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
rate_lock = threading.Lock()
job_rate: dict[str, deque[float]] = defaultdict(deque)
info_rate: dict[str, deque[float]] = defaultdict(deque)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yt-dlp")
cleanup_stop = threading.Event()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def require_rate_limit(bucket: dict[str, deque[float]], key: str, limit: int, window: int) -> None:
    now = time.time()
    with rate_lock:
        rows = bucket[key]
        while rows and rows[0] <= now - window:
            rows.popleft()
        if len(rows) >= limit:
            retry_after = max(1, int(rows[0] + window - now))
            raise HTTPException(429, f"요청이 너무 많습니다. {retry_after}초 뒤 다시 시도해 주세요.")
        rows.append(now)


def youtube_url(video_id: str) -> str:
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(400, "올바른 YouTube 영상 ID가 아닙니다.")
    return f"https://www.youtube.com/watch?v={video_id}"


def base_ydl_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "source_address": "0.0.0.0",
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 2,
    }
    if POT_PROVIDER_HOME:
        options["extractor_args"] = {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilscript": {"server_home": [POT_PROVIDER_HOME]},
        }
    return options


def public_error(error: Exception) -> str:
    message = str(error).strip()
    lowered = message.lower()
    if "private video" in lowered:
        return "비공개 영상은 다운로드할 수 없습니다."
    if "sign in" in lowered or "login" in lowered:
        return "로그인이 필요한 영상은 다운로드할 수 없습니다."
    if "copyright" in lowered or "unavailable" in lowered:
        return "현재 다운로드할 수 없는 영상입니다."
    if "larger than max-filesize" in lowered:
        return "파일이 무료 서버의 1.5GB 제한을 넘습니다."
    if "duration" in lowered:
        return "2시간을 넘는 영상은 무료 서버에서 처리할 수 없습니다."
    return "다운로드 처리에 실패했습니다. 잠시 후 다시 시도해 주세요."


def extract_info(video_id: str) -> dict[str, Any]:
    options = {**base_ydl_options(), "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(youtube_url(video_id), download=False)
    if not info:
        raise RuntimeError("video unavailable")
    duration = int(info.get("duration") or 0)
    if duration > MAX_DURATION_SECONDS:
        raise RuntimeError("duration limit")
    heights = sorted(
        {
            int(row["height"])
            for row in info.get("formats") or []
            if row.get("vcodec") not in (None, "none") and isinstance(row.get("height"), (int, float))
        },
        reverse=True,
    )
    return {
        "title": info.get("title") or "YouTube 영상",
        "duration": duration,
        "thumbnail": info.get("thumbnail") or "",
        "qualities": [height for height in heights if height <= 2160],
    }


def update_job(job_id: str, **values: Any) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)


def progress_hook(job_id: str):
    def hook(data: dict[str, Any]) -> None:
        if data.get("status") != "downloading":
            return
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        downloaded = data.get("downloaded_bytes") or 0
        percent = round(downloaded / total * 100, 1) if total else None
        update_job(
            job_id,
            progress=percent,
            downloadedBytes=downloaded,
            totalBytes=total or None,
            speed=data.get("speed"),
            eta=data.get("eta"),
        )

    return hook


def run_download(job_id: str) -> None:
    with jobs_lock:
        job = dict(jobs[job_id])
    job_dir = WORK_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job(job_id, status="downloading", startedAt=time.time())

    try:
        metadata = extract_info(job["videoId"])
        update_job(job_id, title=metadata["title"])
        output_template = str(job_dir / "%(title).120B [%(id)s].%(ext)s")
        options = {
            **base_ydl_options(),
            "outtmpl": output_template,
            "windowsfilenames": True,
            "max_filesize": MAX_FILE_BYTES,
            "progress_hooks": [progress_hook(job_id)],
        }

        if job["mode"] == "audio":
            options.update(
                {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": job["audioFormat"],
                            "preferredquality": "192",
                        }
                    ],
                }
            )
        else:
            quality = job["quality"]
            options.update(
                {
                    "format": f"bv*[height<={quality}]+ba/b[height<={quality}]",
                    "merge_output_format": "mp4",
                }
            )

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([youtube_url(job["videoId"])])

        candidates = [
            path
            for path in job_dir.iterdir()
            if path.is_file() and path.suffix not in {".part", ".ytdl", ".temp"}
        ]
        if not candidates:
            raise RuntimeError("download output missing")
        output = max(candidates, key=lambda path: path.stat().st_size)
        size = output.stat().st_size
        if size > MAX_FILE_BYTES:
            raise RuntimeError("larger than max-filesize")
        update_job(
            job_id,
            status="complete",
            progress=100,
            filename=output.name,
            filePath=str(output),
            fileSize=size,
            completedAt=time.time(),
            expiresAt=time.time() + JOB_TTL_SECONDS,
        )
    except Exception as error:
        shutil.rmtree(job_dir, ignore_errors=True)
        update_job(job_id, status="error", error=public_error(error), completedAt=time.time())


def cleanup_expired() -> None:
    now = time.time()
    expired: list[str] = []
    with jobs_lock:
        for job_id, job in jobs.items():
            if job.get("status") in {"queued", "downloading"}:
                continue
            deadline = job.get("expiresAt") or (job.get("completedAt", job["createdAt"]) + JOB_TTL_SECONDS)
            if deadline <= now:
                expired.append(job_id)
        for job_id in expired:
            jobs.pop(job_id, None)
    for job_id in expired:
        shutil.rmtree(WORK_ROOT / job_id, ignore_errors=True)


def cleanup_loop() -> None:
    while not cleanup_stop.wait(300):
        cleanup_expired()


@asynccontextmanager
async def lifespan(_: FastAPI):
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=cleanup_loop, name="download-cleanup", daemon=True)
    thread.start()
    yield
    cleanup_stop.set()
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Playlist Player Download Service", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[APP_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Playlist-Player"],
)


class DownloadRequest(BaseModel):
    videoId: str = Field(min_length=11, max_length=11)
    mode: str
    quality: int = 720
    audioFormat: str = "mp3"


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "playlist-player-downloader", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/formats")
def formats(videoId: str, request: Request) -> dict[str, Any]:
    require_rate_limit(info_rate, client_ip(request), MAX_INFO_PER_IP_PER_10_MIN, 600)
    try:
        return extract_info(videoId)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(422, public_error(error)) from error


@app.post("/api/jobs", status_code=202)
def create_job(body: DownloadRequest, request: Request) -> dict[str, Any]:
    require_rate_limit(job_rate, client_ip(request), MAX_JOBS_PER_IP_PER_HOUR, 3600)
    youtube_url(body.videoId)
    if body.mode not in {"video", "audio"}:
        raise HTTPException(400, "지원하지 않는 다운로드 방식입니다.")
    if body.quality not in ALLOWED_QUALITIES:
        raise HTTPException(400, "지원하지 않는 화질입니다.")
    if body.audioFormat not in ALLOWED_AUDIO_FORMATS:
        raise HTTPException(400, "지원하지 않는 오디오 형식입니다.")

    cleanup_expired()
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "videoId": body.videoId,
            "mode": body.mode,
            "quality": body.quality,
            "audioFormat": body.audioFormat,
            "status": "queued",
            "progress": 0,
            "createdAt": time.time(),
        }
    executor.submit(run_download, job_id)
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    cleanup_expired()
    with jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job:
        raise HTTPException(404, "다운로드 작업을 찾을 수 없습니다.")
    response = {
        key: job.get(key)
        for key in (
            "id",
            "status",
            "progress",
            "downloadedBytes",
            "totalBytes",
            "speed",
            "eta",
            "title",
            "filename",
            "fileSize",
            "error",
            "expiresAt",
        )
        if job.get(key) is not None
    }
    if job.get("status") == "complete":
        response["downloadUrl"] = f"/api/jobs/{job_id}/file"
    return response


@app.get("/api/jobs/{job_id}/file")
def download_file(job_id: str):
    cleanup_expired()
    with jobs_lock:
        job = dict(jobs.get(job_id) or {})
    path = Path(job.get("filePath") or "")
    if job.get("status") != "complete" or not path.is_file():
        raise HTTPException(404, "다운로드 파일이 없거나 만료됐습니다.")
    return FileResponse(
        path,
        filename=job.get("filename") or path.name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store"},
    )
