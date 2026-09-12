# Hanser Chatbot 前端页面美化方案

> 基于当前页面截图进行的视觉与交互优化方案。  
> 核心目标：从“本地 Chatbot + Live2D”进一步转化为“Hanser 本身就是整个界面的中心，Chatbot 功能退到背景里”的私人陪伴空间。

---

## 1. 当前页面的核心问题

当前页面已经具备以下优点：

- 整体深色基调比较统一。
- Live2D 已经直接融入主页面，而不是单独放在预览卡片中。
- 页面结构简洁，没有明显的信息过载。
- Chat、状态栏、输入区、Live2D 等核心功能已经齐全。

但目前仍然存在明显的“通用 AI Chatbot + Live2D”感，主要问题如下。

### 1.1 页面中部存在较大的无意义空区

当前视觉关系大致是：

```text
左侧 UI                         右侧人物

标题



建议按钮


输入框                         Hanser
```

标题、建议问题、输入框彼此之间间距过大，而 Live2D 又与左侧聊天区域距离较远。

这导致：

- 页面视觉不够聚拢。
- Hanser 与聊天区域之间没有形成强关系。
- 中间黑色区域成为无意义留白。
- 页面更像“左右两个模块拼接”，而不是一个完整场景。

---

### 1.2 首页结构仍然非常像标准 AI Chatbot

当前首页采用：

```text
大标题
+
说明文字
+
2×2 推荐问题
+
大输入框
```

这一结构非常接近主流 AI 助手的空状态页面。

尤其是：

> 要来聊一会儿吗？

本质上仍是一个标准 Welcome Hero。

对于当前项目，更合适的体验应该是：

> Hanser 主动和用户说第一句话。

因此首页应该从“产品欢迎页”改成“角色 Greeting”。

---

### 1.3 Live2D 目前更像右侧独立人物展示

当前 Live2D 本身效果不错，但与整体页面的空间关系还不够紧密。

主要问题：

- 人物距离聊天内容较远。
- 人物脚下缺少视觉落点。
- 人物下半身被 viewport 直接截断。
- 背景星点较随机。
- Chat 与 Live2D 没有共享明显的视觉轴线。

最终目标不是：

```text
Chat UI | Live2D
```

而应该更接近：

```text
Hanser 就在这个聊天空间里。
```

---

### 1.4 2×2 推荐问题卡片 AI 感较强

当前建议问题内容本身没有问题，但视觉表现属于典型 Suggested Prompts：

```text
┌─────────────┐ ┌─────────────┐
│             │ │             │
└─────────────┘ └─────────────┘
```

容易让人联想到 ChatGPT / Claude / Gemini 等产品。

建议改成：

```text
✦ 今天发生什么了吗？

  最近有没有一直惦记的事？

  还记得我们上次聊到哪了吗？

  想听首歌吗？
```

改为纵向轻列表，而不是 Card Grid。

---

### 1.5 输入框仍然偏 ChatGPT Composer

当前输入区域：

- 大圆角。
- 大面积矩形容器。
- 左下角存在 `Hanser Agent`。
- 右下角发送按钮。
- 占据较宽页面宽度。

这些特征依然比较标准化。

建议：

- 删除重复的 `Hanser Agent`。
- 缩小最大宽度。
- 降低圆角。
- 删除阴影。
- Focus 时使用暖棕色边框。
- 输入框与正文共享同一阅读轴线。

---

### 1.6 UI 配色尚未真正与 Hanser 本身融合

当前 UI 主要由：

```text
深黑
深灰
白
灰
```

构成。

但 Live2D 本身有非常明确的视觉元素：

- 暖棕红头发。
- 深蓝水手服。
- 金色眼睛与星饰。
- 暖白服装。

建议从人物中抽取 UI 色彩：

```text
Night Black
#111218

Sailor Navy
#3B3974

Warm Hair
#B65D3E

Star Gold
#D2B16A

Warm White
#E9E4DD
```

使用原则：

- 深蓝：用户消息、选中态。
- 暖棕红：主要交互、Focus、Active。
- 星金：Hanser marker、少量装饰。
- 暖白：正文。
- 深黑：整体背景。

不要大面积使用高饱和角色色。

---

### 1.7 Header 状态过于工程化

当前顶部存在类似：

```text
文字
后端离线
L2D 已就绪
```

这样的工程信息。

