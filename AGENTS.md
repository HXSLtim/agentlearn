# Repository Guidelines

## Project Structure & Module Organization

This repository contains two independently managed applications:

- `server/` — Python 3.14 backend and Textual TUI. Source code follows a one-way dependency flow: `src/domain` defines pure data, `src/application` contains use cases, `src/infrastructure` wraps external clients and storage, and `src/interfaces` contains TUI and HTTP entry points. `main.py` is the composition root.
- `app/` — Tauri desktop client. It communicates with the server over HTTP/SSE and does not share application code with `server/`.
- `docs/` — roadmap, architecture decisions, and learning path. `TODO.md` is the working queue.
- `server/data/` — runtime transcripts and databases; never commit it.

There is currently no dedicated `tests/` directory.

## Build, Test, and Development Commands

Run backend commands from `server/`:

```bash
uv run python main.py tui       # Start the terminal UI
uv run python main.py desktop   # Serve the desktop API on port 8000
uv run python -m compileall src # Byte-compile backend sources
uv run python -m py_compile main.py
```

Run frontend commands from `app/`:

```bash
npm install
npm run build        # Type-check TypeScript and build Vite assets
npm run dev          # Start the Vite development server
npm run tauri dev    # Start the desktop client
```

## Coding Style & Naming Conventions

- Python uses 4-space indentation, snake_case functions, and PascalCase classes.
- TypeScript uses the existing ES modules, camelCase functions, and PascalCase components/types.
- No formatter or lint command is configured; follow nearby style and keep changes minimal.
- Preserve the deliberate teaching comments in Python.
- Never let `domain` import from outer layers, and keep provider-specific protocol details in `src/infrastructure/adapter.py`.

## Testing Guidelines

There is no automated test suite yet. For every change, run the narrowest relevant checks, then the commands above. For UI changes, exercise both TUI and desktop where applicable and visually inspect a real 380×900 desktop screenshot plus a TUI screenshot before claiming completion.

## Commit & Pull Request Guidelines

History uses short imperative Chinese subjects such as `同步TUI模型下拉选择` and `支持两端会话导出Markdown`. Make one coherent feature or fix per commit. Pull requests need a concise behavior summary, verification performed, linked TODO item, and screenshots for UI changes.

## Security & Configuration

Copy `server/config.example.ini` to `server/config.ini`; the latter is ignored and must never be committed. Use `${ENVIRONMENT_VARIABLE}` references for API keys, do not print secrets, and keep runtime data out of Git.
