# ProtoProject Design Phases

To bring **ProtoProject** from a vision to an operational reality, the development lifecycle can be broken down into four distinct, evolutionary phases.

By prioritizing deterministic data handling (the "mechanical" layer) before layering on agentic AI behavior, we ensure stability, lower token costs, and a clear lineage of how requirements evolve from a chaotic transcript into structured implementation work packages.

Here is the proposed phased roadmap, focusing on high-level goals, user experiences, and data flows.

## Phase 1: The Bedrock (Ingestion & Mechanical Graph)

### Goal

Establish the core Neo4j database schema, deterministic validation rules, and the initial ingestion pipeline. This phase replaces AI ambiguity with rigid data structures, ensuring every requirement has a unique ID, state, and explicit relationship hooks before any heavy AI orchestration is introduced.

### User Experience

* **Input:** The user feeds a raw, unstructured text file (e.g., a meeting transcript or a brain-dump markdown file) into the system.
* **Interaction:** The user interacts via a simple CLI or basic interface to trigger an ingestion run. The Phase 1 CLI now reports live stage progress during long-running operations so the terminal does not appear frozen while the parser, embeddings, or Neo4j work is in flight.
* **Output:** The user receives a summary report showing that $X$ raw nodes were created, alongside a list of initial structural gaps (e.g., "Requirement Y has no parent"). When the LLM path is used, the summary also includes request telemetry such as experimental SDK cost, token counts, and prompt or response character counts.

### Data & Information Flow

1. **Raw Text $\rightarrow$ Parser:** Unstructured text is ingested. A lightweight, terse LLM call parses the text into flat, distinct requirement candidates.
2. **Parser $\rightarrow$ Mechanical Validator:** Python routines automatically assign unique IDs, timestamps, and origin metadata.
3. **Validator $\rightarrow$ Neo4j:** The structured nodes and baseline relationships (`is_child_of`, `depends_on`) are committed directly to Neo4j.
4. **Graph $\rightarrow$ Mechanical Auditor:** Non-AI Python scripts scan the graph for structural anomalies (orphaned nodes, cyclical dependencies) and flag them.

### Phase 1 Ingest Observability

Phase 1 ingest now treats user feedback as part of the core CLI contract rather than an afterthought:

* Progress is emitted to stderr for every major stage so plain-text summaries on stdout remain script-friendly.
* The Copilot parse step reports immediately when a request begins and surfaces SDK usage data after completion.
* The Copilot SDK currently exposes experimental USD `cost` values rather than a stable "credits" abstraction, so ProtoProject reports cost when present and falls back to tokens and character counts when it is not.
* The existing Textual review UI remains post-run in Phase 1; live feedback happens before the TUI launches.

```mermaid
flowchart LR
%%{init: {"theme":"dark","themeVariables":{"lineColor":"#7c8a9f","edgeLabelBackground":"#0f172a","primaryColor":"#1f2937","secondaryColor":"#111827","tertiaryColor":"#1e293b","borderColor":"#475569","textColor":"#e2e8f0"}}%%
    raw["Raw Ingestion"]
    parser[("Terse LLM Parser")]
    validator["Mechanical Validator"]
    neo4j["Neo4j Graph"]
    audit[("Structural Audit")]

    raw --> parser
    parser --> validator
    validator --> neo4j
    neo4j --> audit

    style raw fill:#111827,stroke:#475569,color:#e2e8f0
    style parser fill:#111827,stroke:#475569,color:#e2e8f0
    style validator fill:#111827,stroke:#475569,color:#e2e8f0
    style neo4j fill:#111827,stroke:#475569,color:#e2e8f0
    style audit fill:#111827,stroke:#475569,color:#e2e8f0
    linkStyle 3 stroke:#7c8a9f,stroke-dasharray: 4 4,opacity:0.8
```

---

## Phase 2: The Conversational Architect (Product Requirements & NASA Quality)

### Goal

Introduce LangGraph to orchestrate the refinement of Top-Level Product Requirements. The system transitions from a passive storage bin to an active design partner, auditing requirements against NASA’s high-quality criteria and guiding the user through resolving ambiguities.

### User Experience

* **Input:** A raw, ingested `RequirementRecord` in the `Draft` state (e.g. from the parser in Phase 1).
* **Interaction:** 
  * The system highlights a vague requirement (e.g., *"The system must be fast"*). It presents the specific NASA criteria violated (e.g., *Lack of Verifiability*).
  * **The "Concern Value" Toggle:** For each requirement, the user assigns a **Concern Value** (scale of 1-5). They flag critical paths (e.g., data privacy) as **High Concern** (demanding strict human sign-off) and infrastructure paths as **Low Concern** (granting AI autonomy).
  * If a high-severity issue is found, the system auto-escalates the Concern Value (defaulting to 4 or higher) to mandate human sign-off.
* **Output:** A stabilized, traceable Top-Level Product Requirements Graph.

### Data & Information Flow

