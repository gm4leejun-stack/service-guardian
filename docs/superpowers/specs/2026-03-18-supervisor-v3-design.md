# ai-supervisor v3 升级设计文档

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ai-supervisor 从"两个服务的看门狗"升级为"Mac mini 智能电脑管家"，具备通用服务管理、任务感知上下文记忆和 Token 可观测性。

**Architecture:** 三步交付（每步独立可上线）。Step 1 为本次实施范围：配置驱动服务监控 + 任务感知上下文系统 + Token 可观测性 + 清理死代码。Step 2/3 留待后续迭代。

**Tech Stack:** Python 3.14, python-telegram-bot v20+, anthropic SDK, asyncio

---

## 背景与目标

### 当前问题

1. **通用性差**：Watchdog 硬编码只认识 OpenClaw 和 NanoClaw，无法动态扩展
2. **上下文失忆**：工作记忆仅保留最近 5 轮，重启即丢失，跨任务上下文污染
3. **Token 黑盒**：无法感知每次调用的 token 消耗，无法优化
4. **死代码积累**：`agent/prompts.py` 在 v2 已不使用，v1 残留配置项未清理

### 升级路线

```
Step 1（本次）: 配置驱动 + 上下文系统 + Token 监控 + 清理死代码
Step 2（下次）: 主动资源巡检（CPU/内存/磁盘/网络）
Step 3（再下次）: CLAUDE.md 精简 + docs/runbooks/ 知识库
```

---

## Step 1 详细设计

### 1. 配置驱动服务监控

#### 新增：`config/watchlist.json`

完整字段表（与 `watchdog.py` 现有数据结构对齐）：

```json
{
  "services": [
    {
      "key": "openclaw",
      "label": "ai.openclaw.gateway",
      "description": "OpenClaw Gateway",
      "log": "~/.openclaw/logs/gateway.log",
      "freeze_check": "process_down",
      "runbook": "docs/runbooks/openclaw.md"
    },
    {
      "key": "nanoclaw",
      "label": "com.nanoclaw",
      "description": "NanoClaw",
      "log": "~/nanoclaw/logs/nanoclaw.log",
      "freeze_check": "process_down",
      "bot_token_env": "NANOCLAW_BOT_TOKEN",
      "runbook": "docs/runbooks/nanoclaw.md"
    }
  ]
}
```

**字段说明：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `key` | ✅ | 内部标识符，用于冷却期追踪和日志 |
| `label` | ✅ | launchctl 服务名 |
| `description` | ✅ | 显示名称 |
| `log` | ✅ | 日志文件路径（支持 `~` 展开） |
| `freeze_check` | ✅ | `"process_down"`（检测进程存活）或 `"log-stale"`（检测日志时间戳） |
| `bot_token_env` | ❌ | 从 `.env` 读取对应 Telegram bot token 的环境变量名，用于 pending 检查 |
| `runbook` | ❌ | **Step 1 中忽略，不读取**；Step 3 实施时 Agent 按需加载 |

**设计原则：**
- `freeze_check: "process_down"` — 只检测进程是否存活，不依赖日志时间戳
- `log` 路径 `~` 在加载时通过 `os.path.expanduser()` 展开
- `bot_token_env` 而非直接存 token，避免 token 明文出现在配置文件（git 追踪的文件）
- 用户通过 Telegram 说"帮我监控 Nginx"→ Agent 写入此文件 → 下次巡检自动覆盖

#### 改造：`watchdog.py`

- 启动时读取 `config/watchlist.json`，替换 `SERVICES_TO_WATCH` 常量
- `label` 字段作为 launchctl 服务名传入 `get_service_status(svc["label"])` 和 `restart_service(svc["label"])`，替代原来通过 `key` 在 settings.py 中查找服务名的间接映射
- `key` 字段继续用于冷却期追踪（`_in_cooldown(svc["key"])`）和日志标识
- 从 `bot_token_env` 字段动态读取 bot token：`os.environ.get(svc["bot_token_env"])`
- 配置热更新：每轮巡检前重新读取文件，文件变动无需重启服务
- `_check_service(service_config)` 接口不变，直接传配置字典

