# 龙虾医生经验库设计文档

> **Goal:** 为龙虾医生建立跨机器共享的经验知识库，让每次解决问题的经验得以沉淀传承，下次遇到同类问题时直接参考，越用越聪明。

---

## 背景与目标

### 当前问题

每次解决问题花费的诊断经验全部丢失，下次遇到同类问题（本机或其他机器）需要从头排查，浪费大量 token 和时间。典型案例：OpenClaw 2026.3.x 版本升级后权限策略变化，Agent 花费大量 token 无法定位，直到用户手动提供文档才解决。

### 目标

1. 自动提取每次成功解决的经验，写入结构化知识库
2. 支持用户主动录入外部文档、版本变更说明等知识
3. 处理新问题时优先检索经验库，找到就参考，找不到才自主探索
4. 通过 GitHub 跨机器同步，所有安装了龙虾医生的机器共享同一套经验

---

## 架构总览

```
用户说"好了"(≥3轮对话)          用户说"/remember <内容>"
        ↓                                  ↓
   Haiku 自动提取                    Haiku 结构化录入
   (只取最终有效解法)                (直接存用户提供的知识)
        ↓                                  ↓
        └──────────── 写入 ───────────────┘
                          ↓
              agent/knowledge_cache.json (本地缓存)
                          ↓
              GitHub API PUT → knowledge 分支
              (失败→本地队列→下次重试)

新问题进来
    ↓
检索 knowledge_cache.json (关键词+tag匹配)
    ↓
找到 → "📚 参考历史经验：[摘要]" + Agent 内部参考完整内容
找不到 → Agent 自主探索
```

---

## 数据模型

每条经验条目结构：

```json
{
  "id": "uuid4",
  "time": "2026-03-24T01:38:00+08:00",
  "machine": "lijunshengdeMac-mini",
  "source": "auto",
  "tags": ["openclaw", "permission", "upgrade"],
  "affected_versions": ["2026.3.x"],
  "symptoms": ["Bot拒绝调用工具", "提示没有权限执行此操作"],
  "root_cause": "2026.3.2版本默认权限策略收紧，默认仅允许对话",
  "solution": "openclaw config set tools.profile full && openclaw gateway restart"
}
```

**字段说明：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | ✅ | UUID4，用于删除操作 |
| `time` | ✅ | ISO8601 带时区 |
| `machine` | ✅ | 来自 `settings.MACHINE_NAME`，便于溯源 |
| `source` | ✅ | `"auto"`（用户说"好了"触发）或 `"manual"`（`/remember` 录入）|
| `tags` | ✅ | Haiku 生成，3~6个关键词，用于检索匹配 |
| `affected_versions` | ❌ | 有版本依赖时填写，无则空数组 |
| `symptoms` | ✅ | 现象描述，检索时匹配用户的问题描述 |
| `root_cause` | ✅ | 根本原因，一句话 |
| `solution` | ✅ | 完整可执行的解决方案，保留具体命令，去除个人路径/IP |

**Haiku 提取约束（prompt 强制要求）：**
- 只记录最终被用户确认有效的解法
- 所有失败的尝试一律排除
- 去除路径中的用户名、IP 地址、凭据、hostname
- 保留：具体命令、版本号、配置键名、报错信息关键字

---

## 写入路径

### 路径1：自动提取（对话驱动）

**触发条件：**
- 用户消息命中 `RESET_EXACT` / `RESET_CONTAINS`（现有清空机制）
- 且当前任务对话轮数 ≥ 3（过滤平凡问题）

**流程：**
```
用户说"好了"
  → 判断轮数 ≥ 3
  → 调用 Haiku：从对话历史提取结构化经验条目（source="auto"）
  → Haiku 判断是否有保存价值（平凡解法如"直接重启"不存）
  → 有价值 → 追加到本地 knowledge_cache.json
  → 异步写入 GitHub（不阻塞用户响应）
  → 继续现有清空流程（生成 memory.json 摘要、清空工作记忆）
```

### 路径2：手动录入（用户主动）

**触发方式：**
- Telegram 发送 `/remember <内容>`
- 或自然语言："帮我记住这个"、"把这个经验存下来"

**流程：**
```
用户提供内容（外部文档/手动总结）
  → 调用 Haiku：将原始内容结构化为经验条目（source="manual"）
  → 追加到本地 knowledge_cache.json
  → 异步写入 GitHub
  → 回复"✅ 经验已记录：[root_cause 一句话摘要]"
```

