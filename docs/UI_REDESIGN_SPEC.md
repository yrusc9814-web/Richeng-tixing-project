# LifeSync Frontend — UI/UX Redesign Spec (Codex Output)

> Generated: 2026-06-18 | Author: Codex | Status: Phase A-F Output

---

## 1. PRD 对齐差距报告

### 1.1 隐含 PRD 推导

项目内无独立 PRD 文档。从代码结构、数据库 schema、API 路由反推产品定位：

| 维度 | 推导结论 |
|------|---------|
| 产品名 | LifeSync Hub — 本地日程管理 |
| 核心用户 | 单用户（Vanta），本地部署 |
| 核心场景 | 创建日程 → 天气评估 → 微信通知 → 日历同步 |
| 产品形态 | **日程产品**（非任务管理系统） |
| 设计语言 | Apple 风格（-apple-system 字体栈）、暗色侧边栏 |

### 1.2 差距分析

| # | PRD 预期 | 当前实现 | 差距等级 |
|---|---------|---------|---------|
| G1 | 日程产品：以时间线/卡片为主视图 | 表格视图为主，无卡片 | 🔴 HIGH |
| G2 | 信息架构：今日/即将/历史三段清晰 | 左侧 10 个 filter tab 混杂同步状态过滤 | 🟡 MID |
| G3 | 产品形态：日程中心 | 偏「工程管理面板」（系统状态、日志、同步异常在主导航） | 🔴 HIGH |
| G4 | 视觉规范：Liquid Glass / Apple 风格 | 基础 Apple 字体栈，无 blur/glass 效果，无层次感 | 🟡 MID |
| G5 | 信息层级：primary/secondary/tertiary 分明 | 右侧 7 个 info card 平铺无优先级 | 🟡 MID |
| G6 | 闭环操作：产品级交互 | debug 面板（select + pre JSON 输出） | 🔴 HIGH |
| G7 | 动效系统：状态变化有反馈 | 仅 toast-in + spin，无 card/state/sync 动效 | 🟡 MID |
| G8 | 状态系统：统一视觉表达 | status-chip 有颜色但无动效差异、无 icon 一致性 | 🟡 MID |

---

## 2. UI 草图（Wireframe）

### 2.1 页面整体布局

```
┌─────────────────────────────────────────────────────────────────┐
│  LEFT NAV (200px)  │   TASK CENTER (flex)    │  DETAIL (340px)  │
├───────────────────┼──────────────────────────┼──────────────────┤
│                   │                          │                  │
│  ┌─ Logo ──────┐  │  ┌─ Header ───────────┐  │  ┌─ Task Detail ┐│
│  │ L  LifeSync │  │  │ 日程管理    [🔄][➕]│  │  │ (选中任务)   ││
│  └─────────────┘  │  └────────────────────┘  │  │              ││
│                   │                          │  │ 标题         ││
│  ─ 日程视图 ──    │  ┌─ Stat Cards ──────┐  │  │ 时间/地点    ││
│  ▸ 今日           │  │ 📅 1  📌 1  ⏰ 1  │  │  │              ││
│  ▸ 即将开始       │  └────────────────────┘  │  │ ┌─ 天气 ───┐ ││
│  ▸ 未来           │                          │  │ │ 27°C ☀️  │ ││
│  ▸ 已归档         │  ┌─ Task Cards ┐        │  │ └──────────┘ ││
│                   │  │ ┌─────────┐ │        │  │              ││
│  ─ 同步 ─────     │  │ │TaskCard1│ │        │  │ ┌─ 同步 ───┐ ││
│  ○ 已同步 3       │  │ │synced ✓ │ │        │  │ │ ✅ Synced │ ││
│  ⚠ 待同步 1       │  │ └─────────┘ │        │  │ │ ext_id... │ ││
│  ✕ 失败   0       │  │ ┌─────────┐ │        │  │ └──────────┘ ││
│                   │  │ │TaskCard2│ │        │  │              ││
│  ─ 系统 ─────     │  │ │pending  │ │        │  │ ┌─ 操作 ───┐ ││
│  ⚙ 设置           │  │ └─────────┘ │        │  │ │[天气][微信]│ ││
│  📝 日志           │  │ ┌─────────┐ │        │  │ │[日历][刷新]│ ││
│                   │  │ │TaskCard3│ │        │  │ └──────────┘ ││
│  ┌─ Status ───┐   │  │ │blocked  │ │        │  └──────────────┘│
│  │ DB  ●  OK  │   │  │ └─────────┘ │        │                  │
│  │ API ●  OK  │   │  └─────────────┘        │  ┌─ 今日日程 ───┐ │
│  └────────────┘   │                          │  │ 15:00 任务A  │ │
│                   │  ← 1-3 / 共 3 →          │  │ 16:00 任务B  │ │
│                   │                          │  └──────────────┘│
└───────────────────┴──────────────────────────┴──────────────────┘
```

