# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] — 2026-08-05

### Changed

- **BREAKING**: Rewrite in Python 3.13. Replaces the TypeScript/Node.js implementation entirely.
- Install path is now `uv tool install git+https://github.com/pythoninthegrass/mcp-retroarch`
  or `uvx --from git+https://github.com/pythoninthegrass/mcp-retroarch mcp-retroarch`.
  The previous `npm install -g github:...` path is gone.
- Wire protocol, tool names, tool arguments, environment variables, and tool description
  strings are unchanged from the TypeScript version.

### Removed

- Node.js runtime dependency.
- All npm packaging (`package.json`, `package-lock.json`, `dist/`, `tsconfig.json`).
- `.github/workflows/` (CI not yet ported to Python toolchain).

### Added

- `pyproject.toml` with `hatchling` build backend, `mcp>=1.12,<2` dependency.
- `ruff.toml` for formatting and linting.
- `tests/test_retroarch.py` — NCI client unit tests (no equivalent existed in the TypeScript version).
- `Dockerfile` on `python:3.13-slim`.

### Motivation

`npm install -g github:...` produces a broken symlink on most systems due to a known npm bug
(temp clone → symlink → cleanup leaves the symlink dangling). `uv tool install git+...`
handles this correctly with no workarounds required.
