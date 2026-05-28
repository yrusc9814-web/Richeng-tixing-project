# Sync Trigger 定时运行说明

Phase 12 — sync trigger 的定时调度方案。提供 macOS LaunchAgent、Linux cron、Windows 定时任务三套模板，不实际安装。

---

## 快速概览

| 平台 | 模板文件 | 触发方式 |
|------|---------|---------|
| macOS | `scripts/sync_trigger.plist.template` | LaunchAgent (StartInterval) |
| Linux | `scripts/sync_trigger.crontab.template` | cron |
| Windows | `scripts/sync_trigger_schtask.bat.template` | 任务计划程序 (schtasks) |

核心命令（所有平台通用）：

```bash
python -m local_api.scripts.sync_trigger sync-pending --limit 10
```

---

## 模板占位符

所有模板包含以下占位符，使用前必须替换：

| 占位符 | 说明 | 示例 |
|--------|------|------|
| `<PROJECT_ROOT>` | hermes-agent 仓库根路径 | `/Users/alice/hermes-agent`<br>`D:\hermes-agent` |
| `<PYTHON>` | Python 解释器路径 | `/usr/local/bin/python3`<br>`D:\hermes-agent\.venv\Scripts\python.exe` |
| `<USERNAME>` | Windows 用户名（仅 schtasks） | `Alice` |

**推荐使用 venv 内的 python**，确保依赖一致：

```bash
# macOS/Linux
<PROJECT_ROOT>/.venv/bin/python3

# Windows
<PROJECT_ROOT>\.venv\Scripts\python.exe
```

---

## macOS LaunchAgent

### 文件

`local_api/scripts/sync_trigger.plist.template`

### 模板摘要

- Label: `com.localapi.sync-trigger`
- 命令: `python -m local_api.scripts.sync_trigger sync-pending --limit 10`
- 间隔: 每 300 秒（5 分钟）
- 日志: `local_api/logs/sync_trigger_out.log` / `sync_trigger_err.log`

### 变量

| 变量 | 说明 |
|------|------|
| `StartInterval` | 300（5 分钟），可替换为 `StartCalendarInterval` |
| `KeepAlive` | false — 不自动重启 |
| `ExitTimeOut` | 120 秒 — 超时强制终止 |
| `RunAtLoad` | false — 加载时不立即执行 |
| `Nice` | 10 — 低优先级后台任务 |

### 安装命令（文档参考，不执行）

```bash
# 1. 复制并编辑模板
cp local_api/scripts/sync_trigger.plist.template ~/Library/LaunchAgents/com.localapi.sync-trigger.plist

# 2. 编辑 plist，替换 <PROJECT_ROOT> 和 <PYTHON>

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.localapi.sync-trigger.plist

# 4. 验证
launchctl list | grep com.localapi.sync-trigger
```

### 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.localapi.sync-trigger.plist
rm ~/Library/LaunchAgents/com.localapi.sync-trigger.plist
```

### 查看日志

```bash
tail -f <PROJECT_ROOT>/local_api/logs/sync_trigger_out.log
tail -f <PROJECT_ROOT>/local_api/logs/sync_trigger_err.log
```

---

## Linux cron

### 文件

`local_api/scripts/sync_trigger.crontab.template`

### 模板选项

```cron
# 每 5 分钟（推荐生产环境）
*/5 * * * * <PYTHON> -m local_api.scripts.sync_trigger sync-pending --limit 10 >> <PROJECT_ROOT>/local_api/logs/sync_trigger_cron.log 2>&1

# 每 15 分钟（保守）
# */15 * * * * <PYTHON> -m local_api.scripts.sync_trigger sync-pending --limit 10 >> <PROJECT_ROOT>/local_api/logs/sync_trigger_cron.log 2>&1

# 每天 9:00 AM
# 0 9 * * * <PYTHON> -m local_api.scripts.sync_trigger sync-pending --limit 10 >> <PROJECT_ROOT>/local_api/logs/sync_trigger_cron.log 2>&1

# Dry-run 模式（每 30 分钟，仅验证，无外部同步）
# */30 * * * * <PYTHON> -m local_api.scripts.sync_trigger sync-pending --limit 10 --dry-run >> <PROJECT_ROOT>/local_api/logs/sync_trigger_dryrun.log 2>&1
```

### 安装命令（文档参考，不执行）

```bash
# 1. 编辑模板文件，替换占位符
vi local_api/scripts/sync_trigger.crontab.template
# 取消注释你需要的行，注释掉不需要的

# 2. 加载到 crontab
crontab local_api/scripts/sync_trigger.crontab.template

# 3. 验证
crontab -l
```

---

## Windows 任务计划程序

### 文件

`local_api/scripts/sync_trigger_schtask.bat.template`

### 创建命令（文档参考，不执行）

```cmd
REM 每 5 分钟触发
schtasks /create /tn "LocalAPI Sync Trigger" ^
  /tr "<PYTHON> -m local_api.scripts.sync_trigger sync-pending --limit 10" ^
  /sc minute /mo 5 ^
  /ru <USERNAME> ^
  /f

REM Dry-run 模式（每 30 分钟）
schtasks /create /tn "LocalAPI Sync Trigger (dry-run)" ^
  /tr "<PYTHON> -m local_api.scripts.sync_trigger sync-pending --limit 10 --dry-run" ^
  /sc minute /mo 30 ^
  /ru <USERNAME> ^
  /f
```

### 管理命令

```cmd
schtasks /query /tn "LocalAPI Sync Trigger"       REM 查看
schtasks /delete /tn "LocalAPI Sync Trigger" /f    REM 删除
```

---

## Dry-Run 验证

上线前的验证步骤（仅作为文档参考）：

```bash
# 1. Dry-run 单次
python -m local_api.scripts.sync_trigger sync-pending --limit 10 --dry-run

# 2. 检查输出 — 应返回 JSON 无错误，no external sync

# 3. Dry-run 特定任务
python -m local_api.scripts.sync_trigger sync-task <task-id> --dry-run

# 4. 确认日志可写入
echo "test" >> <PROJECT_ROOT>/local_api/logs/sync_trigger_dryrun.log
```

---

## 日志位置

所有日志统一输出到 `local_api/logs/`：

| 日志文件 | 来源 |
|---------|------|
| `sync_trigger_out.log` | LaunchAgent stdout |
| `sync_trigger_err.log` | LaunchAgent stderr |
| `sync_trigger_cron.log` | cron stdout + stderr |
| `sync_trigger_dryrun.log` | cron dry-run 模式 |
| `sync_trigger_schtask.log` | Windows 任务计划程序 |

---

## 注意事项

1. **不实际安装** — 本文档仅供参考。安装操作由运维人员按需执行。
2. **Python 路径** — 优先使用项目 venv 内的 Python，避免环境差异。
3. **并发控制** — sync_trigger 本身不锁；若 SyncScheduler 内部无去重，需注意并发安全。
4. **日志轮转** — 未配置 logrotate。长期运行后需手动清理或接入 logrotate。
5. **超时保护** — LaunchAgent `ExitTimeOut: 120`；cron 无内置超时，需使用 `timeout` 命令包裹。
