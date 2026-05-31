"""
仲良しTube+ FastAPI バックエンド
最速で YouTube 動画のストリーム URL を取得する API。

複数の Invidious / Piped インスタンスへ並列リクエストを投げ、
最初に成功したレスポンスを返す（race 戦略）ことで遅延を最小化。

デプロイ対応:
  - Render        : `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
  - Vercel        : `api/index.py` がこのモジュールを再エクスポート
  - CodeSandbox   : 同上 (uvicorn)
  - GitHub Pages  : 静的 `static/index.html` のみ配信 (API は別ホスト)
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.privacyredirect.com",
    "https://yewtu.be",
    "https://invidious.f5.si",
    "https://iv.melmac.space",
    "https://yt.omada.cafe",
]

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi-libre.kavin.rocks",
    "https://pipedapi.adminforge.de",
]

TIMEOUT = httpx.Timeout(6.0, connect=3.0)
HEADERS = {"User-Agent": "NakayoshiTubePlus/1.0 (+https://github.com)"}

app = FastAPI(title="NakayoshiTube+ API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _fetch_invidious(client: httpx.AsyncClient, base: str, vid: str) -> dict[str, Any]:
    r = await client.get(f"{base}/api/v1/videos/{vid}", headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    streams = []
    for f in data.get("formatStreams", []) or []:
        streams.append({"url": f.get("url"), "quality": f.get("qualityLabel") or f.get("quality"), "type": f.get("type"), "container": f.get("container")})
    for f in data.get("adaptiveFormats", []) or []:
        streams.append({"url": f.get("url"), "quality": f.get("qualityLabel") or f.get("quality"), "type": f.get("type"), "container": f.get("container"), "bitrate": f.get("bitrate")})
    return {
        "source": f"invidious:{base}",
        "id": vid,
        "title": data.get("title"),
        "author": data.get("author"),
        "duration": data.get("lengthSeconds"),
        "thumbnail": (data.get("videoThumbnails") or [{}])[0].get("url"),
        "hls": data.get("hlsUrl"),
        "dash": data.get("dashUrl"),
        "streams": [s for s in streams if s["url"]],
    }


async def _fetch_piped(client: httpx.AsyncClient, base: str, vid: str) -> dict[str, Any]:
    r = await client.get(f"{base}/streams/{vid}", headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    streams = []
    for f in (data.get("videoStreams") or []) + (data.get("audioStreams") or []):
        streams.append({"url": f.get("url"), "quality": f.get("quality"), "type": f.get("mimeType"), "container": f.get("format"), "bitrate": f.get("bitrate")})
    return {
        "source": f"piped:{base}",
        "id": vid,
        "title": data.get("title"),
        "author": data.get("uploader"),
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnailUrl"),
        "hls": data.get("hls"),
        "dash": data.get("dash"),
        "streams": [s for s in streams if s["url"]],
    }


async def _race(coros: list) -> dict[str, Any]:
    """最初に成功したタスクの結果を返す。全部失敗なら最後の例外を上げる。"""
    tasks = [asyncio.create_task(c) for c in coros]
    last_exc: Exception | None = None
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                try:
                    res = t.result()
                    for p in pending:
                        p.cancel()
                    return res
                except Exception as e:  # noqa: BLE001
                    last_exc = e
        raise last_exc or RuntimeError("all upstreams failed")
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


@app.get("/")
async def root() -> HTMLResponse:
    return HTMLResponse(
        "<h1>NakayoshiTube+ API</h1>"
        "<p>Try <code>/api/video?id=VIDEO_ID</code> or <code>/api/search?q=keyword</code>.</p>"
        "<p><a href='/docs'>Swagger UI</a></p>"
    )


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/video")
async def video(id: str = Query(..., min_length=5, max_length=20)) -> JSONResponse:
    """指定 ID の動画ストリーム URL を最速で返す。"""
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, http2=False) as client:
        coros = [_fetch_invidious(client, b, id) for b in INVIDIOUS_INSTANCES]
        coros += [_fetch_piped(client, b, id) for b in PIPED_INSTANCES]
        try:
            data = await _race(coros)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"all upstreams failed: {e}")
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=60"})


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1, max_length=200)) -> JSONResponse:
    async def _inv(client, base):
        r = await client.get(f"{base}/api/v1/search", params={"q": q, "type": "video"}, headers=HEADERS)
        r.raise_for_status()
        items = r.json()
        return {"source": f"invidious:{base}", "items": [
            {"id": i.get("videoId"), "title": i.get("title"), "author": i.get("author"),
             "duration": i.get("lengthSeconds"), "thumbnail": (i.get("videoThumbnails") or [{}])[0].get("url")}
            for i in items if i.get("type") == "video"
        ]}

    async def _pip(client, base):
        r = await client.get(f"{base}/search", params={"q": q, "filter": "videos"}, headers=HEADERS)
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
        return {"source": f"piped:{base}", "items": [
            {"id": (i.get("url") or "").split("v=")[-1], "title": i.get("title"),
             "author": i.get("uploaderName"), "duration": i.get("duration"), "thumbnail": i.get("thumbnail")}
            for i in items
        ]}

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        coros = [_inv(client, b) for b in INVIDIOUS_INSTANCES] + [_pip(client, b) for b in PIPED_INSTANCES]
        try:
            data = await _race(coros)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"search failed: {e}")
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=120"})


@app.get("/api/proxy")
async def proxy(url: str) -> RedirectResponse:
    """簡易リダイレクト型プロキシ (CORS 対策。重い帯域消費を避けるためストリームは中継しない)。"""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid url")
    return RedirectResponse(url, status_code=307)
