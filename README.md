# VidBrain

A self-hosted pipeline that watches a local folder of `.mp4` tech talk videos, transcribes them with local ASR (faster-whisper), runs them through a DeepSeek-powered LangGraph agent for cleanup / linking / knowledge extraction, and writes structured Markdown notes directly into your Obsidian vault — fully automated.

It runs as a long-lived background daemon with automatic checkpoint-resume, dynamic CPU throttling based on desktop idle state, single-instance enforcement, and optional GPU acceleration via whisper.cpp Vulkan.

## Quickstart

```powershell
# 1. Setup
cd VidBrain
uv sync

# 2. Set env vars (Windows System Environment Variables)
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 3. Run — process one batch then exit
python -m vidbrain.main --vault-dir ./my_vault --once

# 4. Run — continuous daemon (auto-throttle when desktop is active)
python -m vidbrain.main --vault-dir ./my_vault --interval 30m --profile auto
```

## Start / Stop

```powershell
# Start daemon (background, survives Ctrl+C in its own terminal)
python -m vidbrain.main --vault-dir ./my_vault --profile auto --interval 30m

# Graceful stop — press Ctrl+C once, waits for in-flight tasks to finish
# Force stop — press Ctrl+C twice, then reset stuck tasks on next launch (automatic)

# Check if running — only one instance allowed
Test-Path logs/vidbrain.pid                     # PID file exists = running
Get-Content logs/vidbrain.pid                   # shows the PID
Get-Process -Id (Get-Content logs/vidbrain.pid) # process alive?
```

## CLI Reference

### Required
| Flag | Default | Description |
|------|---------|-------------|
| `--vault-dir` | *required* | Obsidian vault path (output directory) |

### Performance profiles (dynamic auto-throttle)
| Flag | Default | Description |
|------|---------|-------------|
| `--profile auto` | ✓ | Auto-switch: `active` (low CPU) when you're using the PC, `idle` (full power) after 5 min of inactivity |
| `--profile active` | | Fixed low-power mode (1 worker, 2 ASR threads, below-normal priority) |
| `--profile idle` | | Fixed full-power mode (2 workers, all cores, normal priority) |

### Processing
| Flag | Default | Description |
|------|---------|-------------|
| `--input-dir` | `I:\web-videos` | Input folder with `.mp4` videos (read-only, never modified) |
| `--model-size` | `tiny` | Whisper model: `tiny` / `base` / `small` / `medium` / `large-v3` |
| `--cpu-threads` | `cpu_count-1` | ASR CPU threads (overridden by `--profile auto`) |
| `--parallel N` | `0` | Process N videos concurrently (overridden by `--profile auto`) |
| `--batch-size` | `5` | Videos per batch |
| `--once` | off | Process one batch and exit |
| `--interval 30m` | off | Continuous mode: wait interval between batches |
| `--limit N` | `0` | Max total videos to process (0 = unlimited) |
| `--video-cooldown` | `0` | Seconds between videos (set by profile in auto mode) |

### Advanced
| Flag | Default | Description |
|------|---------|-------------|
| `--asr-backend cpu` | ✓ | faster-whisper CPU inference |
| `--asr-backend vulkan` | | whisper.cpp with Vulkan GPU (requires compiled `whisper-cli.exe` + GGML model) |
| `--priority` | `normal` | Process priority (overridden by `--profile auto`) |
| `--embedding` | off | Enable semantic search & clustering (needs `DASHSCOPE_API_KEY`) |
| `--db-path` | `./pipeline.db` | SQLite database path |

### Modes
| Flag | Description |
|------|-------------|
| `--classify-only` | Classify files by filename, don't process |
| `--refine` | Run vault refinement (MOC generation, clustering) |
| `--retry-failed` | Reset permanently-failed tasks back to pending |
| `--semi` | Semi-auto mode with human review gates |

### Observability
| Flag | Default | Description |
|------|---------|-------------|
| `--metrics-interval` | `3600` | Metrics snapshot flush interval (seconds) |
| `--metrics-export-dir` | `reports` | Metrics JSON export directory |
| `--audit-export` | off | Export audit log as JSON on shutdown |

## Example Commands

```powershell
# Lightweight background processing (desktop-friendly)
python -m vidbrain.main --vault-dir ./vault --profile active --interval 30m

# Auto mode — goes full-power when you step away
python -m vidbrain.main --vault-dir ./vault --profile auto --interval 30m

# Process exactly 10 videos and stop
python -m vidbrain.main --vault-dir ./vault --once --limit 10

# Max throughput with GPU
python -m vidbrain.main --vault-dir ./vault --asr-backend vulkan --profile idle --once

# Classify only (no processing)
python -m vidbrain.main --vault-dir ./vault --classify-only
```

## How It Works

```
.mp4 file detected
  → classifier (filename keyword match)
  → ASR (faster-whisper, CPU int8)   — result saved to SQLite
  → Agent (DeepSeek LLM): clean text → add [[wikilinks]] → suggest related updates
  → Write .md note to Obsidian vault with YAML front-matter + quality score
  → Auto-update related existing notes with cross-references
```

State is fully tracked in `pipeline.db` (SQLite). A killed process resumes automatically on next launch — stuck intermediate states are reset to pending.

Only one instance can run at a time (PID file lock in `logs/vidbrain.pid`).

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended)
- ~500 MB disk for `tiny` model, ~3 GB for `large-v3`
- Windows (priority throttling, idle detection, Vulkan are Windows-only; falls back gracefully elsewhere)

## Project Structure

```
VidBrain/
├── vidbrain/                  # source
│   ├── main.py               # entry point
│   ├── config.py             # PipelineConfig, LLMConfig
│   ├── pipeline.py           # ASR → Agent → Write orchestration
│   ├── asr_engine.py         # faster-whisper wrapper (CPU)
│   ├── asr_engine_vulkan.py  # whisper.cpp Vulkan wrapper
│   ├── agent_graph.py        # LangGraph 3-node workflow
│   ├── db.py                 # SQLite state machine + checkpoint-resume
│   ├── watcher.py            # watchdog file monitor
│   ├── classifier.py         # filename keyword classifier
│   ├── throttle.py           # process priority + dynamic profile + idle detection
│   ├── singleton.py          # single-instance PID lock
│   ├── vault_cache.py        # note list/content preview cache
│   ├── embedding.py          # DashScope embedding + numpy k-means
│   ├── updater.py            # cross-reference incremental updates
│   ├── feedback.py           # user edit detection
│   ├── refiner.py            # vault MOC generation
│   ├── drafts.py             # semi-auto draft management
│   ├── audit.py              # audit trail (JSONL + SQLite)
│   ├── metrics.py            # runtime metrics collector
│   └── logger.py             # log rotation + API key redaction
├── .model_cache/             # Whisper models (auto-downloaded, gitignored)
├── logs/                     # logs + audit.jsonl + vidbrain.pid (gitignored)
├── pipeline.db               # SQLite (gitignored)
└── pyproject.toml
```

## Constraints

- **Input directory is read-only.** VidBrain never creates, modifies, renames, or deletes any file under `--input-dir`.
- Processed videos stay in place; the DB tracks `SUCCESS` status to skip them on restart.
- The only output target is `--vault-dir` (your Obsidian vault).

## Security

- `DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL` are read from Windows system environment variables only — never written to disk, config files, or logs.
- The logger automatically redacts `key` / `secret` / `token` / `sk` patterns.
