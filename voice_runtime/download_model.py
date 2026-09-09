from __future__ import annotations

from pathlib import Path

from huggingface_hub import snapshot_download


MODEL_ID = "openbmb/VoxCPM2"
MODEL_REVISION = "32279effe8c19989596f05d353d1447f51d9e915"


def main() -> None:
    destination = Path(__file__).resolve().parent / "models" / "VoxCPM2"
    resolved = snapshot_download(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=destination,
    )
    print(f"Downloaded {MODEL_ID}@{MODEL_REVISION} to {resolved}")


if __name__ == "__main__":
    main()
