# AGENTS.md

Guidance for AI coding agents working in this repository.

## Environment Baseline

- Primary language/runtime: Python 3.14.
- Local development runs in the devcontainer defined by [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json).
- Neo4j is expected to be available at `bolt://localhost:7687` with env-driven credentials.

## Common Commands

- Verify core integrations (Neo4j + Copilot SDK runtime):
  - `.venv/bin/python src/test_env.py`
  - `.venv/bin/python pytest

## Scope Note

- This repository is early stage. Prefer incremental, easy-to-validate changes over large framework scaffolding.
