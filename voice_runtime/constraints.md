# Voice runtime constraints

- The active Hanser Voice backend is only `voxcpm2_hybrid`.
- The model is project-local at `voice_runtime/models/VoxCPM2`; runtime downloads are disabled.
- The main VoxCPM2 model stays on CUDA while AudioVAE encode/decode run on CPU to fit the 6 GB GPU target.
- The frozen package revision is `voxcpm-f772e498a45f`; the adapter revision is `voxcpm2_hybrid_vae_cpu_1`.
- Reference continuation uses the approved `clip_000180.wav` asset and its exact reviewed transcript.
- Output is 48 kHz mono. The generated segment timeline is the shared clock for Voice playback and MMD/L2D mouth/expression evaluation.
- The runtime remains a single-worker bounded queue. Recursive generated prompts, runtime model switching, denoising, normalization, bad-case retry, and optimizer rewriting are disabled in the active profile.
- GPT-SoVITS endpoints, sidecars, adapters, and launch steps are not part of this project configuration.
