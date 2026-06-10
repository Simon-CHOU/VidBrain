"""Tests for remote ASR client and remote-first routing."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.main import _build_asr_engine
from src.models.config import PipelineConfig
from src.services.remote_asr_service import RemoteASRClient, RemoteASRError, RemoteFirstASREngine


class _RemoteASRHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = {"status": "ok", "role": "worker", "backend": "vulkan", "model_size": "tiny"}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/inference":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(content_length)
        assert request_body == b"fake-video-binary"
        payload = {
            "status": "ok",
            "segments": [{"start": 0.0, "end": 1.25, "text": "remote transcript"}],
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class TestRemoteASRClient:
    def test_health_and_inference(self, tmp_path: Path) -> None:
        sample_video = tmp_path / "sample.mp4"
        sample_video.write_bytes(b"fake-video-binary")

        server = ThreadingHTTPServer(("127.0.0.1", 0), _RemoteASRHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            client = RemoteASRClient(
                host="127.0.0.1",
                port=server.server_address[1],
                timeout_seconds=1.0,
            )
            health = client.check_health()
            segments = client.transcribe(str(sample_video))

            assert health["status"] == "ok"
            assert health["backend"] == "vulkan"
            assert segments == [{"start": 0.0, "end": 1.25, "text": "remote transcript"}]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class TestRemoteFirstASREngine:
    def test_health_check_failure_trips_circuit_before_remote_request(self) -> None:
        remote_client = MagicMock()
        remote_client.check_health.side_effect = [
            {
                "status": "ok",
                "backend": "cpu",
                "model_size": "tiny",
            },
            RemoteASRError("health failed"),
        ]

        local_cpu_engine = MagicMock()
        local_cpu_engine.transcribe.return_value = [{"start": 0.0, "end": 0.5, "text": "local"}]

        engine = RemoteFirstASREngine(
            remote_client=remote_client,
            local_cpu_engine=local_cpu_engine,
            health_interval_seconds=0,
            failure_threshold=1,
            recovery_threshold=2,
            cooldown_seconds=30,
        )
        engine.bootstrap()

        result = engine.transcribe("C:/videos/test.mp4")

        assert result == [{"start": 0.0, "end": 0.5, "text": "local"}]
        assert engine.state == "open"
        remote_client.transcribe.assert_not_called()
        local_cpu_engine.transcribe.assert_called_once_with("C:/videos/test.mp4")

    def test_remote_failure_falls_back_to_local_cpu_and_opens_circuit(self) -> None:
        remote_client = MagicMock()
        remote_client.check_health.return_value = {
            "status": "ok",
            "backend": "cpu",
            "model_size": "tiny",
        }
        remote_client.transcribe.side_effect = [
            RemoteASRError("boom-1"),
            RemoteASRError("boom-2"),
        ]

        local_cpu_engine = MagicMock()
        local_cpu_engine.transcribe.return_value = [{"start": 0.0, "end": 0.5, "text": "local"}]

        engine = RemoteFirstASREngine(
            remote_client=remote_client,
            local_cpu_engine=local_cpu_engine,
            health_interval_seconds=60,
            failure_threshold=2,
            recovery_threshold=2,
            cooldown_seconds=30,
        )
        engine.bootstrap()

        first = engine.transcribe("C:/videos/test.mp4")
        second = engine.transcribe("C:/videos/test.mp4")

        assert first == [{"start": 0.0, "end": 0.5, "text": "local"}]
        assert second == [{"start": 0.0, "end": 0.5, "text": "local"}]
        assert engine.state == "open"
        assert remote_client.transcribe.call_count == 2
        local_cpu_engine.transcribe.assert_called_with("C:/videos/test.mp4")
        assert local_cpu_engine.transcribe.call_count == 2

    def test_cooldown_probe_recovers_remote_after_threshold(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.now = 0.0

            def __call__(self) -> float:
                return self.now

            def advance(self, seconds: float) -> None:
                self.now += seconds

        clock = FakeClock()
        remote_client = MagicMock()
        remote_client.check_health.side_effect = [
            {
                "status": "ok",
                "backend": "vulkan",
                "model_size": "tiny",
            },
            {
                "status": "ok",
                "backend": "vulkan",
                "model_size": "tiny",
            },
            {
                "status": "ok",
                "backend": "vulkan",
                "model_size": "tiny",
            },
        ]
        remote_client.transcribe.side_effect = [
            RemoteASRError("boom"),
            [{"start": 0.0, "end": 1.0, "text": "remote back"}],
        ]

        local_cpu_engine = MagicMock()
        local_cpu_engine.transcribe.return_value = [{"start": 0.0, "end": 0.5, "text": "local"}]

        engine = RemoteFirstASREngine(
            remote_client=remote_client,
            local_cpu_engine=local_cpu_engine,
            health_interval_seconds=5,
            failure_threshold=1,
            recovery_threshold=2,
            cooldown_seconds=30,
            time_fn=clock,
        )
        engine.bootstrap()

        first = engine.transcribe("C:/videos/test.mp4")
        assert first == [{"start": 0.0, "end": 0.5, "text": "local"}]
        assert engine.state == "open"

        clock.advance(10)
        cooldown_result = engine.transcribe("C:/videos/test.mp4")
        assert cooldown_result == [{"start": 0.0, "end": 0.5, "text": "local"}]
        assert engine.state == "open"
        assert remote_client.check_health.call_count == 1

        clock.advance(20)
        first_probe = engine.transcribe("C:/videos/test.mp4")
        assert first_probe == [{"start": 0.0, "end": 0.5, "text": "local"}]
        assert engine.state == "half_open"

        clock.advance(5)
        second_probe = engine.transcribe("C:/videos/test.mp4")
        assert second_probe == [{"start": 0.0, "end": 0.5, "text": "local"}]
        assert engine.state == "closed"

        clock.advance(1)
        recovered = engine.transcribe("C:/videos/test.mp4")
        assert recovered == [{"start": 0.0, "end": 1.0, "text": "remote back"}]
        assert remote_client.transcribe.call_count == 2
        assert local_cpu_engine.transcribe.call_count == 4


class TestPrimaryRemoteRouting:
    def test_build_asr_engine_prefers_remote_and_uses_cpu_fallback(self) -> None:
        cfg = PipelineConfig(
            role="primary",
            model_size="tiny",
            cpu_threads=3,
            asr_backend="vulkan",
            remote_asr_host="192.168.1.9",
            remote_asr_port=8090,
            remote_asr_timeout_seconds=1.5,
            remote_asr_health_interval_seconds=12,
            remote_asr_failure_threshold=4,
            remote_asr_recovery_threshold=3,
            remote_asr_cooldown_seconds=45,
        )

        with (
            patch("src.main.ASREngine") as mock_asr_cls,
            patch("src.main.RemoteASRClient") as mock_client_cls,
            patch("src.main.RemoteFirstASREngine") as mock_remote_engine_cls,
        ):
            engine = _build_asr_engine(cfg)

        mock_asr_cls.prepare_model.assert_called_once_with("tiny", 3)
        mock_asr_cls.assert_called_once_with(model_size="tiny", cpu_threads=3)
        mock_client_cls.assert_called_once_with(host="192.168.1.9", port=8090, timeout_seconds=1.5)
        mock_remote_engine_cls.assert_called_once_with(
            remote_client=mock_client_cls.return_value,
            local_cpu_engine=mock_asr_cls.return_value,
            health_interval_seconds=12,
            failure_threshold=4,
            recovery_threshold=3,
            cooldown_seconds=45,
        )
        mock_remote_engine_cls.return_value.bootstrap.assert_called_once_with()
        assert engine is mock_remote_engine_cls.return_value
