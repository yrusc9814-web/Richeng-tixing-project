# M2 同步状态机设计文档

版本: Phase 5 最终版（v4 — Codex 审查修订）  
审查: Codex PASS_WITH_NOTES → 3 MAJOR + 4 MINOR + 2 建议已全部修正

---

## 1. 10 个状态语义

### 结论

10 个状态分为四组：初始态（pending）、执行态（in_progress）、终态（synced / failed_permanent / skipped / disabled / deleted）、中间态（failed / stale / orphaned）。

引擎锁定定义：标记为「是」时，同步引擎的状态扫描器不会选取该状态的记录进行自动推进。手动 API 操作不受此限制，但仍受状态转换表约束（例如 in_progress 锁定后仍可通过 API 手动 disable，但不能通过 API 跳转到 pending）。

### 详细定义

| # | 状态 | 含义 | 进入条件 | 离开条件 | 引擎锁定 |
|---|------|------|----------|----------|----------|
| 1 | pending | 等待同步 | ① sync_state 创建时默认 ② disabled→pending 手动重新启用 ③ stale→pending 引擎触发 ④ failed_permanent→pending 管理员强制重置 | → in_progress（引擎选取）→ skipped（手动跳过）→ orphaned（手动标记，需 external_id 已存在）→ disabled（手动禁用）→ deleted（手动删除） | 否 |
| 2 | in_progress | 同步执行中 | pending→in_progress（引擎选取） | → synced（成功）→ failed（可重试失败，需满足 attempt 条件）→ failed_permanent（不可重试错误/超次/超时且 attempt≥4）→ disabled（手动禁用）→ deleted（手动删除） | 是 |
| 3 | synced | 同步成功（终态） | in_progress→synced（同步成功完成） | → stale（数据过期，T）→ orphaned（手动标记）→ disabled（手动禁用）→ deleted（手动删除） | 否 |
| 4 | failed | 同步失败，可重试 | ① in_progress→failed（执行失败或超时，且当前 sync_attempt ≤ 3） | → in_progress（引擎重试）→ failed_permanent（超过重试上限）→ disabled（手动禁用）→ deleted（手动删除） | 否 |
| 5 | failed_permanent | 永久失败（终态） | ① failed→failed_permanent（MAX(sync_attempt) ≥ 4）② in_progress→failed_permanent（不可重试错误，或超时且 sync_attempt ≥ 4） | → pending（管理员手动强制重置）→ orphaned（手动标记）→ disabled（手动禁用）→ deleted（手动删除） | 是 |
| 6 | skipped | 跳过同步（终态） | pending→skipped（手动跳过） | → pending（手动重新启用）→ orphaned（手动标记）→ disabled（手动禁用）→ deleted（手动删除） | 否 |
| 7 | stale | 数据已过期 | synced→stale（检测到任务内容变更，payload_hash 不匹配） | → pending（引擎触发）→ orphaned（手动标记）→ disabled（手动禁用）→ deleted（手动删除） | 否 |
| 8 | orphaned | 目标端数据丢失 | Phase 5：手动 API 标记（除 deleted 外的任何状态均可标记）。条件：pending→orphaned 仅在已有 external_id 时允许。Phase 6+：可由 EventKit 事件监听自动触发 | → deleted（手动清理 / future 自动清理）→ disabled（手动禁用） | 是 |
| 9 | disabled | 用户主动禁用（终态） | 任何状态→disabled（手动 API） | → pending（手动重新启用）→ orphaned（手动标记）→ deleted（手动删除） | 是 |
| 10 | deleted | 软删除（终态） | ① 任何状态→deleted（手动 API）② orphaned→deleted（手动清理 / future 自动清理） | 无。绝对终态。 | 是 |

重要区分：status=deleted 是状态机语义上的软删除终态。DELETE FROM sync_state 是数据库物理清理动作，不属于状态机状态转换范围。

### orphaned 进入规则（Phase 5）

| 当前状态 | → orphaned | 条件 |
|----------|------------|------|
| deleted | ❌ 非法 | 终态不可出 |
| orphaned | ○ 幂等 | X→X 允许，无副作用 |
| pending | ✅ M | 仅在 external_id IS NOT NULL 时允许；否则非法 |
| 其他 7 个状态（in_progress / synced / failed / failed_permanent / skipped / stale / disabled） | ✅ M | 无附加条件 |

---

## 2. 合法状态转换表（10×10）

图例：A = 引擎自动 | M = API 手动 | T = 外部事件触发 | — = 非法 | ○ = 幂等

