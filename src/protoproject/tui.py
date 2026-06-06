"""Read-only Textual TUI for reviewing Phase 1 ingest results."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Label, TabbedContent, TabPane

from .models import IngestResult


class IngestReviewApp(App[None]):
    """Display the result of an ingest run in an interactive terminal UI.

    Phase 1 scope: read-only review.  Interactive refinement is Phase 2.
    """

    TITLE = "ProtoProject — Ingest Review"
    CSS = """
    Screen {
        background: $surface;
    }
    Label.summary {
        padding: 1 2;
        color: $text-muted;
    }
    DataTable {
        height: 1fr;
    }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("tab", "focus_next", "Next panel", show=False),
    ]

    def __init__(self, result: IngestResult) -> None:
        super().__init__()
        self._result = result

    def compose(self) -> ComposeResult:
        src = self._result.source
        req_count = len(self._result.requirements)
        issue_count = len(self._result.issues)

        yield Header()
        yield Label(
            f"Source: {src.id}  |  {src.path or '(stdin)'}  |  "
            f"Requirements: {req_count}  |  Audit issues: {issue_count}",
            classes="summary",
        )
        with TabbedContent():
            with TabPane(f"Requirements ({req_count})", id="reqs"):
                table = DataTable(id="req_table")
                table.add_columns("ID", "Layer", "State", "CV", "Text")
                for req in self._result.requirements:
                    text_preview = (
                        req.text if len(req.text) <= 80 else req.text[:77] + "..."
                    )
                    table.add_row(
                        req.id,
                        req.layer,
                        req.state,
                        str(req.concern_value),
                        text_preview,
                    )
                yield table
            with TabPane(f"Audit Issues ({issue_count})", id="issues"):
                table = DataTable(id="issue_table")
                table.add_columns("Code", "Requirement", "Message")
                for issue in self._result.issues:
                    table.add_row(
                        issue.code,
                        issue.requirement_id or "—",
                        issue.message,
                    )
                yield table
        yield Footer()
