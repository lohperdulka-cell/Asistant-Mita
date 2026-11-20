"""Отдельный запуск Mini App: python -m webapp.run"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from config import settings
from database import Database
from webapp.app import create_app


async def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
    settings.ensure_dirs()
    db = Database(settings.resolved_db_path)
    await db.init()
    app = create_app(db)
    config = uvicorn.Config(
        app,
        host=settings.webapp_host,
        port=settings.webapp_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
