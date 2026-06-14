# LifeSync 用户日常使用 SOP 与故障恢复手册

本文档为 LifeSync 系统的普通用户日常操作与常见故障排除指南。请严格按照指引操作，以确保后台日程同步的稳定运行。

## ⚠️ 核心红线（普通用户切勿操作）
1. **不推荐**直接运行底层的 Python CLI 命令（如 `create_task.py`）。
2. **不要**随意关闭后台同步链路（LaunchAgent）。
3. **不要**手动修改本地的 SQLite 数据库（`tasks.db`）。
4. **不要**尝试重新编译或重签（re-sign）`LifeSyncCalendarHelper.app`。
5. **不要**在 macOS 设置中随意移除或重置 Calendar（日历）隐私权限。

---

## 1. 日常日程任务创建 SOP

### 1.1 最新推荐入口
日常请**始终使用**桌面交互式快捷入口：
- **路径**：`~/Desktop/创建日程任务-交互式.command`
- **用法**：在桌面直接双击该图标，终端将弹出一个安全的交互式向导。

### 1.2 创建流程指引
双击打开向导后，根据提示依次填写：
1. **任务标题**：必填，不能为空（例如 `整理明天日程`）。
2. **开始时间**：默认提供当前时间（ISO 格式）。直接回车使用默认值，或手动修改（例如：`2026-06-13T10:00:00Z`）。
3. **结束时间**：默认提供一小时后时间。规则同上。
4. **是否立即同步**：
   - 输入 `Y`（默认）：任务落库后立刻调起同步引擎，马上在 Apple Calendar 看到结果。
   - 输入 `N`：仅将任务保存到本地数据库，系统将在 5 分钟内由后台自动同步。

### 1.3 取消与防呆机制
- **如何取消**：在任何需要输入的环节，直接输入字母 `q` 并回车，流程即刻安全终止，不会生成脏数据。
- **输入错误处理**：标题为空、时间格式不合法、或结束早于开始时间，均会收到明确的中文报错并等待您重新输入或退出，不会引发程序崩溃。

### 1.4 如何判断是否成功
- **创建成功**：终端会打印绿色的 `✅ 任务已成功创建！` 并显示 `任务 ID (task_id)`。
- **同步成功**：如果您选择了立即同步 `Y`，终端底部会显示 `最终同步状态: synced` 以及一长串 `日历外部 ID (external_id)`，并提示 `✅ 同步成功！`。此时打开 Apple Calendar 即可看到该日程。

---

## 2. 系统健康检查与日志

### 2.1 如何检查后台是否正常
当您怀疑后台同步卡住时，可以通过 `sync_doctor` 工具获取只读健康报告：
```bash
cd /Users/vantawork/Projects/life-sync-hub
./.venv/bin/python -m local_api.scripts.sync_doctor
```
该命令将返回 JSON 格式的报告，重点关注：
- `main_agent.loaded`: 是否为 true
- `alert_agent.loaded`: 是否为 true
- `preflight_ready`: 是否为 true
- `helper_auth`: 是否为 authorized

### 2.2 后台服务丢失如何恢复
如果 `main_agent.loaded` 或 `alert_agent.loaded` 显示为 `false`，说明后台服务被意外关闭。请在终端执行以下命令重新加载：
```bash
cd /Users/vantawork/Projects/life-sync-hub
# 重新加载主同步服务
launchctl load -w ~/Library/LaunchAgents/com.vanta.lifesync.background-sync.plist
# 重新加载监控服务
launchctl load -w ~/Library/LaunchAgents/com.vanta.lifesync.alert-check.plist
```

### 2.3 如何查看详细日志
所有日志存储在 `local_api/logs/` 目录下：
```bash
# 查看后台主要同步日志
tail -n 50 local_api/logs/background-sync.log

# 查看健康监控探针日志
tail -n 20 local_api/logs/sync-alert-check.log
```

---

## 3. 常见故障恢复手册

### 3.1 桌面入口丢失时如何恢复
如果您不小心删除了桌面的 `创建日程任务-交互式.command`：
1. 打开终端。
2. 运行修复脚本重新安装：
   ```bash
   cd /Users/vantawork/Projects/life-sync-hub
   ./scripts/install-desktop-shortcut.sh
   ```

### 3.2 出现时间格式错误怎么处理
- **表现**：终端提示 `❌ 错误: 时间格式错误。必须为 ISO 格式...`。
- **解决**：确保手动输入的时间字符串完整包含中间的大写 `T` 和末尾的大写 `Z`，例如 `2026-06-13T10:00:00Z`。

### 3.3 出现 Calendar 权限异常怎么处理
- **表现**：健康检查显示 `helper_auth: notDetermined` 或 `denied`，任务无法推送到 Apple Calendar。
- **解决**：
  1. 打开 macOS 的**系统设置** -> **隐私与安全性** -> **日历**。
  2. 找到 `LifeSyncCalendarHelper`，确保其开关已打开。
  3. 若列表中没有，或者仍无法同步，请切勿私自重签。请执行以下命令打开日历助手界面：
     ```bash
     open /Users/vantawork/Projects/life-sync-hub/tools/calendar-helper/LifeSyncCalendarHelper.app
     ```
  4. 在弹出的 App 界面中点击 **"Request Calendar Access"** 按钮，当屏幕弹出“是否允许访问日历”时，务必点击“允许”。

### 3.4 出现中文乱码或脚本打不开怎么处理
- **表现**：双击 `.command` 文件提示没有权限，或终端显示一堆乱码。
- **解决**：
  1. **权限丢失**：在终端重新赋予可执行权限：
     ```bash
     chmod +x ~/Desktop/创建日程任务-交互式.command
     ```
  2. **终端乱码**：确保您的 macOS Terminal（终端）的字符编码设置为 `UTF-8`（终端偏好设置 -> 描述文件 -> 高级 -> 字符编码选择 Unicode (UTF-8)）。
