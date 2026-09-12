from __future__ import annotations

import os
from dataclasses import replace

import uvicorn

from hanser_agent.api import create_app
from hanser_agent.config import load_settings


if __name__ == "__main__":
    settings = load_settings()
    settings.performance = replace(
        settings.performance,
        structured_performance_enabled=False,
        speech_runtime_enabled=False,
        dynamic_live2d_enabled=False,
        offline_export_enabled=False,
    )
    settings.port = int(os.environ.get("HANSER_CHAT_ONLY_BACKEND_PORT", "8766"))
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        reload=False,
    )
