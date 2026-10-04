"""Serving the single-page app (``STATIC_DIR``) with an ``index.html`` fallback."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse


IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"
DEFAULT_CACHE = "public, max-age=3600"
NO_CACHE_FILES = frozenset(
    {"index.html", "sw.js", "registerSW.js", "manifest.webmanifest", "offline.html"}
)

MISSING_BUILD_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenManus</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto;
padding: 0 1rem; line-height: 1.5">
<h1>OpenManus API is running</h1>
<p>The web interface has not been built. Run <code>npm ci &amp;&amp; npm run build</code>
in <code>web/</code> (or set <code>OPENMANUS_STATIC_DIR</code>) and reload this page.</p>
<p>Health check: <a href="/api/health">/api/health</a></p>
</body></html>
"""

mimetypes.add_type("application/manifest+json", ".webmanifest")


def _cache_control(relative: str) -> str:
    if relative.startswith("assets/"):
        return IMMUTABLE
    name = PurePosixPath(relative).name
    if name in NO_CACHE_FILES or name.endswith(".html"):
        return NO_CACHE
    return DEFAULT_CACHE


def _find(static_dir: Path, relative: str) -> Optional[Path]:
    """The file for ``relative`` inside ``static_dir`` (never outside it)."""
    if not static_dir.is_dir():
        return None
    root = static_dir.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def register_static(app: FastAPI, static_dir: Path) -> None:
    """Add the catch-all GET route; it must be registered after the API routes."""

    @app.api_route(
        "/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False
    )
    async def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        relative = full_path.strip("/")
        if relative:
            found = await asyncio.to_thread(_find, static_dir, relative)
            if found is not None:
                return FileResponse(
                    found, headers={"Cache-Control": _cache_control(relative)}
                )
            if PurePosixPath(relative).suffix:
                raise HTTPException(status_code=404, detail="Not Found")
        index = await asyncio.to_thread(_find, static_dir, "index.html")
        if index is None:
            return HTMLResponse(MISSING_BUILD_PAGE, headers={"Cache-Control": NO_CACHE})
        return FileResponse(index, headers={"Cache-Control": NO_CACHE})
