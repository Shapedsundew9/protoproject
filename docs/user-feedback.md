# User Feedback

This file contains requests, issues and design decisions made during development.

## Progress Indication & Costs

I just ran `protoproject ingest docs/vision.md` and waited several 10's of seconds with no response so I keyboard interrupted the run. It looks like the LLM was analysing which may be OK. We need feedback for the user so they know what is going on. Since token consumption is a worry does the copilot sdk provide any information on the cost of a request in credits? It would be good to feedback to the user that a request is in progress then the associate cost or cost proxy (num input and output characters).

## Progress Spam

Use a counter or progress bar or the like on a single line for items that are just being counted through. Lots of repeated log lines with an incrementing counter is irritating. For indeterminate waits (e.g. waiting for the LLM) a spinner or single line timer would work.

## Transcript

We need to add a --transcript CLI option to record what is going on behind the scenes i.e. what exactly is being sent to the LLM and what exactly its response was. These are crucial to understanding costs. The option should optionally take a file path or default to a transcript.log or similar.

## Pylint

Please add configuration I think in the toml file if possible. So that pylint does not require Docstrings in the tests.
