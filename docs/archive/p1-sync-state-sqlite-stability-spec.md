# P1 Stability Tightening

只改 3 件事，不动范围外的代码。

## 1. 状态机旁路收口

当前 `update_sync_state()` 已经拒绝 `sync_status` 更新（第 192-197 行抛出 ValueError），PATCH 端点也使用 `transition_sync_state()` 走验证器。

**需要补测试确认以下 invariant 不被破坏：**
- `update_sync_state(sync_id, sync_status="synced")` → ValueError
- `PATCH /api/sync/state/{sync_id}` 带 `sync_status` → 走 `transition_sync_state()`，合法转移成功，非法转移 422
- `update_sync_state(sync_id, external_id="xxx")` → 只更新非状态字段，不报错

## 2. SQLite 稳健性

`busy_timeout=5000` 已经设置 ✅。WAL mode 和 foreign_keys 已经设置 ✅。

**需要检查并补：**
- `sync_engine.py` 的 `_adapter_push_cycle()` 中，一个 sync 的处理涉及多个写（transition + update + log）。**不需要**把它们包成一个事务（单个失败不应该拖累其他 sync），但如果 `update_sync_state` 或 `create_sync_log` 在 `transition_sync_state` 成功后失败，当前代码会记录 warning 但不报错，状态已变但 log 缺失。这个行为可以保留，但要确认日志记录正确。

**需要补的测试：**
- 数据库打开后 `PRAGMA busy_timeout` 正确返回 5000
- `PRAGMA journal_mode` 正确返回 wal
- `PRAGMA foreign_keys` 正确返回 1
- `sync_engine._adapter_push_cycle()` 中 push 失败 → state→failed + log→failed ✅
- `sync_engine._adapter_push_cycle()` 中 push 成功 → state→synced + log→success ✅

## 3. skipped 状态一致性

当前状态已经正确：
- `synced` + `external_id` 时跳过 push（`sync_service.py:150-164`）✅
- 资格检查失败 → `skipped`（`sync_service.py:170-171`）✅
- `skipped` → `in_progress` 不是合法转移（验证器已封锁）✅
- `skipped` → `pending` 是合法手动转移 ✅

**需要补测试：**
- `skipped` → `in_progress` 抛出 ValueError
- `skipped` → `pending` 成功（手动触发）
- `pending` → `in_progress` → push 成功后 → `synced`，不写 success 但状态仍 skipped
- 已经 `synced` + `external_id` 的 sync_state 再次调用 `run_task_sync` → 返回 success=True, status=skipped，不改变 state，不写 sync_log

## 限制

禁止：
- 不动 AppleSyncAdapter
- 不动 state_transition_validator 的转移规则
- 不动 sync_eligibility
- 不动数据库 schema
- 不引入 ORM / 连接池
- 不写 Reminders
- 不写 WeChat
- 不启动 daemon

## 文件范围

修改：
- `local_api/tests/test_state_transitions.py`（加测试）
- `local_api/tests/test_sync_service.py`（加测试）
- `local_api/tests/test_sync_api.py`（加测试）
- `local_api/tests/test_sync_engine.py`（加测试）
- `local_api/database.py`（仅补 `busy_timeout` 测试专用 helper，不改变业务逻辑）
- `local_api/sync_engine.py`（仅补异常路径的 rollback，不改变主流程）

## 验证

完成后：
```
python -m pytest local_api/tests/ -q --tb=short
```
必须全部通过。

## 提交

```
fix: tighten sync state transitions and sqlite resilience
```
