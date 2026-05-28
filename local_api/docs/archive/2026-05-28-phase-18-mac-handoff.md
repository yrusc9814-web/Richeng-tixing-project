# Phase 18-Mac Handoff: Apple Calendar 最小真实写入验收

> **创建日期**: 2026-05-28  
> **创建环境**: WSL2 (Linux) on Windows  
> **目标平台**: macOS (Apple Silicon / Intel)  
> **GitHub 仓库**: https://github.com/yrusc9814-web/life-sync-hub.git  
> **分支**: `phase-6-sync-engine`

---

## 1. 当前状态

Phase 18 在 **WSL2/Linux** 环境下已暂停（**不是代码失败**）。

| 检查项 | 结果 | 原因 |
|--------|------|------|
| preflight readiness | `not_ready` | `apple_platform=FAIL`：sys.platform = linux |
| Apple EventKit 依赖 | SKIP | EventKit 仅在 macOS 上可用 |
| Apple Calendar 权限 | SKIP | TCC 权限仅在 macOS 上可获取 |
| Apple test_mode | PASS | AppleSyncAdapter 已就绪 |
| 测试数据标记 | PASS | `[SYNC-TEST]` 前缀已确认 |
| 回滚/清理规则 | PASS | 文档已就绪 |
| WeChat 配置 | FAIL | `WECHAT_REMINDER_ENABLED` 未设置（不影响 Apple Calendar） |

**结论**: Codespaces / WSL2 / Linux 不能完成真实 Apple Calendar 验收。  
必须由 **macOS 环境** 接手继续。

---

## 2. macOS 接手步骤

### 2.1 克隆仓库

```bash
git clone https://github.com/yrusc9814-web/life-sync-hub.git
cd life-sync-hub
git checkout phase-6-sync-engine
```

### 2.2 安装依赖

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 项目依赖
pip install -r local_api/requirements.txt

