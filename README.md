# Life Sync Hub

**个人日程提醒中枢系统**

一个基于 FastAPI 的本地日程同步引擎，支持 Apple Calendar（macOS）、WeChat 通知，以及多平台定时同步触发。

## 项目结构

```
local_api/
├── adapters/          # 平台适配器（Apple Calendar, Weather 等）
├── docs/              # 文档和阶段归档
├── notify/            # 通知通道（WeChat）
├── reports/           # 项目阶段报告归档
├── routers/           # FastAPI 路由
├── scheduler/         # 同步调度器
├── scripts/           # 工具脚本和模板
├── services/          # 业务服务层
├── sync_client/       # 同步客户端
├── test_data/         # 测试数据库 fixtures
├── tests/             # 测试套件
├── config.py          # 配置
├── database.py        # 数据库初始化
├── main.py            # FastAPI 入口
├── middleware.py       # 中间件
├── models.py          # 数据模型
├── preflight.py       # 环境预检
├── sync_engine.py     # 同步引擎核心
├── sync_models.py     # 同步数据模型
└── validators.py      # 验证器
```

## 快速开始

```bash
# 安装依赖
pip install -r local_api/requirements.txt

# macOS: 安装 Apple Calendar 依赖
pip install pyobjc-framework-EventKit

# 运行预检
python -m local_api.preflight

# 运行测试
pytest local_api/tests/ -v

# 启动 API 服务
uvicorn local_api.main:app --reload
```

## 当前阶段

- 当前分支：`phase-6-sync-engine`
- 最新提交：`8b5e35118` — test(local_api): add real environment preflight checks
- 测试通过数：457 passed
- 当前阶段：**Phase 18-Mac** — Apple Calendar 最小真实写入验收（需 macOS 环境）

## 环境要求

- **Apple Calendar 同步**：仅 macOS (EventKit + TCC 权限)
- **WeChat 通知**：需配置 `WECHAT_REMINDER_ENABLED=true` 及 App ID/Secret
- **其他功能**：跨平台可用

## 安全说明

- 所有同步操作默认使用 `dry_run` / `test_mode`
- 测试数据必须带 `[SYNC-TEST]` 前缀
- 禁止批量写入、禁止写 Reminders、禁止启动同步 daemon（除非明确授权）
- 环境预检 (`preflight.py`) 不做任何真实外部调用
