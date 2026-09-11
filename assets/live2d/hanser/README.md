# Hanser visual rig assets

The first visual candidate uses the distributed Hanser MMD model instead of a
PSD/Cubism model. Binary model assets stay outside Git and are copied into
`.runtime/mmd/hanser_v2.0_cloth2_test` by
`scripts/prepare_mmd_candidate.ps1`.

Validated candidate facts:

- PMX 2.0, 61,950 vertices, 102,648 triangles, 37 materials.
- 1,014 bones, 78 vertex morphs, 847 rigid bodies, 1,518 joints.
- The missing `new/cloth1_BaseColor.png` belongs to material `安全裤`; the
  current test candidate substitutes `tex/cloth2.png` without modifying the
  distributed source.
- Browser rendering normalizes PMX backslashes and `TEX/tex` casing.
- Real-time acceptance starts with physics disabled. Japanese bone and morph
  names are identifiers only; Chinese speech remains on the Chinese TTS path.

The user confirmed that the original author distributes the model for use on a
public platform. Preserve creator/source attribution when promoting the asset
from candidate to a release package.
