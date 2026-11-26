"""Prepare the local Ollama models used by the chat process."""
from urllib.parse import urlsplit

import httpx


OLLAMA_URL = "http://127.0.0.1:11434/v1"
OLLAMA_MODEL = "qwen3.5:4b"


def ollama_origin(endpoint: str) -> str | None:
    url = urlsplit(endpoint)
    if (
        url.scheme == "http"
        and url.netloc in {"127.0.0.1:11434", "localhost:11434", "[::1]:11434"}
        and url.path.rstrip("/") == "/v1"
        and not url.query and not url.fragment
    ):
        return f"{url.scheme}://{url.netloc}"
    return None


def ensure_ollama_models(*profiles) -> None:
    local_models = {(ollama_origin(profile.endpoint), profile.model) for profile in profiles if profile}
    with httpx.Client(trust_env=False, timeout=5) as client:
        for origin, model in local_models:
            if origin is None:
                continue
            try:
                status = client.post(f"{origin}/api/show", json={"model": model})
            except httpx.ConnectError as exc:
                raise RuntimeError("Start the local Ollama service and retry.") from exc
            if status.status_code == 404:
                print(f"Downloading {model} through Ollama", flush=True)
                response = client.post(
                    f"{origin}/api/pull", json={"model": model, "stream": False}, timeout=3600
                )
                response.raise_for_status()
                result = response.json()
                if result.get("status") != "success":
                    raise RuntimeError(f"Ollama could not download {model}: {result}")
            else:
                status.raise_for_status()
