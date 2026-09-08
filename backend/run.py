from __future__ import annotations

import uvicorn

from hanser_agent.api import create_app
from hanser_agent.config import load_settings


if __name__ == "__main__":
    settings = load_settings()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        reload=False,
    )
