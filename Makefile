# Steward — developer workflow

.PHONY: help install-dev lint format typecheck imports test preservation lock secrets bandit gates clean

help:
	@echo "Steward developer commands:"
	@echo "  make install-dev    Install package + dev deps in editable mode"
	@echo "  make lint           Run ruff check"
	@echo "  make format         Run ruff format"
	@echo "  make typecheck      Run mypy strict"
	@echo "  make imports        Run import-linter (boundary contracts)"
	@echo "  make test           Run pytest (unit + integration)"
	@echo "  make preservation   Run preservation gate (merge-blocking)"
	@echo "  make lock           Regenerate requirements.lock via pip-compile"
	@echo "  make secrets        Run detect-secrets audit"
	@echo "  make bandit         Run bandit security scan on src/ (matches CI)"
	@echo "  make gates          Run every CI gate locally before push"
	@echo "  make clean          Remove __pycache__, .pytest_cache, .mypy_cache, .ruff_cache"

# Resolve `.venv/bin/<tool>` first; fall back to the host PATH so CI
# (which installs into the system interpreter) still finds them.
VENV_BIN := $(if $(wildcard .venv/bin/python),.venv/bin/,)

install-dev:
	$(VENV_BIN)pip install -e ".[dev]"

lint:
	$(VENV_BIN)ruff check src tests

format:
	$(VENV_BIN)ruff format src tests

typecheck:
	$(VENV_BIN)mypy src/steward

imports:
	$(VENV_BIN)lint-imports

test:
	$(VENV_BIN)pytest -m "not preservation"

preservation:
	$(VENV_BIN)pytest -m preservation

silent-catch:
	$(VENV_BIN)python scripts/lint-no-silent-catch.py src/steward/

lock:
	./scripts/regen-lock.sh

secrets:
	$(VENV_BIN)detect-secrets scan --baseline .secrets.baseline

# Exact CI invocation (kept in lockstep with .github/workflows/ci.yml).
# Reports Medium/High issues only; B101 (assert) is too noisy on a
# test-heavy codebase even with -x tests.
bandit:
	$(VENV_BIN)bandit -r src/steward/ -ll --skip B101 -x tests

# One-shot "did I break a gate" target before push.
gates: lint typecheck imports silent-catch test bandit
	@echo "[gates] all local gates passed."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