### 2.2 单任务卡片结构

```
┌─────────────────────────────────────────────┐
│  ● RC Smoke Test — Calendar Sync      P1    │  ← 标题 + 优先级
│  📍 Xiamen · 2026-06-18 15:00 → 16:00      │  ← 地点 + 时间
│                                             │
│  ┌─ Status ──────────────────────────────┐ │
│  │ ✅ 已同步 → Apple日历                  │ │  ← 同步状态 chip
│  │ 🌤 27°C · weather_code:95             │ │  ← 天气摘要（如有）
│  └───────────────────────────────────────┘ │
│                                             │
│  [🌦 天气] [💬 微信] [📅 日历] [👁 详情]    │  ← 内联操作按钮
└─────────────────────────────────────────────┘
```

**卡片状态变体：**

```
synced:    ┌──── ✅ 已同步 ────┐  绿色左边框
pending:   ┌──── ⏳ 待同步 ────┐  蓝色左边框 + pulse
syncing:   ┌──── 🔄 同步中 ────┐  蓝色左边框 + spin
blocked:   ┌──── ⛔ 已阻断 ────┐  橙色左边框
failed:    ┌──── ✕ 同步失败 ──┐  红色左边框
skipped:   ┌──── ⊘ 已跳过 ────┐  灰色左边框
```

### 2.3 交互流程草图

```
用户点击 TaskCard
  │
  ├─→ 右侧 Detail Panel 展开该任务详情
  │     ├─ 天气区块（如有缓存数据，即时显示）
  │     ├─ 同步区块（显示 sync_state + external_id）
  │     └─ 操作区块（4 按钮：天气/微信/日历/刷新）
  │
  ├─→ 点击 [🌦 天气]
  │     ├─ 按钮 → loading state (spinner)
  │     ├─ API: POST /api/weather/evaluate
  │     ├─ 成功 → 卡片天气区块淡入显示
  │     └─ 失败 → toast error + 卡片天气区块显示错误
  │
  ├─→ 点击 [📅 日历]
  │     ├─ 按钮 → loading state
  │     ├─ 卡片左边框 → syncing (pulse blue)
  │     ├─ API: POST /api/calendar/sync
  │     ├─ synced → 左边框变绿 + ✅ micro animation
  │     ├─ skipped → toast info "已同步，跳过"
  │     └─ failed → 左边框变红 + toast error
  │
  └─→ 点击 [💬 微信]
        ├─ 按钮 → loading state
        ├─ API: POST /api/wechat/send
        ├─ sent → toast success + ✅
        └─ blocked → toast warning + 透明显示原因
```

---

## 3. UI 重构方案

### 3.1 Layout 重构

| 区域 | 当前 | 重构后 | 理由 |
|------|------|--------|------|
| 左侧栏宽度 | 220px | 200px | 减少视觉占用，信息密度过高 |
| 左侧 filter | 10 个平铺 tab | 分 3 组：日程视图(4) / 同步(3) / 系统(2) | 信息架构分层 |
| 左侧 nav | 8 个 item 混杂 | 精简为 5 个：日程/创建/同步/设置/日志 | 去掉冗余 |
| 主区域 | 表格 | **卡片列表** + 保留表格切换 | 日程产品以卡片为主 |
| 右侧 panel | 7 card 平铺 | 3 区域：任务详情(动态) / 今日日程 / 天气概览 | 有优先级 |
| 闭环操作 | debug 面板 | 融入任务详情卡的操作区 | 产品级交互 |

### 3.2 信息层级

| 层级 | 内容 | 视觉表达 |
|------|------|---------|
| **Primary** | 任务标题、时间、同步状态 | 14px bold / 13px medium / status chip |
| **Secondary** | 地点、优先级、天气摘要 | 12px regular / muted color |
| **Tertiary** | external_id、创建时间、描述 | 11px / very muted / collapsible |

### 3.3 视觉规范

```
Spacing System:
  --space-xs:  4px
  --space-sm:  8px
  --space-md:  16px
  --space-lg:  24px
  --space-xl:  32px

Card Style:
  background: rgba(255, 255, 255, 0.72)
  backdrop-filter: blur(20px) saturate(180%)
  border: 1px solid rgba(255, 255, 255, 0.18)
  border-radius: 12px
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06)
  padding: 16px

Sidebar (保持暗色):
  background: rgba(26, 29, 41, 0.92)
  backdrop-filter: blur(20px)
```

---

## 4. 状态系统设计

### 4.1 统一状态模型

