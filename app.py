"""Local entry point: ``python app.py`` runs the FastAPI server on port 5000."""

from __future__ import annotations

import os

import uvicorn

from app.config import settings


def main() -> None:
    port = int(os.getenv("PORT", settings.PORT))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=not settings.is_production,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
