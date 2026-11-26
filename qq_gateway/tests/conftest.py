from pathlib import Path

import pytest

from hanser_qq.config import Settings


@pytest.fixture
def config(tmp_path):
    return Settings.model_validate({
        "gateway": {"onebot_access_token": "onebot-test", "admin_token": "admin-test",
                    "groups": ["300"], "debounce_seconds": 0.02, "max_wait_seconds": 0.06},
        "backend": {"token": "backend-test",
                    "package_path": Path(__file__).resolve().parents[1] / ".runtime/backend",
                    "config_path": tmp_path / "hanser.yml"},
        "storage_path": tmp_path / "gateway.db",
    })
