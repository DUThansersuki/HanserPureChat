# Persona v2 Text A/B 报告

- Cases：8
- A：当前 production Persona + 当前 Style RAG
- B：`hanser-persona-v2-candidate-20260908` + 混合 Signals + Hard Boundaries + Soft Priors + v2 Style generation
- Responder：`deepseek-v4-flash`；Judge：`deepseek-v4-pro`；双向盲评：是
- 生成成功：16 / 16
- Judge 成功：16 / 16
- 双向一致 case：5；不一致率：0.375
- Consensus：{"inconsistent": 3, "B": 4, "tie": 1}
- A/B hard failures：0 / 0
- 配对 group composite delta（B-A）：{"mean": 0.354167, "ci95": [0.104167, 0.604167], "groups": 8, "method": "paired group bootstrap", "samples": 10000}
- Style 非空覆盖 A/B：8 / 8

结论：**未证实**

理由：sample size or Judge reliability gate not met

限制：模型 Judge 不是人物本人认证；同一 case 的双向评审不当作两个独立样本；来源还原度与 owner preference 分列；多轮自然历史和设置/TTS 尚不属于本次单轮 runner。