---

## 检索与注入

**触发时机：** 每次新任务开始（working memory 为空时的第一条消息）

**检索逻辑：**
```python
def search_knowledge(query: str, top_k: int = 3) -> list[dict]:
    keywords = extract_keywords(query)  # 简单分词，中英文
    results = []
    for entry in knowledge_cache:
        score = 0
        text = " ".join([*entry["tags"], *entry["symptoms"], entry["root_cause"]])
        for kw in keywords:
            if kw in text:
                score += 1
        if score > 0:
            results.append((score, entry))
    return [e for _, e in sorted(results, reverse=True)[:top_k]]
```

**注入方式：** 在任务 prompt 前追加：
```
[经验库参考]
1. 症状：Bot拒绝调用工具，提示没有权限
   根因：OpenClaw 2026.3.x 权限策略收紧
   解法：openclaw config set tools.profile full && openclaw gateway restart
```

**用户可见展示（Telegram 回复开头）：**
```
📚 参考了 1 条历史经验（OpenClaw权限策略变更）
```

---

## 跨机器同步（GitHub API）

**存储位置：** 同 repo 的 `knowledge` 分支，文件路径 `knowledge_base.json`

**读取（启动时）：**
```python
# GET https://api.github.com/repos/{owner}/{repo}/contents/knowledge_base.json?ref=knowledge
# 本地缓存到 agent/knowledge_cache.json，TTL=1小时
# 失败→使用本地缓存，不阻塞启动
```

**写入（异步，非阻塞）：**
```python
# 1. GET 获取当前文件内容和 sha
# 2. 追加新条目
# 3. PUT 提交（携带 sha 防冲突）
# 4. 409 冲突 → 重新 GET 拿新 sha → 重试（最多3次）
# 5. 最终失败 → 写入本地待同步队列 agent/knowledge_pending.json
#    下次写入时先处理队列
```

**配置：** `.env` 新增 `GITHUB_TOKEN`（可选，不填则只本地存储，不跨机同步）

---

## Telegram 新增指令

| 指令 | 类型 | 行为 |
|------|------|------|
| `/remember <内容>` | 慢路径（Haiku） | 手动录入经验，回复确认摘要 |
| `/knowledge` | 零 LLM | 列出最近 10 条经验（id + root_cause 一行） |
| `/knowledge delete <id>` | 零 LLM | 删除指定经验（本地+异步同步 GitHub） |

---

## 文件变更清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `agent/knowledge.py` | 核心逻辑：读/写/检索/GitHub同步 |
| `agent/knowledge_cache.json` | 本地缓存（加入 .gitignore） |
| `agent/knowledge_pending.json` | 待同步队列（加入 .gitignore） |

### 修改文件
| 文件 | 改动要点 |
|------|------|
| `agent/brain.py` | 任务开始时检索注入；用户说"好了"时触发自动提取（piggyback 现有清空逻辑） |
| `bot/telegram_bot.py` | 新增 `/remember`、`/knowledge`、`/knowledge delete` 指令 |
| `config/settings.py` | 新增 `GITHUB_TOKEN`（可选） |
| `install.sh` | 新增可选的 GITHUB_TOKEN 引导问题 |
| `CLAUDE.md` | 说明 `/remember` 用法 |

### .gitignore 新增
```
agent/knowledge_cache.json
agent/knowledge_pending.json
```

---

## 测试要点

1. **自动提取触发**：对话 ≥ 3 轮说"好了"→ 经验写入；< 3 轮说"好了"→ 不写入
2. **手动录入**：`/remember` 后收到确认摘要；`/knowledge` 列出新条目
3. **检索命中**：发包含经验关键词的问题→ 回复开头出现 📚 提示
4. **检索未命中**：不相关问题→ 无 📚 提示，Agent 正常探索
5. **GitHub 同步**：有 GITHUB_TOKEN → knowledge 分支有新文件；无 token → 本地正常运行
6. **冲突处理**：模拟 PUT 409 → 自动重试，条目不丢
7. **无 GITHUB_TOKEN 降级**：正常本地写入，无报错
8. **删除功能**：`/knowledge delete <id>` → 本地删除 + GitHub 异步同步

