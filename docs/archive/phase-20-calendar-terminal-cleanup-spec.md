Phase 20: 任务终止态到 Apple Calendar 事件清理闭环

## 背景
当前 life-sync-hub 在 task completed/cancelled/deleted 时，没有清理 Apple Calendar 中对应的 [SYNC-TEST] 事件。当任务状态变为终止态后，sync_state 仍然停留在 synced，Calendar 事件无人清理。

## 目标
在 task completed/cancelled/deleted 时，自动清理 Apple Calendar 中的对应事件。

## 架构决策

### 状态转移规则
根据 state_transition_validator.py:
- `synced` → `deleted` via `manual` trigger — 合法 ✅
- `synced` → `orphaned` via `manual` trigger — 合法 ✅
- `synced` → `disabled` via `manual` trigger — 合法 ✅
- `pending` → `orphaned` 需要 `has_external_id=True`

Phase 20 选择：清理成功后 sync_state 进入 `disabled` 状态（非 engine-scannable，明确标识已处理）。

### 清理触发点
清理逻辑应注入到 tasks router 中，在以下三个入口触发：

1. **`complete_task()`** (POST /api/tasks/{task_id}/complete)
   - 任务进入 completed → 查找 apple_calendar sync_state
   - 如果 sync_state 有 external_id → 调用 remove_event
   - 成功后 sync_state → disabled，否则 failed

2. **`update_task()`** (PATCH /api/tasks/{task_id})
   - 当 status 变为 `cancelled` → 查找 apple_calendar sync_state
   - 如果 sync_state 有 external_id → 调用 remove_event
   - 成功后 sync_state → disabled

3. **删除任务** - 当前没有 DELETE endpoint
   - 不实现 DELETE（Phase 20 范围外）
   - 但 deleted sync_state 路径仍需支持：如果已有 deleted 状态的 sync_state，不报错（幂等）

### remove_event 幂等与容错
- `AppleSyncAdapter.remove_event(external_id)` 已支持幂等：事件已不存在时返回 True
- remove_event 失败时（返回 False），sync_state 进入 failed（engine 可重试）
- remove_event 因 TCC 权限失败时（Calendar 不可用），记录错误但不阻塞任务状态变更

### Calendar / Reminder 隔离
- 只操作 target='apple_calendar' 的 sync_state
- apple_reminder 的 sync_state 完全不动
- 重用现有的 `_CALENDAR_SYNC_TARGET = "apple_calendar"`

## 实现计划

### 文件修改

1. **`local_api/routers/tasks.py`**
   - 新增函数 `_cleanup_calendar_on_terminal(task_id: str) -> None`
   - 在 `complete_task()` 完成后调用
   - 在 `update_task()` 中当 status→cancelled 时调用
   - `_cleanup_calendar_on_terminal` 逻辑：
     a. 查找 task_id 对应的 apple_calendar sync_state
     b. 如果没有 sync_state 或没有 external_id → 直接返回（无事件可清理）
     c. 如果 sync_status 是 deleted/disabled/orphaned → 直接返回（已处理）
     d. 调用 AppleSyncAdapter(target='apple_calendar').remove_event(external_id)
     e. 成功 → transition_sync_state(sync_id, 'disabled', trigger='manual')
     f. 失败 → transition_sync_state(sync_id, 'failed', trigger='manual') + 记录日志
     g. 异常（如 TCC 拒绝）→ 记录日志但不传播异常（不阻塞任务状态变更）

2. **`local_api/tests/test_tasks.py`** 或测试文件
   - 补测试（见下面测试计划）

### 不修改
- AppleSyncAdapter 业务逻辑
- sync_state_service
- state_transition_validator
- sync_eligibility
- sync_service
- 数据库模型
- celery/daemon

## 测试计划

所有测试通过 FastAPI TestClient 或直接调用 service 层。

1. **completed 后 Calendar 清理**: 
   - 创建任务 + apple_calendar sync_state (synced, 有 external_id)
   - POST /complete → 确认 sync_state 变为 disabled
   - remove_event 幂等：事件已清理后再次 complete 不报错

2. **cancelled 后 Calendar 清理**:
   - 创建任务 + apple_calendar sync_state (synced, 有 external_id)
   - PATCH status=cancelled → 确认 sync_state 变为 disabled

3. **无 sync_state 时无害**:
   - completed 或 cancelled 时没有对应 sync_state → 不报错

4. **apple_reminder 不受影响**:
   - apple_reminder sync_state 在 task completed 后保持原状态

5. **全量 pytest 通过**

## 禁止
- 不写 Reminders
- 不写 WeChat
- 不引入 ORM/连接池
- 不改 AppleSyncAdapter
- 不改 state_transition_validator
- 不改 sync_service
- 不改数据库 schema
