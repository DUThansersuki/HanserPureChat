# Persona v2 Text A/B 报告

- Cases：24
- A：同候选包/同候选语料，但不注入逐轮信号与行为决策
- B：`hanser-persona-v2-candidate-20260908` + 混合 Signals + Hard Boundaries + Soft Priors + v2 Style generation
- Responder：`deepseek-v4-flash`；Judge：`deepseek-v4-flash`；双向盲评：是
- 生成成功：48 / 48
- Judge 成功：48 / 48
- 双向一致 case：19；不一致率：0.208
- Consensus：{"tie": 7, "inconsistent": 5, "A": 3, "B": 9}
- A/B hard failures：0 / 0
- 配对 group composite delta（B-A）：{"mean": 0.159722, "ci95": [-0.121528, 0.409722], "groups": 24, "method": "paired group bootstrap", "samples": 10000}
- Style 非空覆盖 A/B：24 / 24

结论：**未证实**

理由：one or more preregistered target improvements were not demonstrated

限制：模型 Judge 不是人物本人认证；同一 case 的双向评审不当作两个独立样本；来源还原度与 owner preference 分列；多轮自然历史和设置/TTS 尚不属于本次单轮 runner。