| from ╲ to | pending | in_progress | synced | failed | failed_permanent | skipped | stale | orphaned | disabled | deleted |
|-----------|---------|-------------|--------|--------|------------------|---------|-------|----------|----------|---------|
| pending | ○ | A | — | — | — | M | — | M¹ | M | M |
| in_progress | — | ○ | A | A² | A³ | — | — | M | M | M |
| synced | — | — | ○ | — | — | — | T⁴ | M | M | M |
| failed | — | A | — | ○ | A | — | — | M | M | M |
| failed_permanent | M | — | — | — | ○ | — | — | M | M | M |
| skipped | M | — | — | — | — | ○ | — | M | M | M |
| stale | A | — | — | — | — | — | ○ | M | M | M |
| orphaned | — | — | — | — | — | — | — | ○ | M | M / A* |
| disabled | M | — | — | — | — | — | — | M | ○ | M |
| deleted | — | — | — | — | — | — | — | — | — | ○ |

¹ pending→orphaned 仅在 external_id IS NOT NULL 时允许。  
² in_progress→failed：sync_attempt ≤ 3 时。含执行失败和超时。  
³ in_progress→failed_permanent：sync_attempt ≥ 4 时（含超时），或不可重试错误。  
⁴ synced→stale 的 T 触发当前仅限 payload_hash 不匹配。TTL 自动过期为「待用户确认项 C1」，不作为 Phase 5 正式结论。  
\* orphaned→deleted：M = Phase 5（API 手动清理）；A = future（Phase 6+ 自动清理）

### 关键转换说明

| 转换 | 触发 | 条件 |
|------|------|------|
| pending → in_progress | A | 引擎选取 pending 记录 |
| in_progress → synced | A | 同步成功。写入 sync_log（sync_result=success） |
| in_progress → failed | A | 可重试错误或超时，且当前 sync_attempt ≤ 3。写入 sync_log（sync_result=failed） |
| in_progress → failed_permanent | A | ① 不可重试错误 ② 当前 sync_attempt ≥ 4（含超时场景）。写入 sync_log（sync_result=failed） |
| failed → in_progress | A | 引擎重试。sync_attempt 递增 |
| failed → failed_permanent | A | MAX(sync_attempt) ≥ 4 |
| failed_permanent → pending | M | 管理员手动强制重置。sync_attempt 从 1 重新计数。引擎不自动推进 |
| synced → stale | T | 任务内容变更，payload_hash 不匹配。检测机制：sync_client/payload.py 对比 hash。写入 sync_log（sync_result=drift_detected, drift_detected=1） |
| stale → pending | A | 引擎检测到 stale，自动触发重新同步 |
| orphaned → deleted | M | Phase 5 通过 API 手动清理。future：自动 |
| disabled → pending | M | 用户手动重新启用 |
| pending → orphaned | M | 仅在 external_id 已存在时允许（否则返回 422） |
| 任何 → disabled | M | API 调用 |
| 任何 → deleted | M | API 调用。软删除（status=deleted），记录保留 |

---

## 3. 非法状态转换规则

- 拒绝响应码：422 Unprocessable Entity
- 幂等转换 X→X：允许，返回 200，不写 sync_log，无副作用。幂等转换不属于非法转换。deleted→deleted 同样是幂等允许
- 非法转换：指未在转换矩阵中标注为 A / M / T / ○ 的所有其他转换。不写 sync_log
- deleted→任何非 deleted 状态均非法——deleted 是绝对终态

### 错误消息格式

```json
{
  "error": "invalid_state_transition",
  "message": "Cannot transition from '{current_status}' to '{target_status}'",
  "current_status": "synced",
  "target_status": "pending"
}
```

### 非法转换分类

| 类型 | 示例 | 原因 |
|------|------|------|
| 终态不可出 | deleted → 任何 | deleted 是绝对终态 |
| 跳跃转换 | pending → synced | 必须经过 in_progress |
| 锁定状态旁路 | in_progress → pending | 只能出到 synced/failed/failed_permanent |
| pending→orphaned 无 external_id | external_id IS NULL | 目标端未创建过，不存在「孤立」的前提 |

---

## 4. Retry 策略

max_retry=3。重试计数基于 sync_logs.sync_attempt。指数退避间隔。

### sync_attempt 编号规则

sync_attempt 是尝试编号（1-based），不是失败次数。每次进入 in_progress 时确定。

| sync_attempt | 含义 | 对应关系 |
|--------------|------|----------|
| 1 | 首次尝试 | — |
| 2 | 第 2 次尝试 | 即第 1 次重试 |
| 3 | 第 3 次尝试 | 即第 2 次重试 |
| 4 | 第 4 次尝试 | 即第 3 次重试。若本次仍失败 → failed_permanent |

