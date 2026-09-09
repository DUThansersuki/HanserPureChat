# Voice runtime dependency boundary

- Python: `>=3.10,<3.13`; the project bootstrap uses 3.12.
- VoxCPM package source: OpenBMB/VoxCPM commit `f772e498a45fbb5fb8e13fbf9b9c48be9fe33e69`.
- PyTorch/Torchaudio: `2.11.0+cu130` from the official CUDA 13.0 wheel index.
- Model repository: `openbmb/VoxCPM2`, revision `32279effe8c19989596f05d353d1447f51d9e915`.
- Runtime imports never install, download, or load model weights.
- Model loading requires both an enabled, validated profile and the explicit
  `HANSER_VOICE_LOAD_MODEL=true` startup setting.
- The adapter uses the public `VoxCPM.from_pretrained` and `generate` API only;
  project-managed prompt encoding caches are intentionally disabled.