开发模式下可以保留，但成品界面不应该长期显示底层模块状态。

推荐用户只看到：

```text
● 在线
```

或：

```text
✦ 在这里
```

详细状态放在：

```text
Settings / Debug
```

例如：

```text
Agent     Online
Voice     Ready
Live2D    Ready
WebSocket Connected
```

---

### 1.8 当前视觉焦点顺序不合理

当前页面第一视觉焦点更接近：

```text
“要来聊一会儿吗？”
```

而不是 Hanser。

理想顺序应为：

```text
Hanser
↓
她说的话
↓
输入区域
↓
其他功能 UI
↓
装饰
```

---

# 2. 总体设计方向

整体设计目标：

> 不是“ChatGPT + Live2D”，而是一个 Hanser 正在等用户说话的私人房间。

重点不是增加更多视觉元素，而是：

- 删除模板化 UI。
- 收紧空间关系。
- 强化角色存在。
- 减弱产品感。
- 提升页面连续性。

---

# 3. 桌面整体布局

推荐桌面结构：

```text
┌──────┬─────────────────────────────────────────────┐
│      │ Header                                      │
│ Side ├──────────────────────────────┬──────────────┤
│ bar  │                              │              │
│      │       Conversation           │    Hanser    │
│      │                              │              │
│      │       Greeting               │    Live2D    │
│      │                              │              │
│      │       Suggestions            │              │
│      │                              │              │
│      │       Composer               │              │
│      │                              │              │
└──────┴──────────────────────────────┴──────────────┘
```

针对当前约 2048px 宽页面建议：

```text
Sidebar          56–64px collapsed
Chat area        900–1050px
Gap              40–80px
Live2D safe area 380–460px
Right padding    50–80px
```

核心要求：

- Chat 与 Live2D 靠近。
- 中间不保留大面积无意义黑区。
- Live2D 参与布局，而不是通过 absolute 覆盖正文。
- Chat 保持第一阅读优先级。

---

# 4. Greeting 区域重构

当前：

> 要来聊一会儿吗？

建议完全移除标准 Hero。

改成真正的 Persona Greeting。

示例：

```text
✦ HANSER

晚上好。

今天怎么样？

随便说点什么也可以。
```

或者：

```text
✦

晚上好。
今天回来得还挺晚。

怎么样，有什么想说的吗？
```

具体文案应由 Persona 决定，而不是永久写死在前端。

---

## 4.1 Greeting 排版

建议：

```text
font-size: 28–32px
font-weight: 550–600
line-height: 1.4
color: #E9E4DD
```

辅助文字：

```text
font-size: 14px
color: #8D8991
line-height: 1.7
```

最大宽度：

```text
560–620px
```

不要使用过粗的 Hero Bold。

---

## 4.2 删除工程说明

当前类似：

> 这里连接的是本地 Hanser Agent，会保留连续对话与记忆。

建议从首页删除。

这种信息应该放到：

```text
Settings
About
Debug
```

首页只服务于角色沉浸和聊天。

---

# 5. 推荐问题重设计

删除 2×2 卡片。

改为纵向轻量入口：

```text
✦ 今天发生什么了吗？

  最近有没有一直惦记的事？

  还记得我们上次聊到哪了吗？

  想听首歌吗？
```

样式建议：

```text
默认文字：
#8F8C94

hover：
#E3DED8

hover 位移：
translateX(2px)

transition：
120–160ms
```

hover 时可以出现：

```text
—
```

或者左侧细线。

禁止：

- 卡片。
- Shadow。
- Hover 上浮。
- Glow。
- Stagger animation。

---

# 6. Composer 输入区

Composer 是第二重要的视觉组件。

建议：

```text
max-width: 760px
min-height: 72px
max-height: 116px
```

样式：

```css
background: #171920;

border:
1px solid rgba(255,255,255,.06);

border-radius:
10px;

box-shadow:
none;
```

Focus：

```css
border-color:
rgba(190,100,70,.65);

outline:
2px solid rgba(190,100,70,.10);
```

不要：

- 大 Glow。
- 大 Shadow。
- 过强玻璃模糊。

---

## 6.1 输入框内部

删除：

```text
Hanser Agent
```

只保留：

```text
写点什么……
```

可选辅助提示：

```text
Enter 发送 · Shift+Enter 换行
```

但尽量保持低存在感。

---

# 7. 发送按钮