### 失败次数与状态的映射

| 累计失败次数 | 当前 sync_attempt | 失败后状态 |
|--------------|-------------------|------------|
| 第 1 次失败 | 1 | failed |
| 第 2 次失败 | 2 | failed |
| 第 3 次失败 | 3 | failed |
| 第 4 次失败 | 4 | failed_permanent |

### 阈值判定

引擎在 in_progress → 失败时检查当前 MAX(sync_attempt)：

- MAX(sync_attempt) ≥ 4 → 进入 failed_permanent
- MAX(sync_attempt) ≤ 3 → 进入 failed

此规则适用于所有失败场景——包括执行失败、超时、不可重试错误（不可重试错误直接进入 failed_permanent，不经过 failed，不计入 attempt 计数）。

### sync_attempt 计算方式

```sql
-- 确定本次尝试编号（在进入 in_progress 前执行）
SELECT COALESCE(MAX(sync_attempt), 0) + 1 AS next_attempt
FROM sync_logs
WHERE sync_id = ?;
```

### 重试间隔（指数退避）

| 退避阶段 | 等待时间 | 累计 |
|----------|----------|------|
| 第 1 次失败后 | 30 秒 | 30s |
| 第 2 次失败后 | 90 秒 | 120s |
| 第 3 次失败后 | 270 秒 | 390s |

引擎实现方式：failed 记录保持 failed 状态。引擎扫描时计算 当前时间 − 最近一次 sync_log.created_at，若 ≥ 退避间隔 → 触发重试（failed → in_progress）。

Jitter 建议：编码阶段可在退避间隔上加入 ±20% 随机抖动（jitter），避免多个 failed 记录在同秒被批量重试造成并发风暴。Phase 5 设计不强制要求 jitter，列为编码阶段建议。

### 可重试 vs 不可重试

| 分类 | 示例 | 失败后目标 |
|------|------|------------|
| 可重试 | 网络超时、连接拒绝、HTTP 5xx、429 限流 | failed（按 attempt 规则） |
| 不可重试 | sync_target 无效、认证失败（401/403）、数据格式错误（400） | 直接 failed_permanent（不计入 attempt 计数） |

不可重试错误不经过 failed——从 in_progress 直接进入 failed_permanent。

---

## 5. Timeout 策略

timeout=300 秒。使用 sync_state.updated_at 作为 in_progress 开始时间的近似起点。

### 超时检测方式

- pending → in_progress 转换时，update_sync_state() 设置 updated_at = _iso_now()
- 引擎定时扫描 status='in_progress' 的记录
- 若 当前时间 − updated_at > 300s → 判定超时
- last_synced_at 不用于此判断

### 为何可以依赖 updated_at

- updated_at 在每次 update_sync_state() 时被设为当前时间（sync_state_service.py:212）
- pending → in_progress 是一次 update，因此 updated_at 反映本次 in_progress 开始时间
- 前提：in_progress 期间无其他字段被更新

⚠️ 见残余风险 R1：in_progress 期间若有其他字段更新（如 API 手动修改 external_id），updated_at 被刷新，timeout 检测可能误判。编码阶段如发现此路径，必须改用 sync_logs.created_at（现有字段）作为超时起点，或新增 started_at / locked_at 字段，否则不得上线。

### 超时后处理

| 步骤 | 说明 |
|------|------|
| 超时判定 | 当前时间 − updated_at > 300s |
| 目标状态 | 取决于当前 sync_attempt：≤ 3 → failed；≥ 4 → failed_permanent |
| 写入 sync_log | 是。sync_result=failed、error_code='timeout' |
| 后续流程 | 进入 failed 则按 §4 retry 策略继续；进入 failed_permanent 则终止重试 |

优先级规则：timeout 本质上是一次失败结果。超时后的目标状态必须遵循 retry 上限规则——sync_attempt ≥ 4 的超时等同于「第 4 次失败」，应进入 failed_permanent，而非 failed。这样保证 timeout 不会绕过 max_retry 上限。

---

## 6. Terminal 状态完整定义

5 个 terminal 状态（引擎不自动推进）：synced、failed_permanent、skipped、disabled、deleted。

### synced

| 维度 | 说明 |
|------|------|
| 进入条件 | in_progress → synced（同步成功） |
| 打破条件 | 任务内容变更，payload_hash 不匹配 → stale（T） |
| 打破检测 | sync_client/payload.py 对比 hash。触发时写入 sync_log：sync_result=drift_detected, drift_detected=1, payload_hash_before/after 记录变化, triggered_by=system |
| 打破后路径 | stale → pending → in_progress → synced |

