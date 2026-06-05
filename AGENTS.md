# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project Focus

- Build Trussflow: a requirements-engine system that converts unstructured vision input into a traceable requirements graph.
- Treat [docs/vision.md](docs/vision.md) as the product and architecture source of truth.

## Environment Baseline

- Primary language/runtime: Python 3.14.
- Local development runs in the devcontainer defined by [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json).
- Neo4j is expected to be available at `bolt://localhost:7687` with env-driven credentials.

## Common Commands

- Create/update venv and dependencies:
  - `python3 -m venv .venv`
  - `.venv/bin/pip install --upgrade pip`
  - `.venv/bin/pip install -r requirements.txt`
- Verify core integrations (Neo4j + Copilot SDK runtime):
  - `.venv/bin/python src/test_env.py`
- List models available to the current Copilot token:
  - `.venv/bin/python src/list_models.py`

## Required Environment Variables

- Copilot token: `COPILOT_GITHUB_TOKEN` or `GITHUB_TOKEN`
- Neo4j settings (defaults are used when unset):
  - `NEO4J_URI`
  - `NEO4J_USERNAME`
  - `NEO4J_PASSWORD`

## Codebase Map

- [docs/vision.md](docs/vision.md): vision, requirements lifecycle, architectural constraints.
- [src/test_env.py](src/test_env.py): reference integration check for Neo4j and Copilot SDK.
- [src/list_models.py](src/list_models.py): reference async Copilot client usage.
- [.devcontainer/docker-compose.yml](.devcontainer/docker-compose.yml): service topology and default environment.

## Working Conventions

- Prefer small, deterministic checks before AI calls.
- Keep prompts and responses terse; avoid unnecessary token-heavy operations.
- Preserve async patterns used in existing scripts (`asyncio.run`, `async with`).
- Keep user-facing CLI output explicit and actionable when handling failures.

## Do / Don't

- Do verify environment connectivity before adding higher-level features.
- Do use environment variables for credentials and endpoints.
- Do align implementation choices with constraints in [docs/vision.md](docs/vision.md).
- Don't hardcode secrets or assume tokens are always present.
- Don't introduce tooling/runtime assumptions outside the current devcontainer baseline without updating docs.

## Scope Note

- This repository is early stage. Prefer incremental, easy-to-validate changes over large framework scaffolding.
