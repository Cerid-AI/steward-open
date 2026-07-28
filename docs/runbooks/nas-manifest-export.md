# Runbook — `nas_manifest` export (Backup tier)

## When

`steward policy plan` emits `nas_manifest` rows for read-only NAS
tiers (e.g. `/Volumes/Backup`). Steward **never** deletes on those
tiers directly.

## Apply

```bash
steward apply --manifest /tmp/plan.tsv --dry-run   # counts nas rows as applied
steward apply --manifest /tmp/plan.tsv --execute
```

On execute, each `nas_manifest` row is appended to:

```
$STEWARD_DATA_DIR/runs/<manifest_run_id>/nas_manifest.tsv
```

plus a short `README-nas-manifest.txt`. Audit action:
`nas_manifest_exported`.

## Operator handoff

1. Review the TSV (path, hash, size, rationale).
2. Delete on the NAS via DSM or SSH (outside Steward).
3. Re-scan: `steward scan --root /Volumes/Backup --resume`
4. Confirm claims: `steward inspect <hash>`

## Related

- `NAS_READONLY_TIERS` in `core/tiers.py`
- ADR-0009 pull-don't-push