推荐：

```text
尺寸：
32×32 或 36×36

background:
#B65D3E

hover:
#C86B49

disabled:
rgba(182,93,62,.25)

border-radius:
8px
```

Icon：

```text
↑
```

或简洁 Paper Plane。

不建议使用星形作为发送按钮，因为星形应该保留给 Hanser 的视觉语义。

---

# 8. Live2D 区域优化

Live2D 是整个页面最重要的差异化元素。

---

## 8.1 人物位置

当前建议：

- 向左移动约 `60–100px`。
- 向上移动。
- 整体放大约 `5–10%`。
- 头部更接近页面中上区域。
- 不要让角色贴最右边。

目标是让 Hanser 与聊天内容产生空间关系。

---

## 8.2 人物底部视觉落点

建议加入：

```text
极淡椭圆阴影
```

参数方向：

```text
opacity: 4–6%
blur: 40–60px
```

作用：

- 避免角色“漂浮”。
- 增加空间感。
- 不形成卡片或舞台感。

---

## 8.3 背景装饰

当前星点可以保留，但要重新组织。

推荐总量：

```text
5–8 个元素
```

包括：

```text
2–3 个小星点
1 个四角星
1–2 段短航线
1 条 100–160px 极淡细线
```

透明度：

```text
0.04–0.12
```

不要铺满全屏。

不要使用：

- 星空 Canvas。
- 大粒子。
- 光圈。
- 声波。
- 频谱。

---

# 9. Live2D 与 Voice 状态联动

建议统一状态：

```ts
type CharacterVisualState =
  | "idle"
  | "thinking"
  | "speaking"
  | "singing"
  | "listening"
  | "offline";
```

---

## 9.1 Idle

- 页面几乎静止。
- 无背景循环动画。
- Live2D 正常待机。

---

## 9.2 Thinking

消息区域显示：

```text
✦ 让我想想……
```

Live2D：

- 轻微表情变化。
- 不增加额外背景动画。

---

## 9.3 Speaking

- Live2D lipsync。
- 角色附近一到两个星点轻微呼吸。

例如：

```text
opacity:
0.08 → 0.16

duration:
约 2.4s
```

不要加入：

- 音频频谱。
- 声波。
- 光环。
- EQ。

---

## 9.4 Singing

可以比 Speaking 稍明显：

- 星点缓慢亮起。
- 轻微漂移。
- 仍然保持低存在感。

---

# 10. Header 优化

当前 Header 信息较散。

建议改为：

```text
☰   HANSER / 夜航通讯

                         ● 在线     🔊      ⚙
```

Header：

```text
height:
54–58px

background:
#15161C

border-bottom:
1px solid rgba(255,255,255,.035)
```

---

## 10.1 状态显示

用户只看到：

```text
● 在线
```

工程信息移入 Debug。

---

## 10.2 Hanser Agent 名称

当前页面出现多次：

```text
Hanser Agent
```

建议只保留一个稳定身份：

```text
HANSER / 夜航通讯
```

其他地方删除。

---

# 11. Sidebar 优化

collapsed 状态：

```text
width: 56px
```

保留：

```text
新对话
历史
设置
```

Active：

```text
左侧 2px 暖棕红线
```

不使用：

- 大圆形背景。
- 大面积 pill。
- Hover 上浮。

展开状态：

```text
Hanser

+ 新对话

今晚
  关于最近工作的事
  随便聊聊天

最近
  ……
```

避免把所有功能词都世界观化。

---

# 12. 背景层级

当前背景过于接近单一黑色。

建议使用非常微妙的层级：

```text
App background
#111218

Header
#15161C

Sidebar
#15171D

Chat surface
#121319

Composer
#171920
```

视觉上仍然是一整片夜色，但结构更清晰。

---

# 13. 底部黄色长线

当前底部存在一条明显黄色横线。

如果它没有明确功能语义，建议删除。

因为它容易被理解为：

- Progress。
- Loading。
- Audio timeline。

如果希望保留主题装饰，只保留：

```text
40–80px
```

长的一小段频道线。

透明度：

```text
约 15%
```

不要整屏贯穿。

---

# 14. 页面视觉层级

最终应该形成：

```text
第一视觉：Hanser

第二视觉：Hanser 的 Greeting / 回复

第三视觉：输入框

第四视觉：历史 / Voice / Settings

第五视觉：星点、航线等装饰
```

