# VidBrain Manual

This document keeps the detailed operating guide so that `README.md` can stay short and copy-paste friendly.

## Install

```powershell
cd VidBrain
uv sync
```

Required Windows environment variables:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`

Optional environment variables:

- `DASHSCOPE_API_KEY` for `--embedding`
- `WHISPER_CLI_PATH` for `--asr-backend vulkan`

## Recommended Default

If you only want one command to remember, use:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --profile auto --interval 30m
```

This gives you:

- long-lived background processing
- auto-throttling while the desktop is in use
- automatic resume from `pipeline.db`
- simple, stable behavior for day-to-day use

## Common Commands

Run one batch and exit:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --once
```

Run in lower-impact desktop-friendly mode:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --profile active --interval 30m
```

Run in maximum-throughput mode:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --profile idle --once
```

Run in streaming mode without waiting for intervals:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --profile auto --continuous
```

Process at most 10 videos:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --once --limit 10
```

Use Vulkan ASR when `whisper.cpp` is available:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --asr-backend vulkan --profile idle --once
```

Classify files only:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --classify-only
```

Retry failed tasks and continue processing:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --retry-failed --once
```

Run vault refinement:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --refine
```

Run semi-auto mode with review gates:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --semi
```

Review classifications only:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --review-classifications
```

Review drafts only:

```powershell
uv run python -m src.main --vault-dir ./vidbrain_vault --review-drafts
```

## Start And Stop

Graceful stop:

- press `Ctrl+C` once
- VidBrain waits for in-flight work to finish
- final metrics and audit artifacts are flushed before exit

Check whether the daemon is running:

```powershell
Test-Path logs/vidbrain.pid
Get-Content logs/vidbrain.pid
Get-Process -Id (Get-Content logs/vidbrain.pid)
```

Notes:

- only one instance is allowed at a time
- a killed process is recovered on next launch by resetting stuck tasks to `PENDING`

## Operating Modes

`--once`

- processes one batch and exits
- useful for testing or bounded runs

`--interval 30m`

- starts long-lived timed processing
- waits for the interval between batch runs
- good default for stable unattended execution

`--continuous`

- processes the next task immediately without waiting for intervals
- better for backlog draining or near-real-time ingestion

`--profile auto`

- switches between low-impact and full-power behavior based on desktop idle state

`--profile active`

- fixed low-power mode

`--profile idle`

- fixed full-power mode

## CLI Reference

### Core Paths

| Flag | Default | Description |
|------|---------|-------------|
| `--input-dir` | `I:\web-videos` | Read-only input folder that contains `.mp4` videos. |
| `--vault-dir` | `./vidbrain_vault` | Obsidian vault output directory. |
| `--db-path` | `./pipeline.db` | SQLite state database path. |

### Processing

| Flag | Default | Description |
|------|---------|-------------|
| `--model-size` | `tiny` | Whisper model size. |
| `--cpu-threads` | `0` | Use logical cores minus one when `0`. |
| `--batch-size` | `5` | Videos to process per batch. |
| `--limit` | `0` | Maximum total videos to process. `0` means unlimited. |
| `--parallel` | `0` | Number of videos to process concurrently. |
| `--video-cooldown` | `0` | Cooldown seconds after each video or batch. |
| `--asr-backend` | `cpu` | `cpu` or `vulkan`. |

### Remote ASR

| Flag | Default | Description |
|------|---------|-------------|
| `--role` | `primary` | `primary` or `worker`. |
| `--remote-asr-host` | `""` | Remote ASR worker hostname or IP (empty disables remote worker). |
| `--remote-asr-port` | `8080` | Remote ASR worker port. |
| `--remote-asr-timeout` | `2.0` | Remote ASR connection and health check timeout in seconds. |
| `--remote-asr-health-interval` | `10` | Remote ASR health probe interval in seconds. |
| `--remote-asr-failure-threshold` | `2` | Consecutive failures before marking the remote worker offline. |
| `--remote-asr-recovery-threshold` | `2` | Consecutive recoveries before marking the remote worker online. |
| `--remote-asr-cooldown` | `60` | Remote ASR circuit breaker cooldown in seconds. |

### Scheduling

| Flag | Default | Description |
|------|---------|-------------|
| `--once` | off | Process one batch and exit. |
| `--interval` | `30m` | Timed processing interval such as `5m`, `2h`, or `3600`. |
| `--continuous` | off | Keep pulling the next task immediately without waiting. |
| `--profile` | `auto` | `auto`, `active`, or `idle`. |

### Maintenance And Review

| Flag | Default | Description |
|------|---------|-------------|
| `--classify-only` | off | Only classify files by filename. |
| `--retry-failed` | off | Reset retryable failed tasks back to `PENDING`. |
| `--refine` | off | Run vault refinement. |
| `--semi` | off | Enable semi-auto review flow. |
| `--review-classifications` | off | Enter classification review mode only. |
| `--review-drafts` | off | Enter draft review mode only. |

### Auto-Refine

| Flag | Default | Description |
|------|---------|-------------|
| `--auto-refine-after` | `0` | Run refinement after every N batches. |
| `--auto-refine-every` | empty | Run refinement every N hours such as `24h`. |
| `--embedding` | off | Enable embedding-based search and clustering. |

### Observability

| Flag | Default | Description |
|------|---------|-------------|
| `--metrics-interval` | `3600` | Flush metrics snapshot interval in seconds. |
| `--metrics-export-dir` | `reports` | Directory for metrics and optional audit exports. |
| `--audit-export` | off | Export audit JSON during shutdown. |

### Process Priority

| Flag | Default | Description |
|------|---------|-------------|
| `--priority` | `normal` | `normal`, `below_normal`, or `idle`. Usually managed by `--profile`. |

## Data And Safety

- VidBrain never creates, edits, renames, or deletes files under `--input-dir`.
- Processed source videos remain in place and are skipped by DB state on later runs.
- Markdown output is written only to `--vault-dir`.
- Secrets are read from environment variables and should not be stored in project files.
