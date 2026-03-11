"""
nanoclaw_tools.py — NanoClaw group registration and mount management tools.

DB: ~/nanoclaw/store/messages.db (registered_groups table)
Allowlist: ~/.config/nanoclaw/mount-allowlist.json

IMPORTANT: After any write operation, caller must restart nanoclaw service
           via restart_service_tool("nanoclaw") for changes to take effect.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / "nanoclaw/store/messages.db"
_ALLOWLIST_PATH = Path.home() / ".config/nanoclaw/mount-allowlist.json"
_GROUPS_DIR = Path.home() / "nanoclaw/groups"


def _write_chat_config(folder: str, jid: str) -> None:
    """Write chat_config.json to group workspace so Claude Code in the container
    knows which Telegram chat_id to use for progress notifications.

    JID format: tg:<chat_id>  →  chat_id extracted by stripping "tg:" prefix.
    """
    try:
        chat_id = jid.removeprefix("tg:")
        group_dir = _GROUPS_DIR / folder
        group_dir.mkdir(parents=True, exist_ok=True)
        (group_dir / "chat_config.json").write_text(
            json.dumps({"telegram_chat_id": chat_id}, indent=2)
        )
    except Exception as e:
        logger.warning("[nanoclaw_tools] Failed to write chat_config for %s: %s", folder, e)


def _load_allowlist() -> dict:
    if _ALLOWLIST_PATH.exists():
        try:
            return json.loads(_ALLOWLIST_PATH.read_text())
        except Exception:
            pass
    return {"allowedRoots": [], "blockedPatterns": [], "nonMainReadOnly": True}


def _save_allowlist(data: dict) -> None:
    _ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ALLOWLIST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _resolve_group_jid(group_identifier: str) -> tuple[str, str] | tuple[None, str]:
    """Resolve a group name or JID to (jid, name).

    Accepts either a JID (starts with 'tg:') or a display name / folder name.
    Returns (jid, name) on success, (None, error_message) on failure.

    This is the standard resolver for all tools that reference NanoClaw groups.
    Any tool accepting a group identifier should call this instead of requiring
    the caller to supply a raw JID.
    """
    identifier = group_identifier.strip()
    if not identifier:
        return None, "group_identifier 不能为空"
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            # Exact JID match
            if identifier.startswith("tg:"):
                row = conn.execute(
                    "SELECT jid, name FROM registered_groups WHERE jid = ?", (identifier,)
                ).fetchone()
                if row:
                    return row[0], row[1]
                return None, f"未找到 JID: {identifier}"
            # Name or folder match (case-insensitive)
            row = conn.execute(
                "SELECT jid, name FROM registered_groups WHERE name = ? OR folder = ? COLLATE NOCASE",
                (identifier, identifier),
            ).fetchone()
            if row:
                return row[0], row[1]
            # Partial name match as fallback
            row = conn.execute(
                "SELECT jid, name FROM registered_groups WHERE name LIKE ? OR folder LIKE ?",
                (f"%{identifier}%", f"%{identifier}%"),
            ).fetchone()
            if row:
                return row[0], row[1]
            return None, f"未找到群组: '{identifier}'，可用 /nano groups 查看列表"
        finally:
            conn.close()
    except Exception as e:
        return None, f"DB 查询失败: {e}"


@tool
def nanoclaw_manage_mount(
    operation: str,
    path: str,
    group: str = "",
    container_path: str = "",
    readonly: bool = False,
) -> str:
    """管理 NanoClaw 挂载点。

    operation: add | remove
    path: 宿主机绝对路径（例如 /Users/lijunsheng/ai-supervisor）
    group: 群组名称、folder 或 JID，工具自动解析——无需调用方预先查询 JID
           例如: "🦠NanoClaw"、"telegram_nanoclaw"、"tg:-5054076671"
           留空则只更新 allowlist，不更新 DB
    container_path: 容器内路径名（留空则取 path 最后一级目录名）
    readonly: 是否只读（默认 False，挂载目录默认可写以支持 Claude Code 修改）

    注意：操作完成后必须调用 restart_service_tool("nanoclaw") 使配置生效。
    """
    host_path = str(Path(path).expanduser().resolve())
    c_path = container_path.strip() or Path(host_path).name
    allow_rw = not readonly

    # --- Update allowlist ---
    data = _load_allowlist()
    roots: list[dict] = data.setdefault("allowedRoots", [])
    existing_idx = next(
        (i for i, r in enumerate(roots) if r.get("path") == host_path), None
    )

    if operation == "add":
        entry = {"path": host_path, "allowReadWrite": allow_rw}
        if existing_idx is not None:
            roots[existing_idx] = entry
            allowlist_action = f"已更新 allowlist 条目: {host_path}"
        else:
            roots.append(entry)
            allowlist_action = f"已添加到 allowlist: {host_path}"
        _save_allowlist(data)
    elif operation == "remove":
        if existing_idx is not None:
            roots.pop(existing_idx)
            _save_allowlist(data)
            allowlist_action = f"已从 allowlist 移除: {host_path}"
        else:
            allowlist_action = f"allowlist 中未找到: {host_path}（无需修改）"
    else:
        return f"❌ 未知操作: {operation}，可选 add | remove"

    results = [allowlist_action]

    # --- Resolve group identifier → JID, then update DB ---
    if group.strip():
        jid, name_or_err = _resolve_group_jid(group)
        if jid is None:
            results.append(f"⚠️ 无法解析群组 '{group}': {name_or_err}，allowlist 已更新但跳过 DB")
        else:
            try:
                conn = sqlite3.connect(str(_DB_PATH))
                try:
                    row = conn.execute(
                        "SELECT container_config FROM registered_groups WHERE jid = ?", (jid,)
                    ).fetchone()
                    try:
                        cfg = json.loads(row[0]) if row[0] else {}
                    except Exception:
                        cfg = {}
                    mounts: list[dict] = cfg.setdefault("additionalMounts", [])

                    if operation == "add":
                        mounts = [m for m in mounts if m.get("hostPath") != host_path]
                        mounts.append({
                            "hostPath": host_path,
                            "containerPath": c_path,
                            "readonly": readonly,
                        })
                        cfg["additionalMounts"] = mounts
                        folder_row = conn.execute(
                            "SELECT folder FROM registered_groups WHERE jid = ?", (jid,)
                        ).fetchone()
                        conn.execute(
                            "UPDATE registered_groups SET container_config = ? WHERE jid = ?",
                            (json.dumps(cfg), jid),
                        )
                        conn.commit()
                        if folder_row:
                            _write_chat_config(folder_row[0], jid)
                        results.append(
                            f"已更新群组 '{name_or_err}' ({jid}) 的挂载: "
                            f"{host_path} → /workspace/extra/{c_path}"
                        )
                    elif operation == "remove":
                        cfg["additionalMounts"] = [
                            m for m in mounts if m.get("hostPath") != host_path
                        ]
                        conn.execute(
                            "UPDATE registered_groups SET container_config = ? WHERE jid = ?",
                            (json.dumps(cfg), jid),
                        )
                        conn.commit()
                        results.append(
                            f"已从群组 '{name_or_err}' ({jid}) 移除挂载: {host_path}"
                        )
                finally:
                    conn.close()
            except Exception as e:
                results.append(f"❌ DB 操作失败: {e}")

    results.append("⚠️ 请调用 restart_service_tool('nanoclaw') 使配置生效")
    return "\n".join(results)


@tool
def nanoclaw_register_group(
    jid: str,
    name: str,
    folder: str,
    trigger: str = ".*",
    mounts_json: str = "",
    requires_trigger: bool = False,
) -> str:
    """注册新 NanoClaw 群组到数据库。

    jid: 群组唯一 ID（例如 tg:-100123456789 或 tg:7783067080）
    name: 群组显示名称
    folder: 存储目录名，格式 ^[a-z0-9][a-z0-9_-]*$（例如 telegram_mygroup）
    trigger: 触发正则，默认 .* 匹配所有消息
    mounts_json: 额外挂载 JSON 数组，例如
                 '[{"hostPath":"/Users/lijunsheng/ai-supervisor","containerPath":"ai-supervisor","readonly":false}]'
                 留空则无额外挂载
    requires_trigger: 是否必须触发词才响应（默认 False）

    注意：注册完成后必须调用 restart_service_tool("nanoclaw") 使配置生效。
    """
    jid = jid.strip()
    name = name.strip()
    folder = folder.strip()

    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', folder):
        return f"❌ folder 格式错误: '{folder}'，必须匹配 ^[a-z0-9][a-z0-9_-]*$"

    container_config: dict = {}
    if mounts_json.strip():
        try:
            mounts = json.loads(mounts_json)
            if not isinstance(mounts, list):
                return "❌ mounts_json 必须是 JSON 数组"
            container_config["additionalMounts"] = mounts
        except json.JSONDecodeError as e:
            return f"❌ mounts_json JSON 解析失败: {e}"

    added_at = datetime.now(timezone.utc).isoformat()
    cfg_str = json.dumps(container_config) if container_config else None

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            conn.execute(
                """INSERT INTO registered_groups
                   (jid, name, folder, trigger_pattern, added_at, container_config, requires_trigger, is_main)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                (jid, name, folder, trigger, added_at, cfg_str, 1 if requires_trigger else 0),
            )
            conn.commit()
        finally:
            conn.close()
        _write_chat_config(folder, jid)
    except sqlite3.IntegrityError as e:
        err = str(e)
        if "UNIQUE constraint failed: registered_groups.jid" in err:
            return f"❌ JID {jid} 已存在，请用 nanoclaw_manage_mount 更新现有群组"
        if "UNIQUE constraint failed: registered_groups.folder" in err:
            return f"❌ folder '{folder}' 已被占用，请选择其他名称"
        return f"❌ 数据库约束错误: {e}"
    except Exception as e:
        return f"❌ 注册失败: {e}"

    mount_info = ""
    if container_config.get("additionalMounts"):
        mount_info = f"\n挂载: {len(container_config['additionalMounts'])} 个额外路径"
    result = (
        f"✅ 已注册群组:\n"
        f"  JID: {jid}\n"
        f"  名称: {name}\n"
        f"  目录: {folder}\n"
        f"  触发: {trigger}{mount_info}\n"
        f"⚠️ 请调用 restart_service_tool('nanoclaw') 使配置生效"
    )
    return result