而不是让标题成为第一视觉焦点。

---

# 15. 推荐首页结构示意

```text
┌──────────────────────────────────────────────────────────────────┐
│  ☰   HANSER / 夜航通讯                         ● 在线   🔊   ⚙  │
├────┬─────────────────────────────────────────────────────────────┤
│    │                                                             │
│    │                                                             │
│    │          ✦                                                  │
│    │                                                             │
│    │          晚上好。                              ·            │
│    │          今天怎么样？                                      │
│    │                                                   ✦         │
│    │          随便说点什么也可以。                               │
│    │                                                             │
│    │          ─ 今天发生什么了吗？                               │
│    │          ─ 最近有什么一直惦记的事？          Hanser        │
│    │          ─ 还记得我们上次聊到哪了吗？                       │
│    │          ─ 想听首歌吗？                                    │
│    │                                                             │
│    │       ┌─────────────────────────────┐                       │
│    │       │ 写点什么……                   │                       │
│    │       │                           ↑ │                       │
│    │       └─────────────────────────────┘                       │
│    │                                                             │
└────┴─────────────────────────────────────────────────────────────┘
```

---

# 16. Design Token 建议

```css
:root {
  --bg-app: #111218;
  --bg-header: #15161c;
  --bg-sidebar: #15171d;
  --bg-surface: #171920;
  --bg-surface-hover: #1b1d25;

  --text-primary: #e9e4dd;
  --text-secondary: #99959b;
  --text-muted: #706d74;

  --hanser-navy: #3b3974;
  --hanser-warm: #b65d3e;
  --hanser-warm-hover: #c86b49;
  --hanser-gold: #d2b16a;

  --border-subtle: rgba(255,255,255,.055);
  --border-hover: rgba(255,255,255,.10);

  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 10px;

  --chat-width: 760px;
  --live2d-width: 420px;

  --motion-fast: 120ms;
  --motion-base: 180ms;
}
```

---

# 17. 动效原则

保留：

```text
120–180ms opacity transition
2px hover 位移
2–4px 新消息进入
低频星点呼吸
```

删除：

```text
Card hover translateY
Bounce
Spring overshoot
Glow pulse
大面积 blur
持续输入框发光
```

---

# 18. 推荐实施顺序

不要一次性让 Codex “全部美化”。

推荐拆成五阶段。

---

## Phase 1：Layout

优先只改：

```text
Header
Sidebar
Chat width
Live2D safe area
Composer position
```

不要加装饰。

验收重点：

- 中间空区是否减少。
- Chat 和 Live2D 是否更接近。
- 页面重心是否平衡。

---

## Phase 2：Empty State

删除：

```text
Hero title
工程说明
2×2 Cards
```

替换为：

```text
Persona Greeting
纵向 Suggested Prompts
```

---

## Phase 3：Design Token

统一：

```text
颜色
字体
Spacing
圆角
Border
Motion
```

避免后续组件样式漂移。

---

## Phase 4：Live2D

调整：

```text
位置
缩放
Safe area
椭圆阴影
少量星点
航线装饰
```

---

## Phase 5：状态与动效

增加：

```text
thinking
speaking
singing
voice
hover
focus
reduced-motion
```

---

# 19. 首版不建议增加

暂时不要做：

```text
星空 Canvas
WebGL 粒子背景
Shader
动态天气
实时音乐频谱
大面积视差
复杂页面转场
多套主题
每条消息独立角色动画
```

这些功能会增加：

- 性能成本。
- Debug 成本。
- 风格噪声。
- Live2D 视觉竞争。

但不会显著提升“像 Hanser”的核心体验。

---

# 20. 最终目标

最终页面不应该像：

> 一个加入 Live2D 的 AI Chatbot。

也不应该像：

> 一个为了主题感而堆满星星和动效的角色网页。

应该更接近：

> 一个安静、长期可用的私人聊天空间。  
> Hanser 自然地待在右侧，也存在于 Greeting、Voice、Persona、状态和视觉语言中。  
> 用户首先感受到的是“Hanser 在这里”，其次才意识到这是一个 AI Chatbot。

因此，本轮美化的核心不是“加东西”，而是：

```text
删除模板化结构
+
压缩无意义空间
+
强化角色存在
+
统一颜色与状态
+
让 Live2D、Voice、Persona 与 Chat UI 成为同一个系统
```
