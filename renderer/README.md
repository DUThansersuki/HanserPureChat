# Offline performance renderer

`render.mjs` turns the frozen sample clock into a deterministic frame plan. A
browser capture driver must render each numbered PNG by calling the shared
Live2D evaluator with the listed sample position. FFmpeg then combines those
already-rendered frames with an accepted full audio artifact.

Plan-only validation does not need a model or media asset:

```powershell
node renderer/render.mjs --manifest renderer/fixtures/package.schema1.1.example.json --plan-only
```

Silent export uses a `VisualTurnPlan` presentation clock and must not pass an
empty WAV to this audio-clock command.
