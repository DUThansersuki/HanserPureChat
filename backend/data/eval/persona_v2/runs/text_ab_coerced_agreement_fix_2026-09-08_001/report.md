# Persona v2 Text A/B 报告

- Cases：3
- A：当前 production Persona + 当前 Style RAG
- B：`hanser-persona-v2-candidate-20260908` + 混合 Signals + Hard Boundaries + Soft Priors + v2 Style generation
- Responder：`deepseek-v4-flash`；Judge：`deepseek-v4-flash`；双向盲评：是
- 生成成功：6 / 6
- Judge 成功：6 / 6
- 双向一致 case：3；不一致率：0.000
- Consensus：{"B": 3}
- A/B hard failures：0 / 0
- 配对 group composite delta（B-A）：{"mean": 1.25, "ci95": [0.666667, 2.333333], "groups": 3, "method": "paired group bootstrap", "samples": 10000}
- Style 非空覆盖 A/B：3 / 3

结论：**未证实**

理由：one or more preregistered target improvements were not demonstrated

限制：模型 Judge 不是人物本人认证；同一 case 的双向评审不当作两个独立样本；来源还原度与 owner preference 分列；多轮自然历史和设置/TTS 尚不属于本次单轮 runner。