TTL 自动过期：当前 Phase 5 正式结论中，synced → stale 仅由 payload_hash 变化触发。TTL 自动过期（如超过 24 小时未验证自动变 stale）列为「待用户确认项 C1」——非 Phase 5 结论，不在主状态转换矩阵中。若未来启用 TTL，其触发方式同样是 T（synced → stale）。

### failed_permanent

| 维度 | 说明 |
|------|------|
| 进入条件 | ① MAX(sync_attempt) ≥ 4 且本次失败（含超时）② 不可重试错误 |
| 恢复路径 | 仅 M：failed_permanent → pending（管理员强制重置，sync_attempt 从 1 重新计数） |
| 不可自动恢复 | 引擎绝不从 failed_permanent 自动推进 |

### skipped

| 维度 | 说明 |
|------|------|
| 进入条件 | pending → skipped（API 手动） |
| 恢复路径 | skipped → pending（API 手动）→ 正常同步流程 |

### disabled

| 维度 | 说明 |
|------|------|
| 进入条件 | 任何状态 → disabled（API 手动） |
| 恢复路径 | disabled → pending（API 手动）→ 正常同步流程 |

### deleted

| 维度 | 说明 |
|------|------|
| 进入条件 | ① 任何非 deleted 状态 → deleted（API 手动）② orphaned → deleted（手动） |
| 后续 | 绝对终态。状态机不再处理 |
| 物理清理 | 不属于状态机范围 |

---

## 7. Auto / Manual 推进规则

### 引擎自动推进

| # | 路径 | 触发条件 |
|---|------|----------|
| 1 | pending → in_progress | 引擎选取 pending 记录 |
| 2 | in_progress → synced | 同步成功 |
| 3 | in_progress → failed | 可重试错误或超时，且当前 sync_attempt ≤ 3 |
| 4 | in_progress → failed_permanent | 不可重试错误，或当前 sync_attempt ≥ 4（含超时） |
| 5 | failed → in_progress | 重试间隔到期且 MAX(sync_attempt) ≤ 3 |
| 6 | failed → failed_permanent | MAX(sync_attempt) ≥ 4 |
| 7 | stale → pending | 引擎检测到 stale 记录 |
| 8 | in_progress 超时 → failed 或 failed_permanent | 取决于当前 sync_attempt（见 §5） |

### API 手动触发

| # | 路径 | 说明 |
|---|------|------|
| M1 | pending → skipped | 跳过同步 |
| M2 | skipped → pending | 重新启用 |
| M3 | 任何 → disabled | 禁用同步 |
| M4 | disabled → pending | 重新启用 |
| M5 | 任何 → deleted | 软删除 |
| M6 | failed_permanent → pending | 管理员强制重置（重置计数） |
| M7 | orphaned → deleted | 清理孤立记录 |
| M8 | 任何（除 deleted 和 orphaned 自身）→ orphaned | 手动标记为目标端数据丢失。pending→orphaned 需 external_id 已存在 |

### Trigger（外部事件）触发

| # | 路径 | 说明 |
|---|------|------|
| T1 | synced → stale | 任务内容变更，payload_hash 不匹配。sync_result=drift_detected |
| T2 | 任何 → orphaned | Phase 5 不支持。Phase 6+ / Apple sync M1 后由 EventKit 事件监听触发 |

### sync_log 写入规则

#### 引擎同步写入

| 转换 | sync_result | 其他字段 |
|------|-------------|----------|
| in_progress → synced | success | last_synced_at 更新 |
| in_progress → failed | failed | error_code 记录具体错误 |
| in_progress → failed_permanent（超次） | failed | error_code 记录具体错误 |
| synced → stale（T） | drift_detected | drift_detected=1；payload_hash_before/after 记录变化；triggered_by=system |

#### 手动管理动作写入

手动状态变更（disable / enable / soft delete / orphaned 标记 / skipped / failed_permanent 重置）属于管理操作而非同步操作。规则如下：

| 转换 | sync_log 策略 |
|------|---------------|
| pending → skipped | 写 sync_log，sync_result=skipped |
| skipped → pending | 不强制写 sync_log。编码阶段可写审计日志，但不伪造 success/failed |
| 任何 → disabled | 同上 |
| disabled → pending | 同上 |
| 任何 → deleted | 同上 |
| failed_permanent → pending（强制重置） | 同上 |
| 手动 → orphaned | 同上 |

