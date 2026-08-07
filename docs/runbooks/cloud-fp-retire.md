# Runbook — Cloud-FP `retire_direct` (Dropbox / iCloud)

## When to use

Retiring duplicates or reclaiming space on macOS File Provider tiers
where same-FS stash rename is wrong (ADR-0014 / ADR-0015).

## Preconditions

1. Inventory scanned and classified.
2. Run `steward fp status` — expect `cloud_retire_ready=yes` for cloud intent
   (external-drive layout is OK; residual Domains.plist “unlinked” is a warning).
3. FP **settled** — not mid-relocation / reindex (optional limited dump):
   ```bash
   fileproviderctl dump -l com.getdropbox.dropbox.fileprovider | head
   ```
4. Confirm account trash/version-history window (not always 30 days).
5. Prefer a small dry-run batch first.

For store/mount fork, conflict folders, and rescan order see
[`dropbox-rectification.md`](dropbox-rectification.md).

## Plan

```bash
# Example: retention policy that emits retire_direct for Dropbox rows,
# or a hand-built TSV with action=retire_direct.
steward policy plan --policy retention.yml --out /tmp/dropbox-retire.tsv

# ADR-0020: bucket by store/mount dual-presence before bulk cloud retire.
# Only plan-dual.tsv is cloud-safe (verify==unlink on mount). Never rewrite claims.
steward plans filter-dual-presence \
  --manifest /tmp/dropbox-retire.tsv \
  --out-dir /tmp/dropbox-retire-filtered \
  --intent cloud_retire
steward apply --manifest /tmp/dropbox-retire-filtered/plan-dual.tsv --dry-run
```

Sample posture without a plan: `steward fp dual-presence --sample 32` (or
`steward health show` dual_presence section).

## Execute (cloud-propagating — default)

```bash
# verify==unlink on mount (ADR-0015); gate layout health:
steward apply --manifest /tmp/dropbox-retire.tsv --execute --require-fp-healthy

# Bulk (trusted inventory hashes):
steward apply --manifest /tmp/dropbox-retire.tsv --execute --require-fp-healthy --skip-verify
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
