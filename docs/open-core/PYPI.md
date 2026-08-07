# PyPI publish prep — `steward-fs`

**Status:** Prep complete; first upload **operator-gated** (PyPI account + token).  
**Package name:** `steward-fs`  
**CLI entry point:** `steward`  
**Import package:** `steward`  
**Source:** open extract from private `Cerid-AI/steward` via `scripts/export-open-core.sh`

## Preconditions

1. Public extract green: `scripts/export-open-core.sh --stage --verify`
2. `Cerid-AI/steward-open` main matches latest re-export
3. Version in staged `pyproject.toml` matches intended tag (private version is source)
4. Staged name is **`steward-fs`** (export rewrites private `name = "steward"`)
5. LICENSE Apache-2.0 present in stage
6. No private host paths in staged docs (export scrub)

## One-time PyPI setup (operator)

1. Create https://pypi.org project ownership for **`steward-fs`** (or claim name).
2. Create API token (scope: project `steward-fs` preferred).
3. Store as GitHub secret on private or open repo if automating:
   - `PYPI_API_TOKEN` (Trusted Publishing preferred when available)
4. Optional: TestPyPI first (`https://test.pypi.org`).

**Not automated in private CI yet** — avoids accidental public release on every tag.
Use the manual path below until Trusted Publishing is configured.

## Manual publish (from open extract)

```bash
# From private monorepo
scripts/export-open-core.sh --stage --verify
cd dist/open-core-stage
python -m pip install -U build twine
python -m build
# Inspect dist/*.whl dist/*.tar.gz
twine check dist/*
# TestPyPI first (recommended):
# twine upload --repository testpypi dist/*
twine upload dist/*   # requires PYPI token / ~/.pypirc
```

## Install check

```bash
pip install steward-fs
steward --version
python -c "import steward; print(steward.__file__)"
```

## Blockers (document, do not invent)

| Item | State |
|---|---|
| PyPI project `steward-fs` ownership | Operator creates / claims |
| Trusted Publishing (GitHub → PyPI) | Optional follow-on |
| `OPEN_CORE_DEPLOY_TOKEN` | Separate: pushes extract to steward-open (git), not PyPI |
| Private CI auto-upload on tag | Deferred until first manual release succeeds |

## Related

- [`docs/OPEN_CORE.md`](../OPEN_CORE.md) — product identity table  
- [`scripts/export-open-core.sh`](../../scripts/export-open-core.sh) — stage rewrites name to `steward-fs`  
- [`.github/workflows/open-core-publish.yml`](../../.github/workflows/open-core-publish.yml) — git publish to open repo  