若当前 sync_logs schema 不适合记录纯管理动作，编码阶段可只更新 sync_state 而不写 sync_log。管理动作不应使用 sync_result=success 或 failed（这些值仅用于同步引擎产出的日志）。

#### 不写 sync_log 的场景

| 场景 | 原因 |
|------|------|
| 幂等转换（X→X） | 无状态变化 |
| 非法转换尝试 | 未产生同步操作 |
| 物理 DELETE FROM sync_state | 非状态机日志范围 |

写入字段（均为现有字段）：  
sync_id、local_task_id、sync_target、sync_attempt、sync_result、error_code、error_message、drift_detected、drift_fields、payload_hash_before、payload_hash_after、external_id_before、external_id_after、request_id、triggered_by、created_at。

---

## 残余风险

| # | 风险 | 等级 | 说明 |
|---|------|------|------|
| R1 | updated_at 被覆盖 | 中 | in_progress 期间若有其他字段更新（如 API 手动修改 external_id），updated_at 被刷新，timeout 检测可能误判为未超时。Phase 5 可接受 updated_at 作为临时设计。编码阶段如发现此路径，必须改用 sync_logs.created_at（现有字段，每次 sync attempt 写入日志时记录的时间戳）作为超时起点，或新增 started_at / locked_at 字段，否则不得上线 |
| R2 | orphaned 无自动检测 | 中 | 设计已标注 manual/future。Apple sync M1 时必须补上 EventKit 监听 |
| R3 | sync_attempt 并发 | 低 | 两实例同时选取同一 failed 记录可能导致 attempt 重复。编码阶段需加锁 |

---

## 待用户确认项（非 Phase 5 设计结论）

| # | 项目 | Kiro 建议值 | 影响 |
|---|------|-------------|------|
| C1 | stale 的 TTL 自动过期（超过 N 小时未验证自动变 stale） | 24 小时 | 需额外定时任务 + 修改 stale 触发条件 |
| C2 | deleted 物理清理保留时长 | 30 天 | 数据库维护 |
| C3 | orphaned 自动清理等待时间 | 24 小时 | Phase 6+ |
| C4 | 重试间隔值（base / factor / max_gap） | 30s / ×3 / 270s | 可调整。编码阶段建议加 ±20% jitter |

---

## 附录：Codex 审查问题对应修复表

| Codex 编号 | 级别 | 问题 | 修复位置 | 修复方式 |
|------------|------|------|----------|----------|
| M1 | MAJOR | timeout 与 retry-limit 优先级冲突 | §5 超时后处理 + §2 in_progress 行脚注²³ + §7 引擎自动推进 #8 | 明确：超时后目标状态取决于 sync_attempt（≤3→failed，≥4→failed_permanent）。§5 末尾新增优先级规则说明 |
| M2 | MAJOR | drift_detected 映射缺失 | §2 关键转换 synced→stale + §7 sync_log 写入规则表 | 显式写入：synced→stale 对应 sync_result=drift_detected, drift_detected=1 |
| M3 | MAJOR | stale TTL 未覆盖 | §6 synced 定义 + §2 脚注⁴ + 待确认项 C1 | 明确：Phase 5 仅 payload_hash 触发。TTL 留给 C1 |
| m1 | MINOR | orphaned 条件表自引用 | §1 orphaned 进入规则表 | "其他 8 个状态"→"其他 7 个状态（逐列排除 deleted + orphaned 自身）" |
| m2 | MINOR | R1 前向引用缺失 | §5 timeout 策略末尾 | 新增 ⚠️ 引用框指向 §残余风险 R1。补充说明 sync_logs.created_at 是现有字段 |
| m3 | MINOR | 退避 jitter 缺失 | §4 重试间隔末尾 | 新增 Jitter 建议段：±20% 随机抖动，编码阶段建议，Phase 5 不强制 |
| m4 | MINOR | 手动变更 sync_result 未定义 | §7 sync_log 写入规则新增「手动管理动作写入」子节 | 明确 pending→skipped 写 skipped；其他管理动作不强制写 sync_log。编码阶段可只更新 sync_state |
| S1 | 建议 | 引擎锁定未定义 | §1 结论段末尾 | 新增「引擎锁定定义」段 |
| S2 | 建议 | 幂等与非法关系不清 | §3 非法转换规则 | 补：幂等转换不属于非法转换；deleted→deleted 也是幂等；deleted→任何非 deleted 非法 |

---

本文档为 M2 状态机设计基线。不含代码实现、API 端点设计、数据库迁移语句。Codex 审查 3 MAJOR / 4 MINOR / 2 建议已全部修正。