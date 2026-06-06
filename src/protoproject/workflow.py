"""LangGraph refinement workflow for Phase 2 — The Conversational Architect.

Flow
----
mark_under_review
  → evaluate
  → [no issues]  mark_stabilized → commit
  → [has issues] generate_proposal
                   → [low concern & no high-severity] apply_auto → commit
                   → [high concern or high-severity]  INTERRUPT (needs_human)
                       (caller presents TUI, then calls workflow.resume())
                         → apply_decision → commit
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import replace
from typing import Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from .embeddings import SentenceTransformerProvider
from .models import HumanDecision, RequirementRecord, ReviewResult, WorkflowOutcome
from .progress import AnyProgressReporter, emit_refine_progress
from .quality import propose_refinement, review_requirement
from .refinement import apply_refinement

log = logging.getLogger(__name__)

_REFINE_PROMPT_TEMPLATE = """\
You are a requirements-engineering assistant following NASA quality standards.

A software requirement has been flagged with the following issues:
{issue_list}

Original requirement:
  {original_text}

A rule-based tool has produced this draft improvement:
  {proposed_text}

Please rewrite the requirement so it:
- Uses a clear normative modal verb (must / shall / should / will)
- Is free of vague or unmeasurable language
- Is specific enough to be objectively verified
- Retains the original intent faithfully

