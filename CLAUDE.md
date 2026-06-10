# VidBrain — Claude Code 项目配置

## 归档协议

### Pitfall 日志归档

当我说"执行 Pitfall 归档"或 Session 积累 5+ 个工程挑战时，执行：

```bash
bash scripts/archive_pitfalls.sh
git add docs/pitfall-problems*.log && git commit -m "docs: archive pitfall log"
```

脚本会将 `docs/pitfall-problems.log` 重命名为 `docs/pitfall-problems-{timestamp}.log`，并初始化含表头的空白日志。

### Eval 报告归档

EVAL 跑完后或我说"归档 eval 报告"时，执行：

```bash
bash scripts/archive_eval_report.sh
git add docs/eval-report*.log && git commit -m "docs: archive eval report"
```

## 关键约束

- 输入目录 `I:/web-videos` 只读，永不可修改
- `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` 只从系统环境变量读取
- 每个 Bash 调用是独立进程，不保持 cwd——始终用绝对路径或先 `cd`
- `git add` 使用精确文件路径，禁止 `git add -A`
