# Persona v2 Text A/B 报告

- Cases：24
- A：当前 production Persona + 当前 Style RAG
- B：`hanser-persona-v2-candidate-20260908` + 混合 Signals + Hard Boundaries + Soft Priors + v2 Style generation
- Responder：`deepseek-v4-flash`；Judge：`deepseek-v4-pro`；双向盲评：是
- 生成成功：48 / 48
- Judge 成功：48 / 48
- 双向一致 case：15；不一致率：0.375
- Consensus：{"A": 4, "inconsistent": 9, "tie": 2, "B": 9}
- A/B hard failures：0 / 0
- 配对 group composite delta（B-A）：{"mean": 0.211806, "ci95": [-0.034722, 0.444444], "groups": 24, "method": "paired group bootstrap", "samples": 10000}
- Style 非空覆盖 A/B：24 / 24

结论：**未证实**

理由：sample size or Judge reliability gate not met

限制：模型 Judge 不是人物本人认证；同一 case 的双向评审不当作两个独立样本；来源还原度与 owner preference 分列；多轮自然历史和设置/TTS 尚不属于本次单轮 runner。