#### 清理死代码

- **删除** `agent/prompts.py`（v2 brain.py 不导入）
- **删除** `config/settings.py` 中的 v1 残留：
  - `LANGGRAPH_RECURSION_LIMIT`
  - `WATCHDOG_FREEZE_THRESHOLD`（已被 process_down 模式替代，不再使用）

---

### 2. 任务感知上下文系统

#### 设计原则

上下文边界是**任务**，不是**时间**。问题解决后，旧上下文不应污染新任务。

#### 两层记忆架构

**工作记忆（`agent/brain.py` 内存）**

```python
# 数据结构
working_memory: dict[str, list[tuple[str, str]]] = {}
# key = thread_id (str(chat_id))
# value = [(user_msg, assistant_response), ...]  完整保留，不截断

MAX_TASK_TURNS = 20  # 安全上限，防止用户不主动清空时 token 耗尽
```

- 作用域：当前任务，任务结束后清空
- 注入方式：每次调用前拼接为纯文本 context 注入 Claude
- 安全上限：20 轮（约对应 `--max-budget-usd 1.00` 的 token 预算上限）；达到上限时通知用户"当前任务上下文已达上限，建议发送 /new 开始新任务"

**长期记忆（`agent/memory.json` 磁盘持久化）**

```json
[
  {
    "time": "2026-03-18T01:42:34+08:00",
    "thread_id": "7783067080",
    "service": "openclaw",
    "summary": "模型配置错误(claude-3-5 不存在)，已修改 openclaw.json 为 claude-sonnet-4-6，重启后恢复正常。"
  }
]
```

- 上限：保留最近 50 条，超出自动删除最旧的
- 注入：每次新任务开始，注入最近 10 条记录（按 thread_id 匹配）
- 写入时机：工作记忆清空时，用 Haiku 生成 1~3 句话摘要写入

#### 清空触发规则

**仅信任用户，不信任 Claude 的自我判断（Claude 经常误判"已解决"）**

```python
# 独立消息匹配（去除首尾空白后完全等于关键词，避免对话中间误触发）
RESET_EXACT = {"好了", "解决了", "没问题了", "换个话题", "/new"}

# 宽松包含匹配（明确表达结束意图的短语）
RESET_CONTAINS = ["问题解决了", "已经解决了", "好的谢谢", "完成了"]

def should_clear_working_memory(user_msg: str) -> bool:
    msg = user_msg.strip()
    if msg in RESET_EXACT:
        return True
    return any(kw in msg for kw in RESET_CONTAINS)
```

**不触发清空**：Claude 输出含"已解决/已修复"等 → 忽略（不可信）

**清空流程：**
```
用户触发清空
  → 调用 Haiku 生成本轮摘要（1~3句）
  → 写入 agent/memory.json
  → 清空 working_memory[thread_id]
  → 回复"✅ 上下文已清除，开始新任务"
```

---

### 3. Token 可观测性

#### 3.1 每次响应追加 Token 统计

每条 Telegram 消息末尾自动追加：

```
[⬆️ 34.2K  ⬇️ 128]
```

- `⬆️` = 总 input tokens = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`
- `⬇️` = `output_tokens`
- 数字 ≥ 1000 显示 `K`（保留一位小数），< 1000 显示原始数字

**数据来源：** `brain.py` 解析 `--output-format stream-json` 的 `usage` 字段，随 response 文本一起返回给 `telegram_bot.py`。

#### 3.2 `/input` 指令：上次 Input 分析

**重要说明：** `brain.py` 通过 `claude --print` 子进程调用 API，无法获取 Claude Code 内部的精确组件分解（工具定义、内部系统提示等由子进程自行构造）。因此组件分解为**近似估算**，仅供参考；总量和缓存数据为精确值。

输出格式：

```
📥 上次 Input 分析
🕐 2026-03-18 01:42:34 CST
🤖 claude-sonnet-4-6

