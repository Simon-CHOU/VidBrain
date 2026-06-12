"""Tests for primary/worker startup entrypoints."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request, urlopen

from src.main import _make_worker_handler, main, run_worker
from src.models.config import PipelineConfig


class TestMainRoleRouting:
    """Ensure CLI role selection dispatches to the correct runtime path."""

    def test_main_routes_primary_by_default(self) -> None:
        """Default role should continue using the primary path."""
        with (
            patch("src.main.run_primary") as mock_primary,
            patch("src.main.run_worker") as mock_worker,
        ):
            main(["--once"])

        mock_primary.assert_called_once()
        mock_worker.assert_not_called()
        _args, cfg = mock_primary.call_args.args
        assert cfg.role == "primary"

    def test_main_routes_worker_role(self) -> None:
        """Worker role should bypass the primary pipeline path."""
        with (
            patch("src.main.run_primary") as mock_primary,
            patch("src.main.run_worker") as mock_worker,
        ):
            main(["--role", "worker"])

        mock_worker.assert_called_once()
        mock_primary.assert_not_called()
        cfg = mock_worker.call_args.args[0]
        assert cfg.role == "worker"


class TestWorkerRuntime:
    """Validate the worker minimal runtime boundary."""

    def test_run_worker_skips_primary_runtime_components(self) -> None:
        """Worker mode should not initialize the primary-only stack."""
        cfg = PipelineConfig(role="worker", remote_asr_port=8080)

        with (
            patch("src.main.setup_logger") as mock_setup_logger,
            patch(
                "src.main._build_asr_engine", return_value=object()
            ) as mock_build_engine,
            patch("src.main._serve_worker") as mock_serve_worker,
            patch("src.main.acquire_singleton") as mock_singleton,
            patch("src.main.DatabaseManager") as mock_db_manager,
            patch("src.main.LLMConfig") as mock_llm_config,
            patch("src.main.get_metrics") as mock_get_metrics,
            patch("src.main.get_audit") as mock_get_audit,
            patch("src.main.start_watcher") as mock_start_watcher,
            patch("src.main.process_batch") as mock_process_batch,
        ):
            run_worker(cfg)

        mock_setup_logger.assert_called_once()
        mock_build_engine.assert_called_once_with(cfg)
        mock_serve_worker.assert_called_once_with(mock_build_engine.return_value, cfg)
        mock_singleton.assert_not_called()
        mock_db_manager.assert_not_called()
        mock_llm_config.assert_not_called()
        mock_get_metrics.assert_not_called()
        mock_get_audit.assert_not_called()
        mock_start_watcher.assert_not_called()
        mock_process_batch.assert_not_called()

    def test_worker_http_surface(self, tmp_path: Path) -> None:
        """Worker should expose /healthz and /inference."""
        sample_audio = tmp_path / "sample.wav"
        sample_audio.write_bytes(b"RIFF")

        asr_engine = MagicMock()
        asr_engine.transcribe.return_value = [
            {"start": 0.0, "end": 1.0, "text": "test transcript"}
        ]
        cfg = PipelineConfig(role="worker", model_size="tiny", asr_backend="cpu")
        handler_cls = _make_worker_handler(asr_engine, cfg, started_at=time.time() - 1)

        from http.server import ThreadingHTTPServer

        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(f"{base_url}/healthz") as response:
                payload = json.loads(response.read().decode("utf-8"))
            assert payload["status"] == "ok"
            assert payload["role"] == "worker"
            assert payload["backend"] == "cpu"

            request = Request(
                url=f"{base_url}/inference",
                data=json.dumps({"file_path": str(sample_audio)}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))

            assert payload["status"] == "ok"
            assert payload["segments"] == asr_engine.transcribe.return_value
            asr_engine.transcribe.assert_called_once_with(str(sample_audio))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
