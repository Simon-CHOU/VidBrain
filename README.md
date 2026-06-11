# VidBrain

VidBrain watches a local folder of `.mp4` tech talk videos, transcribes them with local ASR, runs cleanup and knowledge extraction through a DeepSeek-powered agent, and writes structured Markdown notes into your Obsidian vault.

It is designed to run as a long-lived local daemon with checkpoint resume, single-instance protection, and automatic CPU throttling based on desktop activity.

## Quick Start

```powershell
cd VidBrain
uv sync
```

Set these Windows environment variables before the first run:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`

## Best Practice

Use this as the default startup command:

```powershell
uv run python -m vidbrain.main --vault-dir ./my_vault --profile auto --interval 30m
```

Why this is the recommended default:

- `--profile auto` keeps the app desktop-friendly while you are using the machine.
- `--interval 30m` runs VidBrain as a steady long-lived daemon with simple predictable behavior.
- The process resumes safely after interruption because state is stored in `pipeline.db`.

Stop it with `Ctrl+C` once for a graceful shutdown.

## Remote ASR Quick Start

If you want to keep `Desktop` as the main VidBrain process and offload ASR to a `Laptop`, use this `primary/worker` setup.

Laptop side, start the remote ASR worker:

```powershell
uv run python -m vidbrain.main --role worker --asr-backend vulkan --model-size tiny --remote-asr-port 8080
```

If the Laptop is not ready for `vulkan` yet, use CPU first:

```powershell
uv run python -m vidbrain.main --role worker --asr-backend cpu --model-size tiny --remote-asr-port 8080
```

Desktop side, start the main VidBrain process and point it at the Laptop:

```powershell
uv run python -m vidbrain.main --role primary --vault-dir ./my_vault --remote-asr-host LAPTOP-3J6HL311 --remote-asr-port 8080 --profile auto --interval 30m
```

Minimal one-shot test:

```powershell
uv run python -m vidbrain.main --role primary --vault-dir ./my_vault --remote-asr-host LAPTOP-3J6HL311 --remote-asr-port 8080 --once
```

What this gives you:

- `Laptop` only runs the remote ASR worker.
- `Desktop` keeps the full VidBrain pipeline and Obsidian output flow.
- VidBrain prefers the remote worker when it is healthy.
- VidBrain falls back to local CPU ASR if the remote worker is unavailable.

See [vidbrain-remote-asr-feasibility.md](docs/vidbrain-remote-asr-feasibility.md) for the fuller setup guide, health-check behavior, and the `Task Scheduler` worker auto-start example.

## Manual

Detailed commands, operating modes, stop/check instructions, and the full CLI reference now live in [manual.md](docs/manual.md).

## How It Works

```text
.mp4 detected
  -> classify by filename
  -> transcribe with local ASR
  -> clean and enrich with the agent
  -> write Markdown into the Obsidian vault
  -> update related notes with cross-links
```

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended)
- Windows for the full desktop-throttling and Vulkan feature set

## Constraints

- `--input-dir` is read-only. VidBrain never modifies source videos.
- `--vault-dir` is the only content output target.
- Only one VidBrain instance can run at a time through `logs/vidbrain.pid`.