─────────────────
≈ 对话历史:   8.3K
≈ CLAUDE.md:   405
≈ 提示注入:   1.4K
≈ 其余(工具等): 24.2K
─────────────────
⬆️ Input 合计: 34.2K  ← 精确
  ├ 新鲜处理:   3
  ├ 缓存命中:   0
  └ 缓存写入: 34.2K
⬇️ Output:     128
```

**计算方案：**

```python
# brain.py 在注入前通过字符长度估算各组件 token 数
# 经验值：中英混合约 2.5~3 chars/token
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)

history_tokens   = estimate_tokens(history_text)
claude_md_tokens = estimate_tokens(claude_md_content)
hint_tokens      = estimate_tokens(notify_hint)

# 精确值来自 API
total_input = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
other_tokens = total_input - history_tokens - claude_md_tokens - hint_tokens
# other_tokens 可能为负（估算误差），显示时取 max(0, other_tokens)
```

组件数字标注 `≈`，总量（合计、缓存三项、output）不标注（精确）。

**存储：** `last_usage: dict[str, UsageRecord]`，key = thread_id，每次调用后更新。`/input` 零 LLM 直接读取。

---

### 4. 新增 Telegram 指令

| 指令 | 类型 | 行为 |
|------|------|------|
| `/new` | 零 LLM | 清空当前工作记忆 → 生成摘要写入长期记忆 → 回复"✅ 上下文已清除" |
| `/input` | 零 LLM | 读取 `last_usage[chat_id]` → 格式化输出分析 |

同步更新：
- `/help` 文本加入 `/new`、`/input` 说明
- `set_my_commands()` 注册新指令

---

## 文件变更清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `config/watchlist.json` | 服务监控配置，替代 watchdog.py 中的硬编码 |
| `agent/memory.json` | 长期记忆持久化（初始为空数组 `[]`，加入 .gitignore） |

### 修改文件
| 文件 | 改动要点 |
|------|------|
| `watchdog.py` | 读取 watchlist.json；热更新；bot_token 从 env 变量读取 |
| `agent/brain.py` | 工作记忆（任务作用域，MAX_TASK_TURNS=20）+ 长期记忆注入 + usage 解析 + 清空逻辑 |
| `bot/telegram_bot.py` | 追加 token 统计 + `/new` 指令 + `/input` 指令 + 更新 `/help` + set_my_commands |
| `config/settings.py` | 删除 LANGGRAPH_RECURSION_LIMIT、WATCHDOG_FREEZE_THRESHOLD 等 v1 残留 |

### 删除文件
| 文件 | 原因 |
|------|------|
| `agent/prompts.py` | v2 架构死代码，brain.py 不导入 |

### .gitignore 新增
```
agent/memory.json   # 含用户对话摘要，不入版本库
agent/sessions.json # 已有未追踪文件，确认忽略
```

---

## 不在本次范围内（Step 2/3）

- 系统资源主动巡检（CPU/内存/磁盘/网络）
- CLAUDE.md 精简重构
- `docs/runbooks/` 专项知识库建立

---

## 测试要点

1. **watchlist.json 热更新**：运行时新增服务配置，下一轮巡检（60s 内）自动识别，无需重启
2. **工作记忆清空**：发"好了"后下一条消息不带旧历史；发无关消息不触发清空
3. **MAX_TASK_TURNS**：连续发 21 条消息不触发清空，第 21 条时收到上限提示
4. **长期记忆持久化**：清空后重启服务，`/input` 仍能展示历史摘要条目数
5. **Token 统计**：`[⬆️ X ⬇️ Y]` 出现在每条非系统消息末尾
6. **`/input`**：组件有 `≈` 标注；合计、缓存三项无 `≈`；可在 `/input` 后立即查看
7. **`/new`**：回复"✅ 上下文已清除"，下一条自然语言消息无旧上下文
8. **死代码清理**：删除 `agent/prompts.py` 后 `python main.py --bot` 启动无报错
9. **watchlist bot_token**：nanoclaw 的 Telegram pending 检查从 env 变量正常读取
