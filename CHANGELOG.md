# Changelog

All notable changes to ROOT are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.1.0] — 2026-06-02

### Added

- **`rootd.py`** — Warm ROOT daemon. Keeps the database and embedder loaded in memory so searches return instantly without cold-start latency. Exposes a minimal localhost HTTP API (`GET /health`, `GET /search`) for fast agent-to-agent queries, with zero LLM calls on the hot path.

- **`query.py`** — Agent-callable CLI for the full ROOT tool surface. Lets any external process (shell scripts, other AI agents, cron jobs) invoke ROOT tools via `python query.py <tool> [args]` without standing up the MCP server. Supports: `search`, `open_loops`, `themes`, `blind_spots`, `weekly_digest`, `decision_trail`, `project_pulse`.

- **`health-check.py`** — Operational health check for ROOT deployments. Validates that the indexer is running, the database is not stale, background crons are firing, and the MCP server is reachable. Designed to run as a daily cron job and catch silent failures before they go unnoticed.

- **`skill-audit.py`** — Audits which ROOT MCP tools are actually being invoked. Surfaces underused tools and identifies gaps in how your knowledge graph is being queried, so you can tune extraction and indexing priorities.

- **`slack-alert.py`** — Sends ROOT health and digest alerts to a Slack webhook. Pairs with `health-check.py` for teams or individuals who want operational visibility without polling logs manually.

- **`run-indexer.sh`** — Convenience shell wrapper for the indexer. Activates the venv, runs incremental indexing with entity extraction, and logs output in a format compatible with launchd and cron. Useful as a drop-in replacement for the raw `python indexer.py` invocation in launchd plists.

### Changed

- **`llm.py`** — Multi-backend LLM client rebuilt. Anthropic and OpenRouter backends are now first-class and feature-equivalent. Extraction and synthesis are independently configurable per backend. Nested Claude environment variables are stripped before subprocess calls to prevent token leakage in agentic setups. OpenRouter's OpenAI-compatible tool schema is fully supported.

- **`indexer.py`** — Added an LLM pause guard: if a `.llm-paused` sentinel file exists in the project root (optionally containing a `reset_epoch` timestamp), entity extraction is skipped gracefully without killing the indexing run. Useful for rate-limit management and cost control in automated deployments.

- **`embeddings.py`** — Embedder initialization hardened; model loading errors now surface earlier with a clearer message rather than failing silently during the first batch.

- **`server.py`** — MCP server stability improvements. Tool handler errors are now caught at the transport layer and returned as structured error responses rather than crashing the server process.

- **`db.py`** — Minor schema query optimizations and additional defensive checks on write paths.

---

## [1.0.0] — 2026-03-21

Initial release. Personal knowledge graph with entity extraction, GraphRAG traversal, semantic search, and MCP integration for Claude Code.