1. **Neo4j $\rightarrow$ LangGraph Orchestrator:** LangGraph pulls a draft requirement node and its immediate neighbors (to evaluate context).
2. **Orchestrator $\rightarrow$ NASA Evaluator (AI Agent):** A specialized agent evaluates the requirement text against NASA quality parameters.
3. **NASA Evaluator $\rightarrow$ Refinement Suggester:** If issues are found, the evaluator generates a `RefinementProposal` containing the tightened text, adjusted concern values, and identified quality issues.
4. **Refinement Suggester $\rightarrow$ User UI (TUI):** If the requirement is High Concern (or has high-severity issues), LangGraph pauses the state machine and pushes a clarification/approval prompt to the user.
5. **User / AI Agent Decision $\rightarrow$ Neo4j:** The user's input or the AI's autonomous choice updates the requirement by creating a new version entry, setting the state (`Stabilized` or `Draft`), writing the `supersedes_id` link, and committing it to Neo4j.

### Phase 2 Refinement & State Management

The refinement engine operates as a state machine where requirements transition systematically based on quality validation and human supervision.

* **LangGraph Orchestration State Machine:** The LangGraph workflow coordinates the sequence: loading drafts, performing rule-based evaluation, proposing changes, awaiting human interaction when needed, and committing new requirement versions.
* **NASA Quality Check Rules:** The refinement engine runs deterministic checks defined in [quality.py](file:///workspaces/protoproject/src/protoproject/quality.py) to assess the following criteria:
  * **Length Check (`TOO_SHORT`):** Requirements must be at least 12 characters long to ensure they convey substantive meaning.
  * **Normative Modal Verbs (`NO_MODAL_VERB`):** Requirements must contain a modal verb (`must`, `shall`, `should`, or `will`) to signify a clear normative obligation.
  * **Vague Language Check (`VAGUE_LANGUAGE`):** Requirements are scanned for ambiguous terms (`fast`, `quickly`, `user-friendly`, `easily`, `robust`, `scalable`, `flexible`, `intuitive`, `seamless`) that cannot be verified objectively.
  * **Specificity Check (`LOW_SPECIFICITY`):** Requirements must have at least 4 words to ensure minimum detail.
* **Concern Value Lifecycle & Escalation:**
  * Concern values range from 1 to 5, serving as the threshold for human intervention.
  * If a requirement triggers a high-severity quality issue (e.g., `TOO_SHORT` or `NO_MODAL_VERB`), the concern value is automatically escalated (set to 4 or higher) to flag the requirement for human review.
  * **Low Concern (1-3):** Grants the AI system permission to resolve minor issues autonomously (e.g., minor wording corrections or template insertions) without pausing the loop.
  * **High Concern (4-5):** Pauses the execution flow, requiring the user to explicitly choose a proposal, edit the text, or manually sign off on the override.
* **Version Evolution & History Tracking:**
  * Changes do not overwrite the existing nodes in place. Instead, they create a new `RequirementRecord` with an incremented `version` counter (e.g., version 2).
  * A `supersedes_id` relationship (represented as a `[:SUPERSEDES]` relation in Neo4j) links the new requirement record to the old one.
  * The state field tracks the requirement lifecycle:
    * `Draft`: Initial ingested candidate.
    * `Under_Review`: Active evaluation state.
    * `Stabilized`: Passed all quality checks (or manually approved by the user).
    * `Superseded`: Replaced by a newer version in the history chain.

```mermaid
flowchart TD
%%{init: {"theme":"dark","themeVariables":{"lineColor":"#7c8a9f","edgeLabelBackground":"#0f172a","primaryColor":"#1f2937","secondaryColor":"#111827","tertiaryColor":"#1e293b","borderColor":"#475569","textColor":"#e2e8f0"}}%%
    neo4j[("Neo4j Database")]
    pull["Retrieve Draft Requirement"]
    eval["NASA Quality Evaluator"]
    decide{"Issues Detected?"}
    auto["Generate Refinement Proposal"]
    check_concern{"Concern & Severity Check"}
    human["Human-in-the-Loop Pause\n(CLI / TUI Review)"]
    apply["Apply Refinement\n(New Version & Link)"]
    stabilized["Mark as Stabilized"]

    neo4j --> pull
    pull --> eval
    eval --> decide
    decide -->|Yes| auto
    decide -->|No| stabilized
    auto --> check_concern
    check_concern -->|High Concern / Severity| human
    check_concern -->|Low Concern & Severity| apply
    human -->|User Approves / Modifies| apply
    apply --> neo4j
    stabilized --> neo4j

    style neo4j fill:#111827,stroke:#475569,color:#e2e8f0
    style pull fill:#111827,stroke:#475569,color:#e2e8f0
    style eval fill:#111827,stroke:#475569,color:#e2e8f0
    style decide fill:#111827,stroke:#475569,color:#e2e8f0
    style auto fill:#111827,stroke:#475569,color:#e2e8f0
    style check_concern fill:#111827,stroke:#475569,color:#e2e8f0
    style human fill:#111827,stroke:#475569,color:#e2e8f0
    style apply fill:#111827,stroke:#475569,color:#e2e8f0
    style stabilized fill:#111827,stroke:#475569,color:#e2e8f0
    linkStyle 8 stroke:#7c8a9f,stroke-dasharray: 4 4,opacity:0.8
    linkStyle 9 stroke:#7c8a9f,stroke-dasharray: 4 4,opacity:0.8
```

---

## Phase 3: The Multi-Layer Weaver (System Architecture & Design Elaboration)

### Goal

Enable the vertical layering of the graph. The system must now take stabilized Product Requirements and map them down to **System Architecture Requirements** (tech stacks, separation of concerns) and subsequently to **Design Requirements**.

### User Experience

* **Interaction:** The user watches the AI autonomously generate downstream architectural proposals for **Low Concern** domains. For **High Concern** domains, the AI presents architectural options (e.g., Dockerizing constraints) for selection.
* **The Feedback Loop:** If the AI discovers during the design mapping that a product requirement is technically infeasible, the user experiences a "Reverse-Iteration" event, where the system requests permission to supersede or modify the original product requirement.

### Data & Information Flow

1. **Product Graph Layer $\rightarrow$ Architecture Synthesis Agent:** LangGraph passes product constraints to an architecture agent.
2. **Architecture Agent $\rightarrow$ Neo4j:** New nodes are generated representing architectural decisions, linked via `maps_to` or `constrains` relationships to the parent product nodes.
3. **Conflict Detection $\rightarrow$ Reverse Flow:** If a technical bottleneck occurs, an information flow triggers *backward* up the graph, updating the parent product node status to "Under Revision" and alerting the user.

```mermaid
flowchart LR
%%{init: {"theme":"dark","themeVariables":{"lineColor":"#7c8a9f","edgeLabelBackground":"#0f172a","primaryColor":"#1f2937","secondaryColor":"#111827","tertiaryColor":"#1e293b","borderColor":"#475569","textColor":"#e2e8f0"}}%%
    product["Product Layer"]
    architecture["System Architecture Layer"]
    design["Design Layer"]

    product -->|Maps To| architecture
    architecture -->|Maps To| design
    design -.->|Infeasibility Feedback Loop| product

    style product fill:#111827,stroke:#475569,color:#e2e8f0
    style architecture fill:#111827,stroke:#475569,color:#e2e8f0
    style design fill:#111827,stroke:#475569,color:#e2e8f0
    linkStyle 2 stroke:#7c8a9f,stroke-dasharray: 4 4,opacity:0.8
```

---

## Phase 4: The Autonomous Expeditor (Implementation & Guardrails)

### Goal

The final aggregation of the graph into executable **Implementation Work Packages**. A secondary, independent "Oversight AI Agent" is introduced to govern autonomous changes, ensuring the core vision isn't diluted when low-impact adjustments are made.

### User Experience

* **Interaction:** The user clicks "Generate Work Packages" for a specific epic or the entire project.
* **Autonomous Execution:** For **Low Concern** segments, the system makes minor adjustments or detail completions entirely in the background. The user simply views an audit log of what the Oversight Agent approved.
* **Output:** A clean export of atomic, contextual markdown files or JSON payloads tailored for execution by implementers (or GitHub Copilot), complete with an immutable traceability chain back to the original transcript.

### Data & Information Flow

1. **Design Graph Layer $\rightarrow$ Work Package Aggregator:** The final design nodes are parsed mechanically to compile all necessary parent context.
2. **Proposed Graph Modifications $\rightarrow$ Oversight Agent:** If an implementation detail requires a minor change to a design node, the modification is routed to the Oversight Agent.
3. **Oversight Agent Decision Matrix:**

* *If Impact is Low & Parent Concern is Low:* The agent auto-approves and writes directly to Neo4j.
* *If Impact is High or Parent Concern is High:* The change is routed to the User Approval Queue.

1. **Final Graph State $\rightarrow$ Target Output:** The fully traced packages are compiled for GitHub Copilot ingestion.

---

## Summary of Phase Objectives

| Phase | Core Objective | Primary Tech Layer | User Experience Focus |
| --- | --- | --- | --- |
| **1. The Bedrock** | Ingestion & structural integrity | Python + Neo4j (Mechanical) | Seeing raw data turn into organized, predictable nodes. |
| **2. Conversational Architect** | Product Requirement quality | LangGraph + Terse LLMs | Defining the vision clearly and setting **Concern Values**. |
| **3. Multi-Layer Weaver** | Architecture & Design mapping | Hierarchical Graph Queries | Managing the push-and-pull between vision and technical reality. |
| **4. Autonomous Expeditor** | Implementation & Oversight | Multi-Agent Guardrails | Reviewing auto-approved changes and exporting clean work units. |
