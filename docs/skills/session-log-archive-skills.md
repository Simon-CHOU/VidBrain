---
name: Session Log Archive Skills
description: Automated archiving for pitfall-problems.log and eval-report.log with timestamped rename and fresh template initialization
type: feedback
originSessionId: 72bd2365-9b51-4987-989a-45a12282f0cd
---

# Session Log Archive Skills

## Pitfall Log Archive

When the user says "执行 Pitfall 归档" or "archive pitfalls", or when a session has accumulated 5+ significant engineering challenges, run:

```bash
cd F:/ML/bailian-playground/embedding-research && bash scripts/archive_pitfalls.sh
```

Then commit:
```bash
git add docs/pitfall-problems*.log && git commit -m "docs: archive pitfall log and initialize fresh session log"
```

## Eval Report Archive

When the user says "归档 eval 报告" or when an eval run completes, run:

```bash
cd F:/ML/bailian-playground/embedding-research && bash scripts/archive_eval_report.sh
```

Then commit:
```bash
git add docs/eval-report*.log && git commit -m "docs: archive eval report and initialize fresh template"
```

## Trigger conditions

- When a major debugging cycle concludes
- When the user explicitly asks
- When the session is ending and there are documented issues/results
- When pitfall log has 5+ entries since last archive
- When an eval run completes with results worth preserving
