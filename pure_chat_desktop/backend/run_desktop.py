from __future__ import annotations

import os
from dataclasses import replace

import uvicorn

from hanser_agent.config import load_settings
from hanser_agent.desktop_api import create_desktop_app


if __name__ == "__main__":
    settings = load_settings()
    settings.performance = replace(
        settings.performance,
        structured_performance_enabled=False,
        speech_runtime_enabled=False,
        dynamic_live2d_enabled=False,
        offline_export_enabled=False,
    )
    settings.host = "127.0.0.1"
    settings.port = int(os.environ.get("HANSER_BACKEND_PORT", "18765"))
    uvicorn.run(
        create_desktop_app(settings=settings),
        host=settings.host,
        port=settings.port,
        reload=False,
    )

