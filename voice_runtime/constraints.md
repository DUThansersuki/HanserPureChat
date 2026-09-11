# Voice runtime dependency boundary

- The Hanser Voice runtime supports only the project-local GPT-SoVITS v2Pro sidecar.
- Control runtime: Python 3.12 in `voice_runtime/.venv`.
- Model runtime: bundled Python environment in `voice_runtime/models/GPT-SoVITS-v2Pro/runtime`.
- Sidecar endpoint is restricted to loopback HTTP and uses the public `/tts` API.
- The frozen sidecar configuration is `voice_runtime/config/gpt_sovits_v2pro.sidecar.candidate.yml`.
- Voice identity and prompt recordings live under `assets/voices/hanser` and are selected through the reviewed manifest.
- Runtime imports never install or download model weights.
