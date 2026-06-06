# User Feedback

This file contains requests, issues and design decisions made during development.

## Progress Indication & Costs

I just ran `protoproject ingest docs/vision.md` and waited several 10's of seconds with no response so I keyboard interrupted the run. It looks like the LLM was analysing which may be OK. We need feedback for the user so they know what is going on. Since token consumption is a worry does the copilot sdk provide any information on the cost of a request in credits? It would be good to feedback to the user that a request is in progress then the associate cost or cost proxy (num input and output characters).

Implementation notes:

- `protoproject ingest` now emits progress to stderr for the full Phase 1 pipeline: source read, LLM parse, embedding/model load, audit, Neo4j persistence, and similarity scan.
- During the Copilot parse step, the CLI reports immediately that a request is in progress and emits heartbeat-style wait feedback while the session is still running.
- The Copilot SDK does not expose a stable "credits" value. When the SDK emits usage events, ProtoProject reports the experimental USD `cost` plus token counts.
- When cost or token details are unavailable, ProtoProject still reports local character-count proxies for prompt and response sizes.
- Ctrl-C now exits the ingest command with code `130` without printing a full asyncio traceback.

## Progress Spam

Use a counter or progress bar or the like on a single line for items that are just being counted through. Lots of repeated log lines with an incrementing counter is irritating. For indeterminate waits (e.g. waiting for the LLM) a spinner or single line timer would work.
