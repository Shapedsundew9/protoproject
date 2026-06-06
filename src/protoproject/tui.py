"""Unified Textual TUI for ProtoProject — Phase 1 ingest review and Phase 2 refinement.

``ProtoProjectApp`` replaces the read-only ``IngestReviewApp`` from Phase 1.
It accepts either an ``IngestResult`` (ingest mode) or a ``ReviewResult``
(refinement review mode) and renders the appropriate tab layout.

Backward-compat shim
--------------------
``IngestReviewApp = ProtoProjectApp``  is provided at the bottom of this module
so any code that still imports ``IngestReviewApp`` continues to work unchanged.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from .models import HumanDecision, IngestResult, RequirementRecord, ReviewResult
from .progress import format_usage_summary


class ProtoProjectApp(App[HumanDecision | None]):
    """Single unified TUI for ingest review and interactive refinement.

    Modes
    -----
    *Ingest mode* (``ingest_result`` supplied):
        Shows "Ingested Requirements" and "Audit Issues" tabs.
        Each requirement row can be selected; pressing ``r`` enqueues it for
        refinement (returned via ``app.run()`` for the caller to action).

    *Refinement review mode* (``review_result`` supplied):
        Shows "Refinement Review" tab with the quality issues, the AI proposal
        in an editable TextArea, and concern-value controls.
        Returns a ``HumanDecision`` from ``app.run()``.
    """

    TITLE = "ProtoProject"
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
    #proposal_area {
        height: 8;
        border: solid $primary;
        margin: 1 2;
    }
    #concern_label {
        padding: 0 2;
        color: $accent;
    }
    #issues_table {
        height: 1fr;
    }
    .section_header {
        padding: 1 2;
        text-style: bold;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("q", "quit_app", "Quit"),
        Binding("tab", "focus_next", "Next panel", show=False),
        # Refinement bindings (active only in refinement review mode)
        Binding("a", "accept_proposal", "Accept", show=False),
        Binding("s", "skip_requirement", "Skip", show=False),
        Binding("up", "increase_concern", "CV+", show=False),
        Binding("down", "decrease_concern", "CV-", show=False),
        # Ingest mode
        Binding("r", "request_refine", "Refine selected", show=False),
    ]

    def __init__(
        self,
        ingest_result: IngestResult | None = None,
        review_result: ReviewResult | None = None,
    ) -> None:
        super().__init__()
        self._ingest = ingest_result
        self._review = review_result
        # Mutable concern value for refinement review mode.
        self._concern_value: int = (
            review_result.requirement.concern_value
            if review_result is not None
            else 3
        )

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        if self._ingest is not None:
            yield from self._compose_ingest_summary()
        if self._review is not None:
            yield from self._compose_review_summary()
        with TabbedContent():
            if self._ingest is not None:
                yield from self._compose_ingest_tabs()
            if self._review is not None:
                yield from self._compose_review_tab()
        yield Footer()

    def on_mount(self) -> None:
        if self._review is not None:
            self.TITLE = f"ProtoProject — Refinement Review: {self._review.requirement.id}"
            # Show relevant bindings in footer.
            self.BINDINGS = [  # noqa: RUF012
                Binding("a", "accept_proposal", "Accept"),
                Binding("s", "skip_requirement", "Skip"),
                Binding("up", "increase_concern", "CV+"),
                Binding("down", "decrease_concern", "CV-"),
                Binding("q", "quit_app", "Quit"),
            ]
        else:
            self.TITLE = "ProtoProject — Ingest Review"
            self.BINDINGS = [  # noqa: RUF012
                Binding("r", "request_refine", "Refine selected"),
                Binding("q", "quit_app", "Quit"),
                Binding("tab", "focus_next", "Next panel", show=False),
            ]

    # ------------------------------------------------------------------
    # Ingest mode composition helpers
    # ------------------------------------------------------------------

    def _compose_ingest_summary(self):
        src = self._ingest.source
        req_count = len(self._ingest.requirements)
        issue_count = len(self._ingest.issues)
        yield Label(
            f"Source: {src.id}  |  {src.path or '(stdin)'}  |  "
            f"Requirements: {req_count}  |  Audit issues: {issue_count}",
            classes="summary",
        )
        if self._ingest.llm_usage is not None:
            yield Label(
                f"LLM Usage: {format_usage_summary(self._ingest.llm_usage)}",
                classes="summary",
            )
        else:
            yield Label("LLM Usage: mechanical parser", classes="summary")

    def _compose_ingest_tabs(self):
        req_count = len(self._ingest.requirements)
        issue_count = len(self._ingest.issues)
        with TabPane(f"Requirements ({req_count})", id="reqs"):
            table = DataTable(id="req_table")
            table.add_columns("ID", "Layer", "State", "CV", "Text")
            for req in self._ingest.requirements:
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
            for issue in self._ingest.issues:
                table.add_row(
                    issue.code,
                    issue.requirement_id or "—",
                    issue.message,
                )
            yield table

    # ------------------------------------------------------------------
    # Refinement review composition helpers
    # ------------------------------------------------------------------

    def _compose_review_summary(self):
        req = self._review.requirement
        yield Label(
            f"ID: {req.id}  |  Layer: {req.layer}  |  Version: {req.version}  |  "
            f"State: {req.state}",
            classes="summary",
        )

    def _compose_review_tab(self):
        req = self._review.requirement
        issues = self._review.quality_issues
        proposal = self._review.proposal

        with TabPane("Refinement Review", id="refine_review"):
            yield Label("Quality Issues", classes="section_header")
            issues_table = DataTable(id="issues_table")
            issues_table.add_columns("Code", "Severity", "Message")
            for issue in issues:
                issues_table.add_row(issue.code, issue.severity, issue.message)
            yield issues_table

            proposed_text = proposal.proposed_text if proposal else req.text
            yield Label("Proposed Text (editable — press 'a' to accept)", classes="section_header")
            yield TextArea(proposed_text, id="proposal_area")

            yield Label(
                f"Concern Value: [{self._concern_value}]  (↑/↓ to adjust, 1–5)",
                id="concern_label",
            )
            yield Label(
                "  a = Accept  |  s = Skip (leave as Draft)  |  q = Quit run",
                classes="summary",
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_quit_app(self) -> None:
        self.exit(None)

    def action_accept_proposal(self) -> None:
        if self._review is None:
            return
        text_area = self.query_one("#proposal_area", TextArea)
        accepted_text = text_area.text.strip()
        if not accepted_text:
            return
        self.exit(
            HumanDecision(
                action="accept",
                text=accepted_text,
                concern_value=self._concern_value,
            )
        )

    def action_skip_requirement(self) -> None:
        if self._review is None:
            return
        req = self._review.requirement
        self.exit(
            HumanDecision(
                action="skip",
                text=req.text,
                concern_value=req.concern_value,
            )
        )

    def action_increase_concern(self) -> None:
        if self._review is None:
            return
        self._concern_value = min(5, self._concern_value + 1)
        self._refresh_concern_label()

    def action_decrease_concern(self) -> None:
        if self._review is None:
            return
        self._concern_value = max(1, self._concern_value - 1)
        self._refresh_concern_label()

    def action_request_refine(self) -> None:
        """In ingest mode, signal that the selected requirement should be refined."""
        if self._ingest is None:
            return
        try:
            table = self.query_one("#req_table", DataTable)
            row_key = table.cursor_row
            if row_key is not None and 0 <= row_key < len(self._ingest.requirements):
                req = self._ingest.requirements[row_key]
                # Return the requirement ID so the CLI can route it for refinement.
                self.exit(
                    HumanDecision(
                        action="refine_selected",
                        text=req.id,
                        concern_value=req.concern_value,
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_concern_label(self) -> None:
        try:
            label = self.query_one("#concern_label", Label)
            label.update(
                f"Concern Value: [{self._concern_value}]  (↑/↓ to adjust, 1–5)"
            )
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Backward-compat alias
# ---------------------------------------------------------------------------

IngestReviewApp = ProtoProjectApp
