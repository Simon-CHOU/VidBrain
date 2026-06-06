# Pipeline throughput optimization Spec

## Why
Previous run processed only 2/5 videos successfully in 15 minutes, with 3 permanent failures caused by HuggingFace SSL connectivity issue. The SSL error chain (model download → SSL EOF → retry × 3 → 30s cooldown → permanent fail) combined with 30-minute batch interval and aggressive CPU throttling leaves the system idle >95% of the time.

## What Changes
- **config.py**: Change defaults `video_cooldown=0`, `priority_level="normal"`, `batch_size=10`, `cpu_threads` to use all cores
- **asr_engine.py**: Add model download retry with exponential backoff + HF mirror support via `HF_ENDPOINT` env var; add `prepare_model()` method for pre-download
- **main.py**: Call `prepare_model()` at startup to ensure model is downloaded before first batch; change default interval to `5m`
- **run_daemon.ps1**: Update default parameters: `interval=5m`, `batch_size=10`, `cooldown=0`

## Impact
- Affected specs: none
- Affected code: `config.py`, `asr_engine.py`, `main.py`, `run_daemon.ps1`

## MODIFIED Requirements

### Requirement: ASR Model Resilience
The ASR engine SHALL retry model download with exponential backoff (3 attempts, 2^n seconds) and support HF mirror via `HF_ENDPOINT` environment variable.

#### Scenario: Model download succeeds via mirror
- **WHEN** `HF_ENDPOINT=https://hf-mirror.com` is set
- **THEN** the model downloads from the mirror successfully

#### Scenario: Model pre-cached
- **WHEN** the model is already cached in `.model_cache/`
- **THEN** no download is attempted

### Requirement: Production-grade defaults
System SHALL use production-optimized defaults: no cooldown, normal process priority, full CPU utilization, 5-minute batch interval.

#### Scenario: Default production run
- **WHEN** running `python -m vidbrain.main --vault-dir ... --interval 5m`
- **THEN** batches process with 0s cooldown, using all CPU cores, at normal priority