| 状态 | 颜色 | Icon | 动效 | 语义 |
|------|------|------|------|------|
| `synced` | #52c41a 绿 | ✅ | scale-in bounce | 同步成功 |
| `pending` | #1890ff 蓝 | ⏳ | 无 | 等待同步 |
| `syncing` | #1890ff 蓝 | 🔄 | spin 1s linear | 同步进行中 |
| `blocked` | #faad14 橙 | ⛔ | pulse 2s | 配置缺失，透明阻断 |
| `failed` | #ff4d4f 红 | ✕ | shake 0.3s | 同步失败 |
| `skipped` | #999 灰 | ⊘ | 无 | 已同步/不符合条件 |
| `stale` | #faad14 橙 | ⚠ | 无 | 数据过期 |

### 4.2 CSS 实现

```css
.status-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 4px;
  font-size: 12px; font-weight: 500;
}
.status-chip.synced  { color: #52c41a; background: rgba(82,196,26,0.1); }
.status-chip.pending { color: #1890ff; background: rgba(24,144,255,0.1); }
.status-chip.syncing { color: #1890ff; background: rgba(24,144,255,0.1); }
.status-chip.blocked { color: #faad14; background: rgba(250,173,20,0.1); }
.status-chip.failed  { color: #ff4d4f; background: rgba(255,77,79,0.1); }
.status-chip.skipped { color: #999;    background: rgba(153,153,153,0.1); }
.status-chip.stale   { color: #faad14; background: rgba(250,173,20,0.1); }
```

---

## 5. 动效系统设计

### 5.1 动效清单

| # | 名称 | 触发 | 实现 | Easing | GPU Safe |
|---|------|------|------|--------|----------|
| A1 | card-enter | 卡片首次渲染 | opacity 0→1 + translateY 12px→0 | `cubic-bezier(0.22,1,0.36,1)` | ✅ |
| A2 | state-transition | 状态 chip 变化 | background-color crossfade 0.3s | `ease` | ✅ |
| A3 | sync-pulse | syncing 状态 | box-shadow pulse 1.5s infinite | `ease-in-out` | ✅ |
| A4 | success-bounce | synced 成功瞬间 | scale 1→1.05→1, 0.4s | `cubic-bezier(0.34,1.56,0.64,1)` | ✅ |
| A5 | error-shake | failed 失败瞬间 | translateX 0→-4→4→-2→0, 0.3s | `ease` | ✅ |
| A6 | loading-skeleton | 数据加载中 | shimmer gradient 1.5s infinite | `linear` | ✅ |
| A7 | detail-slide-in | 右侧详情展开 | opacity 0→1 + translateX 8px→0 | `cubic-bezier(0.22,1,0.36,1)` | ✅ |
| A8 | button-press | 按钮点击 | scale 0.95, 0.1s | `ease` | ✅ |

### 5.2 Keyframes

```css
@keyframes card-enter {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}

@keyframes sync-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(24,144,255,0.3); }
  50%      { box-shadow: 0 0 0 6px rgba(24,144,255,0); }
}

@keyframes success-bounce {
  0%   { transform: scale(1); }
  40%  { transform: scale(1.05); }
  100% { transform: scale(1); }
}

@keyframes error-shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-4px); }
  75% { transform: translateX(4px); }
}

@keyframes shimmer {
  0%   { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

@keyframes detail-slide-in {
  from { opacity: 0; transform: translateX(8px); }
  to   { opacity: 1; transform: translateX(0); }
}
```

### 5.3 性能约束

- 所有动画仅使用 `opacity` + `transform`（GPU 加速）
- 禁止动画 `width/height/top/left/margin`
- `backdrop-filter` 仅用于 card 和 sidebar（最多 2 层同时渲染）
- `will-change: transform, opacity` 仅在动画期间设置

---

## 6. 前端结构改造建议

### 6.1 改造范围

| 文件 | 改动类型 | 内容 |
|------|---------|------|
| `index.html` | 结构调整 | 左侧栏分组、主区域加卡片容器、右侧栏重组 |
| `style.css` | 大幅扩展 | CSS variables 扩展、card 样式、动效 keyframes、状态系统 |
| `app.js` | 功能扩展 | renderTaskCards()、card 交互、detail panel 逻辑、动效触发 |

### 6.2 不改动

- API 调用逻辑（apiFetch/fetchTasks/createTask 等）
- 数据模型（STATUS_SYNC_LABELS/TASK_STATUS_LABELS 等）
- 创建/编辑 modal 逻辑
- 后端 API/DB

### 6.3 新增 JS 函数

```
renderTaskCards(tasks)     — 卡片列表渲染
selectTaskCard(taskId)     — 选中卡片 → 右侧详情展开
renderTaskDetail(task)     — 右侧详情面板渲染
triggerStateAnimation(el, state) — 触发状态动效
showSkeleton(container)    — 显示骨架屏
hideSkeleton(container)    — 隐藏骨架屏
```

### 6.4 视图切换

主区域顶部加 `[卡片视图] [表格视图]` 切换，默认卡片视图，保留表格视图兼容。
