# Tasks

## Task 1: ASR model download resilience
- [x] Add `prepare_model()` classmethod to `ASREngine` that downloads model upfront with retry (exponential backoff: 1s, 2s, 4s)
- [x] Use `HF_ENDPOINT` env var if set (enables HF mirror like `https://hf-mirror.com`)
- [x] Call `prepare_model()` in `main.py` before creating ASREngine, so model is ready before processing starts

## Task 2: Production defaults optimization
- [x] In `config.py`, change `priority_level` default from `"below_normal"` to `"normal"`, change `batch_size` default from `5` to `10`
- [x] In `config.py`, change `cpu_threads` default from `max(1, min(2, cpu_count // 4))` to `max(1, cpu_count - 1)` 
- [x] In `main.py`, change `--interval` default from `""` to `"30m"`

## Task 3: Daemon script defaults
- [x] In `run_daemon.ps1`, change `$Interval` default from `"30m"` to `"5m"`, `$BatchSize` from `5` to `10`, `$Cooldown` from `30` to `0`

## Task 4: Restart daemon with new config
- [x] Stop any running VidBrain daemon
- [x] Start daemon with updated defaults: interval=5m, batch=10, cooldown=0, normal priority, max CPU threads

# Task Dependencies
- Task 2 depends on nothing
- Task 1 depends on nothing
- Task 3 depends on nothing
- Task 4 depends on Task 1, 2, 3
