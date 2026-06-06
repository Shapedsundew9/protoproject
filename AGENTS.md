# AGENTS.md

Guidance for AI coding agents working in this repository.

## Environment Baseline

- Primary language/runtime: Python 3.14.
- Local development runs in the devcontainer defined by [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json).
- Neo4j is expected to be available at `bolt://localhost:7687` with env-driven credentials.

## Common Commands

- Before executing any python commands
  - `source .venv/bin/activate`
- Verify core integrations (Neo4j + Copilot SDK runtime):
  - `python src/test_env.py`
- Use `pytest` for validations

## Graphify

**ALWAYS** Before answering architecture or codebase questions, read `graphify-out/GRAPH_REPORT.md` for god nodes and community structure

## Scope Note

- This repository is early stage. Prefer incremental, easy-to-validate changes over large framework scaffolding.
