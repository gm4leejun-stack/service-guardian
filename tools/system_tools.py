"""
system_tools.py — System status and project scaffolding tools.

_system_status_impl() is a plain function (no LangChain wrapper) that can be
called directly by the bot for zero-LLM /sysinfo fast path.
system_status is the @tool wrapper for Agent use.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import psutil
from langchain_core.tools import tool

from tools.service_tools import _get_status

logger = logging.getLogger(__name__)


def _system_status_impl() -> str:
    """Plain function — call directly from bot for zero-LLM fast path."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    openclaw = _get_status("openclaw")
    nanoclaw = _get_status("nanoclaw")

    def _svc_line(key: str, st: dict) -> str:
        icon = "✅" if st["running"] else "❌"
        pid = f"PID={st['pid']}" if st.get("pid") else "未运行"
        return f"{icon} {key}: {st['status']} ({pid})"

    return (
        f"系统状态 (lijunshengdeMac-mini)\n"
        f"CPU: {cpu:.1f}% | "
        f"内存: {mem.percent:.1f}% ({mem.used // 1024**3}G/{mem.total // 1024**3}G) | "
        f"磁盘: {disk.percent:.1f}% ({disk.free // 1024**3}G free)\n\n"
        f"{_svc_line('openclaw', openclaw)}\n"
        f"{_svc_line('nanoclaw', nanoclaw)}"
    )


@tool
def system_status() -> str:
    """一键系统状态：CPU / 内存 / 磁盘使用率 + 所有服务健康状态。"""
    return _system_status_impl()


@tool
def project_scaffold(action: str, path: str, repo_url: str = "", install: bool = True) -> str:
    """项目脚手架工具。

    action: clone（需 repo_url）| init（仅创建目录）
    path: 目标路径（绝对路径或 ~ 开头）
    repo_url: git 仓库 URL（action=clone 时必填）
    install: 克隆后自动安装依赖（检测 package.json → npm install，requirements.txt → pip install），默认 True

    注意：适合简单的 clone + 安装流程。复杂项目初始化请用 fix_with_claude。
    """
    target = Path(path).expanduser().resolve()

    if action == "init":
        target.mkdir(parents=True, exist_ok=True)
        return f"✅ 目录已创建: {target}"

    if action != "clone":
        return f"❌ 未知操作: {action}，可选 clone | init"

    if not repo_url.strip():
        return "❌ action=clone 时必须提供 repo_url"

    results: list[str] = []

    # git clone
    try:
        r = subprocess.run(
            ["git", "clone", repo_url.strip(), str(target)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return f"❌ git clone 失败:\n{r.stderr.strip()}"
        results.append(f"✅ git clone 完成: {target}")
    except subprocess.TimeoutExpired:
        return "❌ git clone 超时（120s）"
    except Exception as e:
        return f"❌ git clone 错误: {e}"

    if not install:
        return "\n".join(results)

    # Auto-detect and install deps
    pkg_json = target / "package.json"
    req_txt = target / "requirements.txt"

    if pkg_json.exists():
        try:
            r = subprocess.run(
                ["npm", "install"],
                capture_output=True, text=True, timeout=180, cwd=str(target),
            )
            if r.returncode == 0:
                results.append("✅ npm install 完成")
            else:
                results.append(f"⚠️ npm install 失败:\n{r.stderr.strip()[:300]}")
        except subprocess.TimeoutExpired:
            results.append("⚠️ npm install 超时（180s）")
        except Exception as e:
            results.append(f"⚠️ npm install 错误: {e}")

    if req_txt.exists():
        # Prefer venv pip if available
        venv_pip = target / "venv/bin/pip"
        pip_cmd = str(venv_pip) if venv_pip.exists() else "pip3"
        try:
            r = subprocess.run(
                [pip_cmd, "install", "-r", "requirements.txt"],
                capture_output=True, text=True, timeout=180, cwd=str(target),
            )
            if r.returncode == 0:
                results.append("✅ pip install 完成")
            else:
                results.append(f"⚠️ pip install 失败:\n{r.stderr.strip()[:300]}")
        except subprocess.TimeoutExpired:
            results.append("⚠️ pip install 超时（180s）")
        except Exception as e:
            results.append(f"⚠️ pip install 错误: {e}")

    if not (pkg_json.exists() or req_txt.exists()):
        results.append("ℹ️ 未检测到 package.json 或 requirements.txt，跳过依赖安装")

    return "\n".join(results)
