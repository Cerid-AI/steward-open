# USER ACTION REQUIRED — boot container erase

Live-OS prep is done. Nothing left that an agent can do without you at the keyboard.

## Do this when you are ready

1. Confirm Level 1 and Level 2 are mounted and you can open:
   - `/Volumes/Level 1/Develop` (repos)
   - `/Volumes/Level 2/boot-rebuild-backup-20260818T011741Z/ssh/id_ed25519`
   - `/Volumes/Level 2/boot-rebuild-backup-20260818T011741Z/steward/inventory.db`
2. Apple menu → Restart → hold **⌘R** until Recovery.
3. Unlock FileVault.
4. Disk Utility → **View → Show All Devices**.
5. Confirm **BOOTCAMP** is still a sibling partition.
6. Select the **~868 GB APFS container** (Level 00 group) — **not** the 1 TB APPLE SSD.
7. Erase that container as APFS, name **Level 00**.
8. Install macOS onto **Level 00**.
9. First boot: user `sunrunner`. Save FileVault recovery key **off this Mac**.
10. After login, restore from the L2 pack (see `BOOT-CONTAINER-REBUILD.md`). Recreate:
    `ln -s "/Volumes/Level 1/Develop" ~/Develop`

## Do not

- Erase the whole Apple SSD (destroys Boot Camp).
- Run Boot Camp Assistant remove/install.
- Expect `~/Develop` to survive on boot — it already lives on Level 1.

## Optional before you reboot

Reload trading-agent from **your** Terminal (sandbox could not):

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cerid.trading-agent.plist
```

Dogcam stays stopped until you start it.
