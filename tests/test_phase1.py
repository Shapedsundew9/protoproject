from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from copilot.generated.session_events import (  # noqa: E402
    AssistantMessageData,
    AssistantUsageData,
    SessionEvent,
    SessionEventType,
    SessionUsageInfoData,
)

from protoproject.audit import audit_requirements
from protoproject.config import AppConfig
from protoproject.embeddings import SentenceTransformerProvider
from protoproject.ingest import ingest_file
from protoproject.models import IngestResult, RequirementRecord
from protoproject.parser import _mechanical_parse_fallback, parse_requirement_text
from protoproject.progress import IngestProgressEvent, LLMUsageSummary, emit_progress
from protoproject.validator import (
    ValidationContext,
    build_source_record,
    normalize_requirements,
)

_FAKE_EMBED = [0.0] * 384


def _mock_provider():
    """Return a SentenceTransformerProvider whose embed_text is patched."""
    provider = SentenceTransformerProvider()
    provider.embed_text = lambda text: _FAKE_EMBED  # type: ignore[method-assign]
    return provider


class Phase1PipelineTests(unittest.TestCase):
    def test_parse_normalize_and_audit(self) -> None:
        raw_text = """# Requirements
- The system must store requirements.
  - The system must store a source hash.
- The system must trace versions.
"""
        drafts = _mechanical_parse_fallback(raw_text)
        self.assertEqual(len(drafts), 3)

        source = build_source_record(raw_text)
        requirements = normalize_requirements(
            drafts,
            ValidationContext(source=source, embedding_provider=_mock_provider()),
        )

        self.assertEqual(len(requirements), 3)
        self.assertEqual(requirements[1].parent_id, requirements[0].id)
        self.assertTrue(all(len(req.embedding) == 384 for req in requirements))

        issues = audit_requirements(requirements)
        self.assertEqual(issues, [])

    def test_parser_ignores_blank_lines(self) -> None:
        drafts = _mechanical_parse_fallback("\n\nThe system must work.\n\n")
        self.assertEqual(len(drafts), 1)

    def test_async_parser_uses_fallback_without_client(self) -> None:
        """parse_requirement_text falls back to mechanical parse when no client."""
        raw_text = "- The system must work.\n- The system must scale.\n"
        drafts = asyncio.run(parse_requirement_text(raw_text))
        self.assertEqual(len(drafts), 2)

    def test_async_parser_emits_llm_usage_and_progress(self) -> None:
        raw_text = "- The system must work.\n"
        response_text = (
            '[{"text": "The system must work.", "layer": "Product", '
            '"concern_value": 3, "parent_text": null}]'
        )
        session = _FakeCopilotSession(
            response=_session_event(
                AssistantMessageData(
                    content=response_text,
                    message_id="msg-1",
                    model="gpt-5.4",
                ),
                SessionEventType.ASSISTANT_MESSAGE,
            ),
            emitted_events=[
                _session_event(
                    SessionUsageInfoData(
                        current_tokens=123,
                        messages_length=1,
                        token_limit=4096,
                    ),
                    SessionEventType.SESSION_USAGE_INFO,
                ),
                _session_event(
                    AssistantUsageData(
                        model="gpt-5.4",
                        cost=0.0125,
                        input_tokens=150,
                        output_tokens=60,
                        cache_read_tokens=10,
                        duration=timedelta(seconds=1.25),
                        time_to_first_token=timedelta(milliseconds=250),
                    ),
                    SessionEventType.ASSISTANT_USAGE,
                ),
            ],
        )
        client = _FakeCopilotClient(session)
        progress_events = []
        usage_summaries: list[LLMUsageSummary] = []

        drafts = asyncio.run(
            parse_requirement_text(
                raw_text,
                client,
                progress=progress_events.append,
                on_llm_usage=usage_summaries.append,
                transcript=None,
            )
        )

        self.assertEqual(len(drafts), 1)
        self.assertEqual(client.create_session_calls, 1)
        self.assertGreaterEqual(len(progress_events), 2)
        self.assertEqual(progress_events[0].status, "started")
        self.assertEqual(progress_events[0].stage, "llm_parse")
        self.assertEqual(progress_events[-1].status, "completed")
        self.assertIsNotNone(progress_events[-1].usage)

        summary = usage_summaries[0]
        self.assertEqual(summary.model, "gpt-5.4")
        self.assertEqual(summary.cost_usd, 0.0125)
        self.assertEqual(summary.input_tokens, 150)
        self.assertEqual(summary.output_tokens, 60)
        self.assertEqual(summary.cache_read_tokens, 10)
        self.assertEqual(summary.context_tokens, 123)
        self.assertEqual(summary.token_limit, 4096)
        self.assertEqual(summary.output_chars, len(response_text))
        self.assertGreater(summary.input_chars, len(raw_text))

    def test_async_parser_reports_fallback_when_llm_fails(self) -> None:
        raw_text = "- The system must work.\n"
        client = _FakeCopilotClient(_FakeCopilotSession(error=RuntimeError("boom")))
        progress_events = []

        drafts = asyncio.run(
            parse_requirement_text(
                raw_text,
                client,
                progress=progress_events.append,
                transcript=None,
            )
        )

        self.assertEqual(len(drafts), 1)
        self.assertEqual(progress_events[0].status, "started")
        self.assertEqual(progress_events[-1].status, "fallback")
        self.assertIn("mechanical fallback", progress_events[-1].message)

    def test_depends_on_cycle_detected(self) -> None:
        source = build_source_record("test")

        req_a = RequirementRecord(
            id="REQ-A",
            text="Requirement A",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=0,
            source_id=source.id,
            depends_on_ids=["REQ-B"],
        )
        req_b = RequirementRecord(
            id="REQ-B",
            text="Requirement B",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=0,
            source_id=source.id,
            depends_on_ids=["REQ-A"],
        )
        issues = audit_requirements([req_a, req_b])
        codes = {i.code for i in issues}
        self.assertIn("CYCLE_DETECTED", codes)

    def test_missing_dependency_flagged(self) -> None:
        source = build_source_record("test")

        req = RequirementRecord(
            id="REQ-A",
            text="Requirement A",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=0,
            source_id=source.id,
            depends_on_ids=["REQ-MISSING"],
        )
        issues = audit_requirements([req])
        codes = {i.code for i in issues}
        self.assertIn("MISSING_DEPENDENCY", codes)

    def test_ingest_file_emits_pipeline_progress(self) -> None:
        progress_events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "vision.md"
            path.write_text("- The system must work.\n", encoding="utf-8")

            with (
                patch(
                    "protoproject.ingest.SentenceTransformerProvider",
                    _FakeEmbeddingProvider,
                ),
                patch("protoproject.ingest.Neo4jStore", _FakeNeo4jStore),
            ):
                result = asyncio.run(
                    ingest_file(
                        path,
                        config=AppConfig(),
                        progress=progress_events.append,
                    )
                )

        self.assertEqual(len(result.requirements), 1)
        self.assertIsNone(result.llm_usage)

        stage_order = []
        for event in progress_events:
            if event.stage not in stage_order:
                stage_order.append(event.stage)

        self.assertEqual(
            stage_order,
            [
                "read_source",
                "build_source",
                "llm_parse",
                "parse_requirements",
                "embed_requirements",
                "audit_requirements",
                "schema_init",
                "persist_source",
                "persist_requirements",
                "similarity_scan",
                "complete",
            ],
        )

    def test_run_ingest_writes_progress_to_stderr_and_usage_to_stdout(self) -> None:
        from protoproject import cli as cli_module  # noqa: PLC0415

        source = build_source_record("- The system must work.\n", path="docs/vision.md")
        requirement = RequirementRecord(
            id="REQ-1",
            text="The system must work.",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=1,
            source_id=source.id,
        )
        usage = LLMUsageSummary(
            model="gpt-test",
            cost_usd=0.0125,
            input_tokens=123,
            output_tokens=45,
            input_chars=1000,
            output_chars=240,
            duration_seconds=1.5,
        )
        result = IngestResult(
            source=source,
            requirements=[requirement],
            issues=[],
            llm_usage=usage,
        )

        async def fake_ingest_file(
            path,
            copilot_client=None,
            progress=None,
            transcript=None,
        ):
            _ = path, copilot_client
            self.assertIsNone(transcript)
            emit_progress(
                progress,
                stage="llm_parse",
                status="started",
                message="Copilot parse request in progress.",
            )
            emit_progress(
                progress,
                stage="llm_parse",
                status="completed",
                message="Copilot parse completed.",
                usage=usage,
            )
            return result

        stdout = io.StringIO()
        stderr = io.StringIO()
        real_asyncio_run = asyncio.run
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(cli_module, "ingest_file", fake_ingest_file),
            patch.object(cli_module.asyncio, "run", side_effect=real_asyncio_run),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = cli_module.main(["ingest", "docs/vision.md", "--no-tui"])

        self.assertEqual(exit_code, 0)
        self.assertIn("No COPILOT_GITHUB_TOKEN", stderr.getvalue())
        self.assertIn("[ingest:llm parse]", stderr.getvalue())
        self.assertIn("cost $0.0125", stderr.getvalue())
        self.assertIn("LLM usage:", stdout.getvalue())
        self.assertIn("cost $0.0125", stdout.getvalue())

    def test_run_ingest_uses_async_tui_runner_when_tty(self) -> None:
        from protoproject import cli as cli_module  # noqa: PLC0415

        source = build_source_record("- The system must work.\n", path="docs/vision.md")
        requirement = RequirementRecord(
            id="REQ-1",
            text="The system must work.",
            embedding=_FAKE_EMBED,
            layer="Product",
            concern_value=3,
            state="Draft",
            version=1,
            timestamp=1,
            source_id=source.id,
        )
        result = IngestResult(
            source=source,
            requirements=[requirement],
            issues=[],
            llm_usage=None,
        )

        async def fake_ingest_file(
            path,
            copilot_client=None,
            progress=None,
            transcript=None,
        ):
            _ = path, copilot_client, progress, transcript
            return result

        app_called = {"run_async": 0}

        class _FakeApp:
            def __init__(self, ingest_result) -> None:
                self.ingest_result = ingest_result

            def run(self) -> None:
                raise AssertionError(
                    "run() should not be called from async ingest path"
                )

            async def run_async(self) -> None:
                if self.ingest_result != result:
                    raise AssertionError("unexpected ingest result passed to TUI")
                app_called["run_async"] += 1

        stdout = _FakeTtyStream()
        stderr = io.StringIO()
        real_asyncio_run = asyncio.run
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(cli_module, "ingest_file", fake_ingest_file),
            patch.object(cli_module, "IngestReviewApp", _FakeApp),
            patch.object(cli_module.asyncio, "run", side_effect=real_asyncio_run),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = cli_module.main(["ingest", "docs/vision.md"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(app_called["run_async"], 1)
        self.assertIn("No COPILOT_GITHUB_TOKEN", stderr.getvalue())

    def test_parser_writes_transcript_for_request_and_response(self) -> None:
        raw_text = "- The system must work.\n"
        response_text = (
            '[{"text": "The system must work.", "layer": "Product", '
            '"concern_value": 3, "parent_text": null}]'
        )
        session = _FakeCopilotSession(
            response=_session_event(
                AssistantMessageData(
                    content=response_text,
                    message_id="msg-1",
                    model="gpt-5.4",
                ),
                SessionEventType.ASSISTANT_MESSAGE,
            )
        )
        client = _FakeCopilotClient(session)

        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = Path(tmpdir) / "transcript.log"
            drafts = asyncio.run(
                parse_requirement_text(
                    raw_text,
                    client,
                    transcript=transcript_path,
                )
            )

            transcript = transcript_path.read_text(encoding="utf-8")

        self.assertEqual(len(drafts), 1)
        self.assertIn("=== REQUEST @", transcript)
        self.assertIn("prompt:", transcript)
        self.assertIn("Extract all distinct, atomic software requirements", transcript)
        self.assertIn("=== RESPONSE @", transcript)
        self.assertIn("response:", transcript)
        self.assertIn(response_text, transcript)

    def test_parser_writes_fallback_transcript_without_client(self) -> None:
        raw_text = "- The system must work.\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = Path(tmpdir) / "transcript.log"
            drafts = asyncio.run(
                parse_requirement_text(
                    raw_text,
                    transcript=transcript_path,
                )
            )
            transcript = transcript_path.read_text(encoding="utf-8")

        self.assertEqual(len(drafts), 1)
        self.assertIn("=== FALLBACK @", transcript)
        self.assertIn("reason=no_copilot_client", transcript)

    def test_cli_ingest_parser_supports_transcript_optional_path(self) -> None:
        from protoproject import cli as cli_module  # noqa: PLC0415

        parser = cli_module._build_arg_parser()

        args_default = parser.parse_args(["ingest", "docs/vision.md", "--transcript"])
        self.assertEqual(args_default.transcript, Path("transcript.log"))

        args_custom = parser.parse_args(
            ["ingest", "docs/vision.md", "--transcript", "logs/custom.log"]
        )
        self.assertEqual(args_custom.transcript, Path("logs/custom.log"))

    def test_main_returns_130_on_keyboard_interrupt(self) -> None:
        from protoproject import cli as cli_module  # noqa: PLC0415

        def raising_run(coro):
            coro.close()
            raise KeyboardInterrupt

        with patch.object(cli_module.asyncio, "run", side_effect=raising_run):
            exit_code = cli_module.main(["ingest", "docs/vision.md", "--no-tui"])

        self.assertEqual(exit_code, 130)

    def test_cli_progress_reporter_uses_single_line_updates_on_tty(self) -> None:
        from protoproject import cli as cli_module  # noqa: PLC0415

        stream = _FakeTtyStream()
        reporter = cli_module.CliProgressReporter(stream)

        reporter(
            IngestProgressEvent(
                stage="persist_requirements",
                status="progress",
                message="Persisted requirement 1 of 3.",
                current=1,
                total=3,
            )
        )
        reporter(
            IngestProgressEvent(
                stage="persist_requirements",
                status="progress",
                message="Persisted requirement 2 of 3.",
                current=2,
                total=3,
            )
        )
        reporter(
            IngestProgressEvent(
                stage="persist_requirements",
                status="completed",
                message="Persisted 3 requirements and relationships.",
                current=3,
                total=3,
            )
        )

        self.assertEqual(stream.value.count("\n"), 1)
        self.assertGreaterEqual(stream.value.count("\r"), 3)
        self.assertIn("3/3", stream.value)

    def test_cli_progress_reporter_uses_spinner_for_llm_wait_on_tty(self) -> None:
        from protoproject import cli as cli_module  # noqa: PLC0415

        stream = _FakeTtyStream()
        reporter = cli_module.CliProgressReporter(stream)

        reporter(
            IngestProgressEvent(
                stage="llm_parse",
                status="started",
                message="Copilot parse request in progress.",
                elapsed_seconds=0.0,
            )
        )
        reporter(
            IngestProgressEvent(
                stage="llm_parse",
                status="progress",
                message="Copilot parse still in progress.",
                elapsed_seconds=6.0,
            )
        )
        reporter(
            IngestProgressEvent(
                stage="llm_parse",
                status="completed",
                message="Copilot parse completed.",
                elapsed_seconds=8.0,
            )
        )

        self.assertEqual(stream.value.count("\n"), 1)
        self.assertGreaterEqual(stream.value.count("\r"), 3)
        self.assertIn("0.0s", stream.value)
        self.assertIn("6.0s", stream.value)
        self.assertIn("done", stream.value)


class _FakeCopilotClient:
    def __init__(self, session: "_FakeCopilotSession") -> None:
        self._session = session
        self.create_session_calls = 0

    async def create_session(self, **_: object) -> "_FakeCopilotSession":
        self.create_session_calls += 1
        return self._session


class _FakeCopilotSession:
    def __init__(
        self,
        *,
        response: SessionEvent | None = None,
        emitted_events: list[SessionEvent] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._emitted_events = emitted_events or []
        self._error = error
        self._handlers = []
        self.prompt = ""

    def on(self, handler):
        self._handlers.append(handler)

        def unsubscribe() -> None:
            self._handlers.remove(handler)

        return unsubscribe

    async def send_and_wait(self, *, prompt: str) -> SessionEvent | None:
        self.prompt = prompt
        for event in self._emitted_events:
            for handler in list(self._handlers):
                handler(event)
        if self._error is not None:
            raise self._error
        return self._response


def _session_event(data, event_type: SessionEventType) -> SessionEvent:
    return SessionEvent(
        data=data,
        id=uuid4(),
        timestamp=datetime.now(timezone.utc),
        type=event_type,
    )


class _FakeEmbeddingProvider:
    def __init__(self, progress=None) -> None:
        self.progress = progress

    def embed_text(self, _text: str) -> list[float]:
        return _FAKE_EMBED


class _FakeNeo4jStore:
    def __init__(
        self,
        *,
        uri: str,
        username: str,
        password: str,
        embedding_dimension: int = 384,
        progress=None,
    ) -> None:
        _ = uri, username, password, embedding_dimension
        self.progress = progress

    def initialize_schema(self) -> None:
        emit_progress(
            self.progress,
            stage="schema_init",
            status="started",
            message="Initializing Neo4j schema.",
        )
        emit_progress(
            self.progress,
            stage="schema_init",
            status="completed",
            message="Neo4j schema ready.",
        )

    def persist_source(self, source) -> None:
        emit_progress(
            self.progress,
            stage="persist_source",
            status="started",
            message=f"Persisting source {source.id}.",
        )
        emit_progress(
            self.progress,
            stage="persist_source",
            status="completed",
            message=f"Persisted source {source.id}.",
        )

    def persist_requirements(self, requirements) -> None:
        total = len(requirements)
        emit_progress(
            self.progress,
            stage="persist_requirements",
            status="started",
            message=f"Persisting {total} requirements.",
        )
        for index, _requirement in enumerate(requirements, start=1):
            emit_progress(
                self.progress,
                stage="persist_requirements",
                status="progress",
                message=f"Persisted requirement {index} of {total}.",
                current=index,
                total=total,
            )
        emit_progress(
            self.progress,
            stage="persist_requirements",
            status="completed",
            message=f"Persisted {total} requirements and relationships.",
        )

    def find_similar(self, *_args, **_kwargs):
        return []

    def close(self) -> None:
        return None


class _FakeTtyStream:
    def __init__(self) -> None:
        self.value = ""

    def write(self, text: str) -> int:
        self.value += text
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return True


if __name__ == "__main__":
    unittest.main()
