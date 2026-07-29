# Contributing to Steward open-core

## Source of truth

**Private monorepo `Cerid-AI/steward` is the sole source of truth** for code
until the Phase 2 package invert (public package as dependency).

This public repository (`Cerid-AI/steward-open`) is a **generated extract**
produced by `scripts/export-open-core.sh` and published via
`scripts/sync-steward-open.sh` / GitHub Actions.

| Do | Don't |
|---|---|
| Open **issues** here for public API bugs and docs | Expect long-lived feature branches on open alone |
| Send PRs for small docs/typo fixes (maintainers may re-export) | Develop features only on steward-open |
| Use private repo for lab adapters and field notes | Commit inventory data, host paths, or field notes |

## Public product identity

| Surface | Name |
|---|---|
| Public git repo | `Cerid-AI/steward-open` |
| Installable package (PyPI target) | **`steward-fs`** |
| Private dogfood monorepo | `Cerid-AI/steward` |

CLI entry point remains `steward` in both trees.

## Development workflow (maintainers)

```bash
# In private monorepo:
scripts/export-open-core.sh --stage --verify
OPEN_CORE_PUSH=1 scripts/sync-steward-open.sh
```

Or push a version tag / run the `Open-core publish` workflow.

## Safety / design rules (public contract)

- Operator-in-the-loop: destructive apply requires explicit dry-run or execute.
- MCP default mode is `plan` (ADR-0016); execute needs `write` + plan_token.
- No Cerid runtime dependency in open-core.
- `steward.core` must not import infra/cli (import-linter).
- macOS File Provider behaviour is documented, not guaranteed on Linux.
- Inventory databases and host field notes never ship in this extract.

## License

Apache-2.0.
