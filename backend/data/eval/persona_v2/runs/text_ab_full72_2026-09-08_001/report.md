# Persona v2 Text A/B 报告

- Cases：72
- A：当前 production Persona + 当前 Style RAG
- B：`hanser-persona-v2-candidate-20260908` + 混合 Signals + Hard Boundaries + Soft Priors + v2 Style generation
- Responder：`deepseek-v4-flash`；Judge：`deepseek-v4-flash`；双向盲评：是
- 生成成功：144 / 144
- Judge 成功：144 / 144
- 双向一致 case：54；不一致率：0.250
- Consensus：{"B": 43, "inconsistent": 18, "tie": 8, "A": 3}
- A/B hard failures：3 / 0
- 配对 group composite delta（B-A）：{"mean": 0.634409, "ci95": [0.505376, 0.760081], "groups": 62, "method": "paired group bootstrap", "samples": 10000}
- Style 非空覆盖 A/B：69 / 69

结论：**正向候选**

理由：three preregistered goals improved without material naturalness/warmth regression

限制：模型 Judge 不是人物本人认证；同一 case 的双向评审不当作两个独立样本；来源还原度与 owner preference 分列；多轮自然历史和设置/TTS 尚不属于本次单轮 runner。
