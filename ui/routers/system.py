"""Low-risk system endpoints shared by the source and packaged applications."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter


def create_system_router(
    base_dir: Path,
    get_kb_status: Callable[[], dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    async def health() -> dict[str, Any]:
        kb_status = get_kb_status()
        return {
            "status": "ok",
            "version": "3.0.0",
            "kb_state": kb_status.get("state", "loading"),
            "kb_error": kb_status.get("error"),
        }

    @router.get("/api/dev/version")
    async def dev_version() -> dict[str, float | str]:
        latest = 0.0
        for subdirectory in ("templates", "static"):
            root = base_dir / subdirectory
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    latest = max(latest, path.stat().st_mtime)
                except OSError:
                    pass
        return {"version": f"{latest:.3f}", "ts": latest}

    return router
