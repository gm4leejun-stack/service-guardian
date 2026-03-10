# ai-supervisor 项目规范

## 项目信息
- **服务名**: `com.ai-supervisor`
- **重启命令**: `launchctl stop com.ai-supervisor && sleep 2 && launchctl start com.ai-supervisor`
- **GitHub**: https://github.com/gm4leejun-stack/service-guardian (private)
- **主分支**: master

## 修改代码后必须执行

每次修改代码后，**必须**按顺序完成以下步骤：

1. **提交到 GitHub**
   ```bash
   git add <修改的文件>
   git commit -m "<简洁描述改动内容>"
   git push origin master
   ```

2. **重启服务**
   ```bash
   launchctl stop com.ai-supervisor && sleep 2 && launchctl start com.ai-supervisor
   ```

3. **验证服务恢复**
   ```bash
   launchctl list com.ai-supervisor | grep PID
   ```

以上三步缺一不可，不得跳过。

## 代码修改原则

1. **解决同类问题**：每次修改必须能处理同类问题，而不只是修这一次
2. **符合服务定义**：智能、高效、成本可控、可移植、可自愈
3. **最小改动**：只改动必要的内容，不引入无关变更
