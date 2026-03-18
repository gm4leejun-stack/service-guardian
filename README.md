# 龙虾医生（ai-supervisor）

Mac 上的本地 DevOps Agent，通过 Telegram 管理本机的各项服务。

## 架构

```
Telegram (@jun_xiao_code_bot)
    ↓
bot/telegram_bot.py
    ├── 快速路径（零 LLM）
    │     /sysinfo → psutil 直接返回
    │     /nano groups → sqlite3 直接查询
    │
    └── 智能路径
          ↓
        agent/brain.py
          → claude --print（Claude Sonnet，全 Mac 权限）
          → CLAUDE.md 作为系统提示
          → 最近 5 条对话历史注入，保持上下文连续性

Watchdog（后台线程，每 60s）
    → OpenClaw: 日志过时 + Telegram pending > 0 才告警
    → NanoClaw: 进程停止才告警
    → Smart Triage: Haiku 确认是否真实故障
    → 静默时段 00:00–08:00，60min 冷却防止告警风暴

Mac Exec Bridge（localhost:18800）
    → NanoClaw 容器通过 host.docker.internal 访问
    → 让容器内的 Claude Code 执行 Mac 命令（launchctl 等）
    → Bearer token 认证，仅绑定 127.0.0.1
```

## 安装（一条命令）

### 1. 克隆项目

```bash
git clone https://github.com/gm4leejun-stack/service-guardian.git ~/ai-supervisor
```

### 2. 运行安装脚本

```bash
bash ~/ai-supervisor/install.sh
```

脚本会自动：创建 venv、安装依赖、引导配置、注册开机自启。

- 已安装 Claude Code：只需填 **Telegram Bot Token**
- 未安装 Claude Code：还需填 **Anthropic API Key**

### 3. 配置 Watchdog 告警接收人

安装完成后，向你的 Bot 发送 `/myid`，把返回的 chat_id 填入 `.env`：

```
ADMIN_CHAT_ID=<你的chat_id>
```

然后重启生效：

```bash
launchctl stop com.ai-supervisor && launchctl start com.ai-supervisor
```

验证：`launchctl list com.ai-supervisor | grep PID`

---

## NanoClaw 群接入 ai-supervisor

新建 NanoClaw 群后，发给 **@jun_xiao_code_bot**：

> 帮我把 ~/ai-supervisor 挂载到 [群名] 这个群

ai-supervisor 会自动完成挂载 + 重启 NanoClaw。挂载后：

- 群内 Claude Code 可读写 ai-supervisor 代码
- 群内 Claude Code 通过 `mac_exec_cli.py` 执行 Mac 命令（launchctl、ps 等）
- `CLAUDE.md` 自动加载，Claude Code 自动检测执行环境并适配

---

## .env 配置项

| 变量 | 说明 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | @BotFather 申请的 Bot Token |
| `ANTHROPIC_API_KEY` | API Key |
| `ANTHROPIC_BASE_URL` | API 代理地址 |
| `HAIKU_MODEL` | Watchdog Smart Triage 用的廉价模型 |
| `ADMIN_CHAT_ID` | Watchdog 告警发送的 Telegram 用户 ID |
| `EXEC_BRIDGE_TOKEN` | Mac Exec Bridge 的认证 Token（NanoClaw 容器用）|
| `EXEC_BRIDGE_PORT` | Mac Exec Bridge 端口，默认 18800 |

---

## Telegram 命令

| 命令 | 说明 |
|------|------|
| `/sysinfo` | CPU / 内存 / 磁盘 + 服务状态（零延迟）|
| `/status` | 服务状态（通过 Agent）|
| `/fix openclaw\|nanoclaw\|all` | 重启服务 |
| `/logs [errors\|tmp\|summary\|search]` | 查看日志 |
| `/run <命令>` | 执行 Shell 命令 |
| `/claude <任务>` | 调用 Claude Code |
| `/nano groups` | 列出 NanoClaw 群组（零延迟）|
| `/nano mount add\|remove <路径> <JID>` | 管理挂载 |
| `/nano register <JID> <name> <folder>` | 注册新群组 |
| `/scaffold <路径> <repo_url>` | 克隆项目并安装依赖 |

也可以直接用中文描述，例如：
- "OpenClaw 没回复了，帮我检查一下"
- "把 ~/ai-supervisor 挂载到 telegram_nanoclaw 群组"

---

## 项目结构

```
ai-supervisor/
├── .env                      # 密钥（不在 git 中，需备份）
├── main.py                   # 入口：bot + watchdog + exec bridge
├── watchdog.py               # 服务巡检 + 自动救援
├── CLAUDE.md                 # Claude Code 系统提示（执行规则）
├── config/settings.py        # 所有配置项
├── agent/
│   └── brain.py              # claude --print 调用 + 对话历史
├── bot/
│   └── telegram_bot.py       # Telegram Bot 处理器
└── tools/
    ├── service_tools.py       # check_service, restart_service
    ├── log_tools.py           # read_logs, search_logs
    ├── shell_tools.py         # run_shell_command
    ├── notify_tools.py        # notify_user（Telegram 推送）
    ├── notify_cli.py          # CLI 通知工具（Claude Code 调用）
    ├── exec_bridge.py         # Mac Exec Bridge HTTP 服务器
    ├── mac_exec_cli.py        # NanoClaw 容器调用的桥接客户端
    ├── system_tools.py        # system_status（/sysinfo 快速路径）
    └── nanoclaw_tools.py      # NanoClaw 群组和挂载管理
```
