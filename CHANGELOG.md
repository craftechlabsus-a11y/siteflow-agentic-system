# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-19

### Added
- Initial release of `siteflow-agentic-system`: multi-specialty BIM/engineering agentic engine connected via MCP and Skills with Antigravity IDE and Claude.
- Core modules: `artifact_store`, `confidence`, `dlp_guard`, `llm_pool`, `project_graph`, `quota_switcher`, `router`, `skills_base`, `structured`.
- Deterministic engines: `bim_graph`, `cad_vision`, `docs_rag`, `planning_4d`.
- MCP Gateway (`src/mcp_gateway/server.py`) and Orchestrator (`dag_runner`, `project_connector`).
- Automated Semantic Versioning (Conventional Commits + GitHub Actions `python-semantic-release`).
