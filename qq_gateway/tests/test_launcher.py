import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

from test_onebot_integration import free_port


def test_combined_startup_and_owned_process_shutdown(config, tmp_path):
    config.gateway.port = free_port()
    config.backend.port = free_port()
    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((root / "hanser.example.yml").read_text(encoding="utf-8"))
    raw["llm"] |= {"model": "local-test", "api_key": "not-a-live-key", "base_url": "http://model.invalid/v1"}
    raw["data"] = {"db_path": str(tmp_path / "hanser.db"), "userdict_path": str(config.backend.package_path / "userdict.txt")}
    config.backend.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    shutil.copy2(config.backend.package_path / "documents.seed.db", tmp_path / "hanser.db")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    log_path = tmp_path / "launcher.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "hanser_qq", "run", "--config", str(path)],
            cwd=root, env=dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1"), stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                for _ in range(150):
                    assert process.poll() is None, log_path.read_text(encoding="utf-8")
                    try:
                        response = client.get(f"http://127.0.0.1:{config.gateway.port}/health",
                                              headers={"X-Hanser-QQ-Admin-Token": config.gateway.admin_token})
                        if response.status_code == 200:
                            assert response.json()["backend"]["chat_ready"]
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.05)
                else:
                    raise AssertionError("combined launcher did not become ready")
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                process.wait(timeout=15)
        assert process.returncode == 0, log_path.read_text(encoding="utf-8")
        for port in (config.gateway.port, config.backend.port):
            with httpx.Client(trust_env=False, timeout=1) as client:
                try:
                    client.get(f"http://127.0.0.1:{port}/health")
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    continue
                raise AssertionError(f"owned service at {port} survived launcher shutdown")
