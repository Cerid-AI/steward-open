# Runbook — Cloud-FP `retire_direct` (Dropbox / iCloud)

## When to use

Retiring duplicates or reclaiming space on macOS File Provider tiers
where same-FS stash rename is wrong (ADR-0014 / ADR-0015).

## Preconditions

1. Inventory scanned and classified.
2. FP **settled** — not mid-relocation / reindex:
   ```bash
   fileproviderctl dump -l <provider> | grep -E 'reconciliation|pending-indexable'
   ```
3. Confirm account trash/version-history window (not always 30 days).
4. Prefer a small dry-run batch first.

## Plan

```bash
# Example: retention policy that emits retire_direct for Dropbox rows,
# or a hand-built TSV with action=retire_direct.
steward policy plan --policy retention.yml --out /tmp/dropbox-retire.tsv
steward apply --manifest /tmp/dropbox-retire.tsv --dry-run
```

## Execute (cloud-propagating — default)

```bash
# Verifies on store path; unlinks on ~/Library/CloudStorage/Dropbox/...
steward apply --manifest /tmp/dropbox-retire.tsv --execute

# Bulk (trusted inventory hashes):
steward apply --manifest /tmp/dropbox-retire.tsv --execute --skip-verify
```

## Execute (local-only reclaim)

```bash
# Unlinks claim/store path. Cloud trash / quota NOT guaranteed.
steward apply --manifest /tmp/dropbox-retire.tsv --execute --allow-store-path-unlink
```

## Aftercare

- Spot-check cloud trash on dropbox.com for a sample path.
- `steward status` / `steward stash list` (stash unused for retire_direct).
- Re-scan if claims should reflect post-delete reality:
  `steward scan --root <root> --resume`

## Congestion / timeouts

- Rows that hit `Errno 60` are **deferred** (`FP unavailable`); re-run
  the same manifest later — already-deleted paths error as missing.
- Keep Mac awake: `caffeinate -dims &`

## Related

- ADR-0014, ADR-0015
- `docs/field-notes-2026-07-13-fp-cleanup.md`
