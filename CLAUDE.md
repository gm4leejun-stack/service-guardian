# ai-supervisor 项目规范

## 项目信息
- **服务名**: `com.ai-supervisor`
- **重启命令**: `launchctl stop com.ai-supervisor && sleep 2 && launchctl start com.ai-supervisor`
- **GitHub**: https://github.com/gm4leejun-stack/service-guardian (private)
- **主分支**: master

## 修改代码后必须执行

**重启和验证**：每次修改代码后必须执行：
```bash
launchctl stop com.ai-supervisor && sleep 2 && launchctl start com.ai-supervisor
launchctl list com.ai-supervisor | grep PID
```

**提交 GitHub**：以下情况才提交，不是每次文件改动都提交：
- 完成一个完整的 bug 修复
- 完成一个完整的新功能
- 用户明确要求提交
- 一次对话结束前，若有未提交的有意义改动

```bash
git add <修改的文件>
git commit -m "<简洁描述改动内容>"
git push origin master
```

## 行为准则

**你是执行者，不是顾问。** 收到问题描述后直接执行，不得询问"要我现在改吗？"、"需要我帮你处理吗？"。
- ✅ 正确：读代码 → 找根因 → 修改 → 重启 → 验证 → 汇报结果
- ❌ 错误：分析完问题后问用户是否要修

## 代码修改原则

**评估每次改动前先问这两个问题：**

1. **解决同类问题**：这次改动能不能处理下次出现的同类问题？
   - ✅ 改规则/逻辑，让系统自动处理这类情况
   - ❌ 只处理这一个具体实例（hardcode、特殊 case）

2. **符合服务定义**：改动是否让系统更智能、高效、成本可控、可移植、可自愈？
   - ✅ 减少人工干预、减少 token 消耗、增加自动恢复能力
   - ❌ 增加复杂度却没有带来上述收益

3. **最小改动**：只改动必要的内容，不引入无关变更
