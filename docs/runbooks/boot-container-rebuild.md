# Boot APFS container rebuild (Boot Camp–safe)

**Date:** 2026-08-18  
**Machine:** Mac Pro 7,1 (T2, Intel) · macOS 26.5.2  
**Do not erase the 1 TB APPLE SSD as a whole disk.**

---

## Why this exists

~450 GiB on **Level 00 - Data** is allocated but not enumerable (live `du` ≈ Recovery `du` ≈ 280 GiB).  
Deleting Data **only** breaks the System+Data volume group.  
The supported reclaim is: **erase the 868 GB APFS container** (`disk3s2` / Level 00 group), reinstall macOS, restore files.

---

## GPT on the Apple SSD (never touch slice 3)

```
APPLE SSD AP1024N  (~1.0 TB)     — physical; IDs change
├── s1  EFI                         315 MB
├── s2  Apple_APFS  868 GB          ← ERASE THIS CONTAINER ONLY
└── s3  Microsoft Basic Data        132 GB  BOOTCAMP (NTFS)  ← NEVER ERASE
```

In Disk Utility: **View → Show All Devices**.  
Select **Level 00** (volume group) or the **APFS Container** under the Apple SSD whose size is **~868 GB**.  
If the selected device is **1.0 TB / APPLE SSD AP1024N**, you are about to wipe Windows. **Cancel.**

---

## Already done (2026-08-18, live OS)

| Step | Result |
|------|--------|
| `~/Develop` → `/Volumes/Level 1/Develop` (APFS) + symlink | 60 GiB moved |
| Boot Data after delete of `Develop.boot-bak` | **664 GiB used / 129 GiB free (84%)** |
| dogcam LaunchAgent | **disabled**; RunAtLoad/KeepAlive false — stays down until you start it |
| Identity + Steward + agent configs → L2 | `…/boot-rebuild-backup-20260818T011741Z` |
| Home trees → `home/` on L2 | Documents, Desktop, Downloads, Pictures, Movies, Music, Mail, Messages, Containers, App Support, Prefs |
| L3a | identity pack present; `home/` mirror in progress |

**Develop is already off boot.** Reinstall will not delete Level 1.

---

## Backup pack contents (this folder) — verified 2026-08-18

| Path | Status |
|------|--------|
| `ssh/id_ed25519` | OK |
| `keychains/login.keychain-db` | OK |
| `steward/inventory.db` | OK |
| `weekly-latest.tar.xz` | OK |
| `home/Documents` | OK |
| `home/Pictures/Photos Library.photoslibrary` | OK |
| `home/Music/Music` | OK |
| `home/Library/Mail` | OK |
| `home/Library/Messages` | OK |

Skipped on purpose: Unix sockets, FileProvider tombstones (TCC), `Library/Caches`, 1Password agent fifos.

**L2 pack is enough to erase.** L3a is a second copy of identity + home (NAS).

---

## Recovery procedure (operator)

1. Apple menu → Restart → hold **⌘R** (Intel). Unlock FileVault.  
2. Utilities → Disk Utility → Show All Devices.  
3. Confirm **BOOTCAMP** still listed as sibling of the APFS container.  
4. Select **APFS container ~868 GB** (or Level 00 group). Erase: APFS, name **Level 00**.  
5. Quit Disk Utility. Install macOS onto **Level 00** (the new empty container).  
6. First boot: create user `sunrunner` (or match old short name). Enable FileVault; save recovery key **off this Mac**.  
7. Confirm `/Volumes/BOOTCAMP` exists (or hold Option at boot → Windows).  
8. Restore from this pack + any extra home copy.  
9. Recreate symlink:  
   `ln -s "/Volumes/Level 1/Develop" /Users/operator/Develop`  
10. Steward: point `STEWARD_DATA_DIR` at **Level 1 or Level 2**, not boot. Restore `inventory.db` from `steward/`.  
11. `fileproviderctl dump -l | grep 'error generation'` — stay **&lt; 10**.  
12. Do **not** run Boot Camp Assistant install/remove.

---

## After install — expected

- Data volume nearly empty except new user (~few GiB).  
- Ghost 450 GiB **gone** (new container).  
- Boot Camp partition unchanged.  
- `~/Develop` works via Level 1 symlink.

## Abort

- Disk Utility selection is the **1 TB Apple SSD**.  
- BOOTCAMP missing from the partition list before erase.  
- Backup pack missing `ssh/id_ed25519` or `steward/inventory.db`.
