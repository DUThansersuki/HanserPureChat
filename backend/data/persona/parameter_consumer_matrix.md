# Persona v2 参数消费者表

| 参数 | 类型 | 实际消费者 | 可观察变化 | 不得突破的边界 |
|---|---|---|---|---|
| `humor_initiative` | soft weight | `policy._parameter_factor`、零值 cap | `light_contextual_tease` 权重；零值禁 humor | 明确拒绝、当前停止和不适用场景优先 |
| `teasing_intensity` | hard cap | `policy.build_guidance` | `teasing` 强度上限和轻吐槽权重 | 不允许人身攻击；拒绝后为硬禁 |
| `meme_affinity` | soft weight | `policy._parameter_factor` | `playful_reframe` 权重 | 不把梗当事实，不要求每轮用梗 |
| `profanity_level` | hard cap | `policy.build_guidance` | `profanity` 强度上限；零值硬禁 | 用户禁用与安全边界优先 |
| `profanity_target_rate` | owner target / soft feedback | `policy.build_guidance`、`observe_recent_expressions` | 正常合格聊天低于目标时开放 `light_profanity_release`，高于目标附近时降权 | 当前目标 15%；不逐轮抽签、不强制输出；老子/老娘自我戏谑计入粗口，用户拒绝与不适用语境优先 |
| `innuendo_level` | hard cap | `policy.build_guidance` | `innuendo` 强度上限；零值硬禁 | 必须同时满足规则级成人、适用 playful frame 和明确许可 |
| `cutesy_bias` | soft weight / zero cap | `policy.build_guidance` | 低幼态默认降权；零值硬禁 cutesy | 温暖不等于卖萌 |
| `warmth` | soft weight | `policy._parameter_factor` | distress/listen 行为卡权重 | 不自动建议、不强制抱抱 |
| `candor` | soft weight | `policy._parameter_factor` | `reasoned_independent_response` 权重 | 不用随机反对制造独立性 |
| `reply_length` | presentation preference | `ContextBuilder` | 在 Planner 任务长度允许时偏短或偏详 | 必需事实、原文和 exact output 不截断 |
| `address_bias` | soft weight | `policy` soft preference | 降低称呼填充倾向 | 只能用用户提供的称呼，不得发明 |
| `display_punctuation` | presentation | `DisplayAdapter` | 只改变 final text 的显示标点 | semantic text 与硬格式先验证 |
| `repetition_penalty` | soft weight | `policy` observation、fixed/runtime Style 排序 | 近期表达和样例有限降权 | 不变成硬禁止 |
| `stacking_aversion` | soft weight | `policy` soft preference | 降低粗口、梗、双关、卖萌堆叠 | 单个合适表达仍可用 |
| `observation_turns` | observation | `ChatAgentService` | 近期成功 assistant turn 的观察窗口 | 只读真实持久化成功回复 |
| `recency_decay` | observation | `observe_recent_expressions` | 越旧表达影响越弱 | 不推导许可 |
| `persona_settings.adult_innuendo_opt_in` | request hard gate | `ChatAgentService`、`build_turn_signals`、`infer_expression_permissions` | 开启时把本轮年龄状态标为明确成年人，并允许低强度 innuendo 进入后续语境判断 | 默认关闭；只解除年龄与许可门，不绕过当前未成年/拒绝、停止、非 playful、distress、tension 与强度上限 |

参数值与 `product_overrides.yaml` 的来源会进入 Effective settings trace；preview 使用 `candidate + validated` override，production 只使用 `released` override。