Return ONLY the improved requirement text. No explanation, no preamble."""

_LLM_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class RefinementState(TypedDict):
    """Mutable state carried through the LangGraph workflow for one requirement."""

    requirement: RequirementRecord
    issues: list
    proposal: Any | None
    human_decision: HumanDecision | None
    revised: RequirementRecord | None
    committed: bool


# ---------------------------------------------------------------------------
# Public workflow class
# ---------------------------------------------------------------------------


class RefinementWorkflow:
    """Runs one requirement at a time through the Phase 2 refinement state machine.

    Parameters
    ----------
    store:
        A ``Neo4jStore`` instance used for checkpoint writes and final commits.
    embedding_provider:
        Used to re-embed revised requirement text.  Defaults to
        ``SentenceTransformerProvider`` (consistent with Phase 1).
    copilot_client:
        Optional GitHub Copilot SDK client for AI-enhanced proposals.
        When ``None`` the workflow falls back to rule-based proposals only.
    progress:
        Optional reporter callable for ``RefineProgressEvent`` updates.
    """

    def __init__(
        self,
        store,
        *,
        embedding_provider=None,
        copilot_client=None,
        progress: AnyProgressReporter | None = None,
    ) -> None:
        self._store = store
        self._embedding_provider = embedding_provider or SentenceTransformerProvider()
        self._copilot_client = copilot_client
        self._progress = progress
        self._graph = self._build_graph()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_one(self, requirement: RequirementRecord) -> WorkflowOutcome:
        """Process a single requirement through the full workflow.

        Returns a ``WorkflowOutcome`` whose ``status`` is one of:
        ``"stabilized"``, ``"auto_refined"``, ``"needs_human"``, ``"skipped"``,
        ``"error"``.

        When ``status == "needs_human"`` the workflow has paused; call
        :meth:`resume` with the user's decision to continue.
        """
        initial_state: RefinementState = {
            "requirement": requirement,
            "issues": [],
            "proposal": None,
            "human_decision": None,
            "revised": None,
            "committed": False,
        }
        try:
            final_state = self._graph.invoke(initial_state)
        except _HumanReviewRequired as exc:
            return WorkflowOutcome(
                requirement_id=requirement.id,
                status="needs_human",
                pending_review=exc.review_result,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Workflow error for %s: %s", requirement.id, exc)
            return WorkflowOutcome(
                requirement_id=requirement.id,
                status="error",
                error=str(exc),
            )

        revised = final_state.get("revised")
        issues = final_state.get("issues") or []
        if revised is None:
            return WorkflowOutcome(
                requirement_id=requirement.id,
                status="stabilized",
                revised=final_state["requirement"],
            )
        if issues:
            return WorkflowOutcome(
                requirement_id=requirement.id,
                status="auto_refined",
                revised=revised,
            )
        return WorkflowOutcome(
            requirement_id=requirement.id,
            status="stabilized",
            revised=revised,
        )

    def resume(
        self, requirement: RequirementRecord, decision: HumanDecision
    ) -> WorkflowOutcome:
        """Resume a paused workflow with the user's decision.

        Reconstructs quality state (cheap, deterministic) then runs the
        apply + commit path according to the human's choice.
        """
        try:
            if decision.action == "skip":
                # Revert from Under_Review back to Draft.
                self._store.mark_requirement_state(requirement.id, "Draft")
                emit_refine_progress(
                    self._progress,
                    stage="human_review",
                    status="completed",
                    requirement_id=requirement.id,
                    message="User skipped refinement; requirement left as Draft.",
                    action="skipped",
                )
                return WorkflowOutcome(
                    requirement_id=requirement.id,
                    status="skipped",
                )

            revised = apply_refinement(
                requirement,
                decision.text,
                decision.concern_value,
                target_state="Stabilized",
            )
            revised = self._re_embed(revised)
            self._store.persist_requirement_revision(revised)

            emit_refine_progress(
                self._progress,
                stage="commit",
                status="completed",
                requirement_id=requirement.id,
                message=f"Human-accepted revision committed as v{revised.version}.",
                action="human_accepted",
            )
            return WorkflowOutcome(
                requirement_id=requirement.id,
                status="auto_refined",
                revised=revised,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Resume error for %s: %s", requirement.id, exc)
            return WorkflowOutcome(
                requirement_id=requirement.id,
                status="error",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> Any:
        g: StateGraph = StateGraph(RefinementState)

        g.add_node("mark_under_review", self._node_mark_under_review)
        g.add_node("evaluate", self._node_evaluate)
        g.add_node("mark_stabilized", self._node_mark_stabilized)
        g.add_node("generate_proposal", self._node_generate_proposal)
        g.add_node("apply_auto", self._node_apply_auto)
        g.add_node("human_review", self._node_human_review)
        g.add_node("commit", self._node_commit)

        g.set_entry_point("mark_under_review")
        g.add_edge("mark_under_review", "evaluate")
        g.add_conditional_edges(
            "evaluate",
            self._route_after_evaluate,
            {"clean": "mark_stabilized", "issues": "generate_proposal"},
        )
        g.add_edge("mark_stabilized", "commit")
        g.add_conditional_edges(
            "generate_proposal",
            self._route_after_proposal,
            {"auto": "apply_auto", "human": "human_review"},
        )
        g.add_edge("apply_auto", "commit")
        g.add_edge("commit", END)
        # human_review raises _HumanReviewRequired so no outgoing edge is needed.

        return g.compile()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def _node_mark_under_review(self, state: RefinementState) -> dict:
        req = state["requirement"]
        emit_refine_progress(
            self._progress,
            stage="mark_under_review",
            status="started",
            requirement_id=req.id,
            message=f"Marking {req.id} as Under_Review.",
        )
        self._store.mark_requirement_state(req.id, "Under_Review")
        return {}

    def _node_evaluate(self, state: RefinementState) -> dict:
        req = state["requirement"]
        emit_refine_progress(
            self._progress,
            stage="evaluate",
            status="started",
            requirement_id=req.id,
            message=f"Running NASA quality checks on {req.id}.",
        )
        issues = review_requirement(req)
        emit_refine_progress(
            self._progress,
            stage="evaluate",
            status="completed",
            requirement_id=req.id,
            message=f"{len(issues)} issue(s) found.",
        )
        return {"issues": issues}

    def _node_mark_stabilized(self, state: RefinementState) -> dict:
        req = state["requirement"]
        emit_refine_progress(
            self._progress,
            stage="mark_stabilized",
            status="completed",
            requirement_id=req.id,
            message=f"{req.id} passed all quality checks — marking Stabilized.",
            action="stabilized",
        )
        stabilized = replace(req, state="Stabilized")
        return {"revised": stabilized}

    def _node_generate_proposal(self, state: RefinementState) -> dict:
        req = state["requirement"]
        issues = state["issues"]
        emit_refine_progress(
            self._progress,
            stage="generate_proposal",
            status="started",
            requirement_id=req.id,
            message="Generating refinement proposal.",
        )
        proposal = propose_refinement(req, issues)

        # AI enhancement via Copilot SDK when available.
        if self._copilot_client is not None:
            proposal = self._enhance_with_copilot(req, issues, proposal)

        emit_refine_progress(
            self._progress,
            stage="generate_proposal",
            status="completed",
            requirement_id=req.id,
            message="Proposal ready.",
        )
        return {"proposal": proposal}

    def _node_apply_auto(self, state: RefinementState) -> dict:
        req = state["requirement"]
        proposal = state["proposal"]
        revised = apply_refinement(
            req,
            proposal.proposed_text,
            proposal.concern_value,
            target_state="Stabilized",
        )
        revised = self._re_embed(revised)
        emit_refine_progress(
            self._progress,
            stage="apply_auto",
            status="completed",
            requirement_id=req.id,
            message=f"Auto-refined to v{revised.version}.",
            action="auto_refined",
        )
        return {"revised": revised}

    def _node_human_review(self, state: RefinementState) -> dict:
        """Interrupt the workflow; caller must call resume() with a decision."""
        req = state["requirement"]
        issues = state["issues"]
        proposal = state["proposal"]
        emit_refine_progress(
            self._progress,
            stage="human_review",
            status="started",
            requirement_id=req.id,
            message=(
                f"{req.id} requires human review "
                f"(concern={req.concern_value}, "
                f"high_severity={any(i.severity == 'high' for i in issues)})."
            ),
        )
        review_result = ReviewResult(
            requirement=req,
            quality_issues=issues,
            proposal=proposal,
        )
        raise _HumanReviewRequired(review_result)

    def _node_commit(self, state: RefinementState) -> dict:
        revised = state.get("revised")
        req = state["requirement"]
        target = revised if revised is not None else req
        emit_refine_progress(
            self._progress,
            stage="commit",
            status="started",
            requirement_id=req.id,
            message=f"Committing {target.id} v{target.version} → {target.state}.",
        )
        self._store.persist_requirement_revision(target)
        emit_refine_progress(
            self._progress,
            stage="commit",
            status="completed",
            requirement_id=req.id,
            message="Committed.",
        )
        return {"committed": True}

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _route_after_evaluate(self, state: RefinementState) -> str:
        return "issues" if state["issues"] else "clean"

    def _route_after_proposal(self, state: RefinementState) -> str:
        req = state["requirement"]
        issues = state["issues"]
        proposal = state["proposal"]
        effective_concern = max(
            req.concern_value,
            proposal.concern_value if proposal else req.concern_value,
        )
        has_high_severity = any(i.severity == "high" for i in issues)
        if effective_concern >= 4 or has_high_severity:
            return "human"
        return "auto"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _re_embed(self, req: RequirementRecord) -> RequirementRecord:
        """Return *req* with a freshly generated embedding for the revised text."""
        embedding = self._embedding_provider.embed_text(req.text)
        return replace(req, embedding=embedding)

    def _enhance_with_copilot(self, req, issues, proposal):
        """Call the Copilot SDK to improve the rule-based proposal.

        Falls back to the rule-based proposal on any failure so the workflow
        always continues.
        """
        issue_list = "\n".join(
            f"  - [{i.code}] {i.severity}: {i.message}" for i in issues
        )
        prompt = _REFINE_PROMPT_TEMPLATE.format(
            issue_list=issue_list,
            original_text=req.text,
            proposed_text=proposal.proposed_text,
        )
        try:
            improved_text = asyncio.run(self._call_copilot(prompt))
            if improved_text and improved_text.strip():
                return replace(proposal, proposed_text=improved_text.strip())
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Copilot refinement enhancement failed for %s (%s); "
                "using rule-based proposal.",
                req.id,
                exc,
            )
        return proposal

    async def _call_copilot(self, prompt: str) -> str:
        """Send *prompt* to the Copilot SDK and return the response text."""
        from copilot import PermissionHandler  # noqa: PLC0415

        if hasattr(self._copilot_client, "start"):
            await self._copilot_client.start()
        try:
            session = await self._copilot_client.create_session(
                model="auto",
                on_permission_request=PermissionHandler.approve_all,
                streaming=False,
            )
            response = await session.send_and_wait(
                prompt=prompt,
                timeout=_LLM_TIMEOUT_SECONDS,
            )
            if hasattr(response, "text"):
                return response.text or ""
            if hasattr(response, "content"):
                content = response.content
                if isinstance(content, list):
                    return "".join(
                        getattr(block, "text", "")
                        for block in content
                        if hasattr(block, "text")
                    )
                return str(content)
            return str(response)
        finally:
            if hasattr(self._copilot_client, "stop"):
                from copilot.client import StopError  # noqa: PLC0415
                with contextlib.suppress(StopError, Exception):
                    await self._copilot_client.stop()


# ---------------------------------------------------------------------------
# Internal sentinel
# ---------------------------------------------------------------------------


class _HumanReviewRequired(Exception):
    """Raised by the human_review node to interrupt graph execution."""

    def __init__(self, review_result: ReviewResult) -> None:
        super().__init__("Human review required.")
        self.review_result = review_result
