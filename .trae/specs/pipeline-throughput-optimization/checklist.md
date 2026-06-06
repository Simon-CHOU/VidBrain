# Checklist

- [x] `asr_engine.py` has `prepare_model()` with retry logic and HF_ENDPOINT support
- [x] `main.py` calls `prepare_model()` before creating ASREngine
- [x] `config.py` defaults: `priority_level="normal"`, `batch_size=10`, `cpu_threads` uses all cores
- [x] `config.py` sets `HF_ENDPOINT` mirror for model downloads
- [x] `run_daemon.ps1` defaults: interval=5m, batch_size=10, cooldown=0
- [x] Python import test passes
- [x] Daemon restarted with new config — confirmed processing videos successfully
