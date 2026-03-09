#!/bin/bash
# update.sh — pull latest code from GitHub and restart ai-supervisor
#
# Designed to be called by the Agent via run_shell_command.
# Prints progress so the Agent can relay it to the user via notify_user.
# Restart is delayed 5 seconds to give the Agent time to send the final
# Telegram notification before the process is killed.

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "📦 当前版本：$(git log -1 --format='%h %s' 2>/dev/null || echo '未知')"
echo "🔄 正在拉取最新代码..."

git fetch origin master
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "✅ 已是最新版本，无需更新"
    exit 0
fi

CHANGES=$(git log --oneline HEAD..origin/master 2>/dev/null)
git pull origin master
echo "📋 更新内容："
echo "$CHANGES"

echo "📦 安装依赖..."
venv/bin/pip install -r requirements.txt -q

echo "✅ 代码更新完成，5 秒后重启服务..."
(sleep 5 && launchctl stop com.ai-supervisor && sleep 2 && launchctl start com.ai-supervisor) &
echo "🔄 重启已计划，服务将在约 10 秒后恢复"