# Apple Calendar 依赖（仅 macOS）
pip install pyobjc-framework-EventKit
```

### 2.3 授予 Calendar 权限

**System Settings → Privacy & Security → Calendars**  
确保终端（Terminal）或 IDE 有 Calendar 读写权限。

### 2.4 运行预检

```bash
python -m local_api.preflight
```

**必须满足**: `readiness = "ready"`（所有 Apple 检查项均为 PASS）

如果 preflight 不是 ready：
- 停止，不要尝试写入 Apple Calendar
- 检查依赖安装和 TCC 权限
- 可能需要重新打开终端让权限生效

### 2.5 执行最小写入验收

创建 1 条 `[SYNC-TEST]` 测试事件：

```bash
# 使用 AppleSyncAdapter 的 test_mode
python -c "
from local_api.adapters.apple_adapter import AppleSyncAdapter
adapter = AppleSyncAdapter()
result = adapter.create_event(
    title='[SYNC-TEST] Phase 18 verification',
    notes='Created by Hermes Phase 18-Mac handoff test',
    start_date=...,    # 当前时间附近
    end_date=...,      # 当前时间+15分钟
    test_mode=True     # 必须启用
)
print(f'event_id: {result.event_id}')
print(f'external_id: {result.external_id}')
"
```

**规则**:
- 标题**必须带** `[SYNC-TEST]` 前缀
- **必须**启用 test_mode / dry-run safety
- **只允许创建 1 条**
- **必须记录** external_id / event_id，便于后续清理

### 2.6 验证事件存在

通过 Apple Calendar app 或程序化查询确认事件已创建。

### 2.7 清理

```bash
adapter.delete_event(event_id=..., external_id=...)
```

**规则**:
- **只清理**本次创建的 `[SYNC-TEST]` 测试事件
- **不得**删除用户真实日历事件

### 2.8 验证已清理

再次查询确认该事件不存在。

---

## 3. 禁止事项

| 操作 | 禁止原因 |
|------|---------|
| ❌ 批量写入 Calendar | 违反最小写入原则 |
| ❌ 写 Reminders | 不在本阶段范围内 |
| ❌ 启动同步 daemon / LaunchAgent | 仅验收写入能力 |
| ❌ 删除非 `[SYNC-TEST]` 数据 | 可能损坏用户真实数据 |
| ❌ 修改业务代码 | 此阶段只验收，不开发 |
| ❌ 提交 secrets / .env | 安全风险 |

---

## 4. 已知预检状态（参考）

来自 WSL2 环境的 preflight 输出（供 macOS 对比参考）：

```json
{
  "readiness": "not_ready",
  "summary": "2 check(s) failed: apple_platform, wechat_config",
  "checks": [
    {"name": "apple_platform", "status": "fail", "detail": "Not on macOS. Current platform: linux"},
    {"name": "apple_eventkit_deps", "status": "skip", "detail": "Not on macOS — EventKit is not available."},
    {"name": "apple_calendar_permission", "status": "skip", "detail": "Not on macOS — Calendar permission is not applicable."},
    {"name": "apple_reminder_permission", "status": "skip", "detail": "Not on macOS — Reminders permission is not applicable."},
    {"name": "apple_dry_run_available", "status": "pass", "detail": "AppleSyncAdapter supports dry_run=True flag."},
    {"name": "apple_test_mode_available", "status": "pass", "detail": "AppleSyncAdapter supports test_mode=True flag with [SYNC-TEST] prefix."},
    {"name": "wechat_config", "status": "fail", "detail": "WECHAT_REMINDER_ENABLED is not set."},
    {"name": "test_data_marking_rules", "status": "pass", "detail": "Apple adapter marks test data with prefix '[SYNC-TEST] '."},
    {"name": "rollback_cleanup_rules", "status": "pass", "detail": "Rollback/cleanup rules are documented."}
  ]
}
```

**macOS 上期望**:
- `apple_platform` → PASS
- `apple_eventkit_deps` → PASS (需安装 pyobjc-framework-EventKit)
- `apple_calendar_permission` → PASS (需授予 TCC 权限)
- `apple_dry_run_available` → PASS
- `apple_test_mode_available` → PASS
- `wechat_config` → WARN 或 FAIL（不影响 Apple Calendar 验收）
- 其余保持不变

---

## 5. 关键文件索引

| 文件 | 说明 |
|------|------|
| `local_api/preflight.py` | 环境预检脚本（Phase 17） |
| `local_api/adapters/apple_adapter.py` | Apple Calendar 适配器（含 test_mode、dry_run、[SYNC-TEST] 前缀） |
| `local_api/tests/test_preflight.py` | 预检测试（457 tests total） |
| `local_api/docs/archive/2026-05-28-phase-17-real-preflight.md` | Phase 17 归档 |
| `local_api/tests/test_adapters.py` | 适配器测试 |

---

## 6. 风险 / 遗留问题

| 风险 | 说明 |
|------|------|
| 🟡 TCC 权限弹窗 | macOS 首次调用 EventKit 会弹出权限请求，需要用户手动授权 |
| 🟡 PyObjC 版本兼容 | 不同 macOS 版本可能需要特定 PyObjC 版本 |
| 🟡 时间参数 | Calendar 事件需要正确的 datetime 格式 |
| 🟢 test_mode 保护 | AppleSyncAdapter 的 test_mode 会输出日志但不会写入真实 Calendar（实际写入了，通过前缀标记） |
| 🟢 文档完整 | Phase 15-18 归档、预检脚本、回滚规则均已就绪 |

---

## 7. 下一步目标

**Phase 18-Mac**: Apple Calendar 最小真实写入验收

完成后建议进行:
1. 输出 Phase 18-Mac 闭环报告
2. 归档 Phase 18 文档到 `docs/archive/`
3. 准备 Phase 19（Apple Calendar 功能扩展 / WeChat 通知集成 / 定时同步）

---

*本交接文档由 WSL2 Hermes 生成，供 macOS Hermes 接手参考。*
*任何 Phase 18-Mac 的问题应在此文档基础上向 macOS Hermes 提出。*
