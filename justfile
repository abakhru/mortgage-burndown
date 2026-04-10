# Mortgage burndown — https://github.com/casey/just
set shell := ["bash", "-euo", "pipefail", "-c"]

# Show recipes
default:
    @just --list

# Install deps from uv.lock into .venv
install:
    uv sync

# Run the web app (http://127.0.0.1:5000)
run:
    uv run python -m mortgage_burndown

alias dev := run

# Ruff lint (no writes)
lint:
    uv run ruff check src

# Ruff lint with auto-fix
fix:
    uv run ruff check --fix src

# Byte-compile and import smoke test
check:
    uv run python -m compileall -q src
    uv run python -c "from mortgage_burndown.app import app; from mortgage_burndown.mortgage import calculate_mortgage; print('ok')"

# Refresh uv.lock from pyproject.toml and sync
lock:
    uv lock
    uv sync

# Upgrade locked deps to latest allowed by pyproject, then sync
upgrade:
    uv lock --upgrade
    uv sync

# Remove Python cache dirs (skips .venv)
clean:
    find . -name __pycache__ -type d -not -path './.venv/*' | while IFS= read -r d; do rm -rf "$d"; done
    find . -name '*.py[co]' -not -path './.venv/*' -delete
