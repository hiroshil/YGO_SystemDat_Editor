# YGO `system.dat` Editor 1.0

A Python/Tkinter tool to decode, inspect, edit, and re-encode `system.dat` for the analyzed executable.

The tool uses only the Python standard library. No external packages are required.

## 1. Quick start on Windows

1. Install Python 3.10 or later and enable the **Tcl/Tk and IDLE** component.
2. Extract the **entire** package into one folder.
3. Fully exit the game.
4. Run:

```bat
RUN_EDITOR.bat
```

or:

```bat
py -3 ygo_system_dat_editor.py
```

5. Open the **Data files** tab to verify paths.
6. Open `system.dat`.
7. Check the **Summary** tab. Outer checksum, inner magic, inner checksum, and signature should all be `OK`.
8. Before moving a save between machines or profiles, open **Registry / Identity** and read sections 3–5 below.

The editor always creates a backup when overwriting a save:

```text
system.dat.bak-YYYYMMDD-HHMMSS
```

Always keep a copy outside the game folder before testing.

---

## 2. Is `flcrc` tied to machine hardware?

No. In the analyzed build's code path, `flcrc` does **not** use the disk serial, CPU ID, MAC address, machine SID, or any hardware fingerprint.

It is an identity token generated from the registry/profile when the game does not yet have an `flcrc` value:

```text
clear identity, 8 bytes
  byte 0       type/version, commonly 01
  bytes 1..3   preserved padding
  bytes 4..7   DWORD generated from local time at initialization

registry flcrc, 13 bytes
  bytes 0..3   dedicated checksum
  bytes 4..12  9-byte encoded identity
```

When the registry does not yet contain `flcrc`, the engine:

1. creates an 8-byte identity;
2. sets the first byte to `1`;
3. generates the final DWORD from a local-time routine;
4. encodes the identity into 9 bytes;
5. prepends a 4-byte checksum to produce a 13-byte `flcrc`;
6. writes `flcrc` to the registry;
7. uses the same identity as the first 8 decoded bytes of `system.dat`.

So the token is not hardware-dependent, but it can differ between:

- two machines;
- two Windows users/profiles;
- two installs or two independent registry hives;
- admin and non-admin runs if Windows virtualization redirects the game to different keys;
- repacks/mods using a different registry path.

---

## 3. What does the engine actually compare?

The identity checker routine in this build does not require all 8 bytes to match exactly. It compares:

```text
identity byte 0
identity DWORD at bytes 4..7
```

Bytes `1..3` are padding and are not part of the save-acceptance decision in the analyzed routine. The editor still preserves them.

The **Registry / Identity** tab shows one of these states:

| State | Meaning |
|---|---|
| `EXACT` | The full 8-byte identity decoded from the registry matches the save header exactly. |
| `ENGINE MATCH` | Byte 0 and the DWORD at bytes 4..7 match; only padding bytes 1..3 differ. This build still accepts it. |
| `DIFFERENT` | The engine identity key differs. The save may be rejected and the game may initialize a new `system.dat`. |
| `INVALID` | The value is not a valid 13-byte `flcrc` or its checksum is wrong. |
| `MISSING` | The key exists but has no `flcrc` value yet. |
| `UNCHECKED` | No save has been opened for comparison yet. |

So you do not need to override the registry when the state is already `EXACT` or `ENGINE MATCH`.

---

## 4. Why does copying only `system.dat` to another machine make the game recreate it?

Decoded `system.dat` starts with an 8-byte identity. The registry stores the corresponding identity in `flcrc`.

The engine read path:

1. reads the encrypted file;
2. checks the outer checksum and decodes it;
3. reads the first 8-byte identity;
4. compares the identity key against the registry;
5. copies gameplay payload into runtime memory only if the checks succeed.

If the save and the registry are `DIFFERENT`, the read path returns failure. The upper initialization layer may then create a default payload and write a new `system.dat`. That is why copying **only the save file** may not be enough.

You need to synchronize using **one** of the two workflows below. Do not do both.

---

## 5. Two correct ways to migrate a save

### Method A — Override the registry from the save

Use this when you want to keep the identity already embedded in the transferred save.

1. Exit the game.
2. Back up the current save on the target machine.
3. Copy the `system.dat` you want to transfer into the correct game/save folder.
4. Open that file in the editor.
5. Open **Registry / Identity**.
6. Click **Scan registry**.
7. Select the correct key/view used by the game.
8. If the state is `DIFFERENT`, click **Override selected registry from save**.
9. The editor backs up the old `flcrc`, writes the new value, then re-reads it for verification.
10. Scan again. The state should become `EXACT`.
11. Launch the game.

The editor does not create new registry keys automatically. It only writes to an existing key to reduce the chance of writing to the wrong product/path.

Registry backups are stored at:

```text
%APPDATA%\YGOSystemDatEditor\registry_backups\
```

Each write produces:

- a `.bin` file containing the old value, if the value existed;
- a `.txt` file recording the root, key path, registry view, and hex value.

To restore a `.bin` backup, scan the registry, select the exact same **root/path/view**, then use
**Import flcrc.bin to selected registry**. The editor verifies 13-byte length, checksum,
backs up the current value, and performs read-back verification after writing. The `.bin` file is a raw
13-byte `REG_BINARY`, not a `.reg` file, and it should not be opened with Registry Editor.

If the backup metadata says `flcrc=MISSING`, the value did not exist before that operation, so there is
no `.bin` file to import. To roll back exactly to the missing-value state, you must delete `flcrc`
manually in Regedit at the correct root/path/view, and only after checking the `.txt` file.

### Method B — Rebind the save to the target machine's registry

Use this when you want to keep the identity already present on the target machine/profile.

1. Exit the game.
2. Open the save file you want to transfer.
3. Go to **Registry / Identity** and click **Scan registry**.
4. Select the key/view containing the valid `flcrc` currently used by the game.
5. Click **Rebind save to selected registry**.
6. The editor replaces the in-memory 8-byte identity header with the identity decoded from the registry.
7. Click **Save** to re-encode and write the file.
8. Scan/compare again. The state should become `EXACT`.
9. Launch the game.

The gameplay payload and inner checksum do not contain the identity; rebind only changes the header outside the payload. The save is still re-encoded and the outer checksum is recomputed.

### Do not do both

- Method A keeps the save identity and changes the registry.
- Method B keeps the registry identity and changes the save.

These solve the same mismatch. Choose one based on which identity you want to preserve.

### If the game has already overwritten the transferred save

1. Exit the game immediately.
2. Do not continue using the new file as the source.
3. Restore the original save from backup.
4. Synchronize identity before launching the game again.

---

## 6. Registry paths and Windows virtualization

The editor scans these common locations:

```text
HKCU\Software\Classes\VirtualStore\MACHINE\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKCR\VirtualStore\MACHINE\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKLM\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKLM\SOFTWARE\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKCU\Software\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
```

The editor tries default, 32-bit, and 64-bit registry views when the Windows API supports them.

You may see multiple rows because of:

- a 32-bit game on 64-bit Windows;
- UAC registry virtualization;
- the game having been run both as admin and non-admin;
- old leftover keys;
- repacks creating multiple paths.

How to choose the correct key:

1. prefer a key that already has an `flcrc` value;
2. compare its state against a save that the game currently accepts;
3. check which key changes after the game creates a new save;
4. use the same privilege level as the game when needed;
5. do not write blindly to every key.

Writing to HKLM may require Administrator rights. The editor reports an error if Windows denies access; it does not bypass permissions.

---

## 7. **Data files** tab: which files should be loaded and why?

| File | Role in the editor | Required? | Structure used |
|---|---|---:|---|
| `card_nameeng.bin` | Shows the **Card name** column and enables name search. | Recommended | Fixed-size `0x40`-byte record by internal index. |
| `CARD_ID.bin` | Shows external Card ID in hex/decimal; helps skip sentinel `0000/FFFF` during bulk edit. | No | `0x45B` little-endian `uint16`; total length `0x8B6`. |
| `system.dat` | Default save for quick open. | No | Encrypted `0x13C6`, decoded `0x1190`, or payload `0x1188`. |

### `card_nameeng.bin`

The engine name lookup uses:

```text
record_address = base + internal_index * 0x40
```

Each record is a NUL-terminated name string within a 64-byte region. The editor decodes using Windows-1252 (`cp1252`) and maps directly by the internal index from the save table.

The supplied file is `0x14988` bytes long, containing 1,318 full records and 8 trailing zero bytes. The editor deliberately ignores the incomplete tail. The save only has `0x45B = 1115` entries, so the editor uses the first 1,115 records.

Example:

```text
index 0x0000  blank
index 0x0001  Earthbound Spirit
index 0x0002  Felgrand Dragon
```

### Path configuration

The **Data files** tab lets you:

- select each file individually;
- choose one folder and auto-fill any detected files;
- auto-detect beside the script, the current working directory, and the save folder;
- reload/validate each file;
- save configuration at:

```text
%APPDATA%\YGOSystemDatEditor\config.json
```

The `.bin` files do not need to live next to the script as long as the paths are configured correctly.

---

## 8. `system.dat` format

### Sizes

```text
Encrypted system.dat               0x13C6 bytes
  outer checksum                   4 bytes
  encoded body                     0x13C2 bytes

Decoded image                      0x1190 bytes
  identity header                  8 bytes
  payload                          0x1188 bytes
```

Encoding formula:

```text
(0x1190 / 8) * 9 + 4 = 0x13C6
```

The editor also accepts:

- decoded image `0x1190` bytes;
- payload-only `0x1188` bytes.

Payload-only has no identity header. The editor creates a zero header so the data can be analyzed, but that file must be rebound or synchronized with `flcrc` before the game will accept it.

### 8 → 9 byte codec

```text
clear chunk       8 bytes
encoded chunk     9 bytes
modulus           299 (0x12B)
encode exponent   5
decode exponent   53 (0x35)
```

Each clear byte is modular-exponentiated into a 9-bit value, shifted according to position 0..7, and packed into 9 bytes. The decoder extracts each 9-bit value and applies exponent 53 modulo 299.

### Outer checksum

Seed:

```text
83 ED 76 45
```

The engine distributes encoded bytes across four accumulators by `offset mod 4` and subtracts modulo 256. The four checksum bytes are stored at the start of the file.

### Payload footer

Offsets relative to the start of the payload:

```text
0x117A..0x1181  ASCII "YUGIOH01"
0x1182          uint16 magic 0xFBA5
0x1184          uint16 inner checksum
0x1186          uint16 trailing padding
```

The inner checksum is the 16-bit two's complement of the sum of the first `0x8C2` words of the payload, with the magic value set correctly.

---

## 9. Card table in the save

```text
payload +0x000A  stored total card count, uint16
payload +0x000C  card table
entry count       0x45B = 1115
entry size        2 bytes
last index        0x45A
byte size         0x8B6
```

Important distinction:

- `0x8B6` is the **byte size** of the table, not the maximum card ID;
- valid internal indexes are `0x0000..0x045A`;
- external Card IDs may be larger than `0x8B6` because that is a different namespace.

Each `uint16` entry:

```text
bits  0..7   owned quantity, 0..255
bits  8..9   deck counter A
bits 10..11  deck counter B
bits 12..13  deck counter C
bit  14      new/unseen flag
bit  15      not yet identified
```

When editing quantity, the editor changes only the low byte and preserves deck counters, bit 14, and bit 15. When editing `New`, the editor changes only bit 14.

Bulk operations can skip external IDs `0000` and `FFFF`. This option is most useful when `CARD_ID.bin` has been loaded.

---

## 10. GUI tabs

### Summary

Shows:

- source format and size;
- outer checksum;
- inner magic/checksum;
- signature;
- exact decode/encode round-trip;
- 8-byte identity header;
- `flcrc` derived from the save;
- engine identity key;
- stored/calculated card total;
- data-file status.

### Cards

- search by name, internal index, external ID hex, or decimal;
- show name, ID, owned count, deck counters, flags, and raw value;
- edit individual cards;
- bulk-set owned count;
- mark/clear `new`;
- recalculate total.

### Registry / Identity

- scan standard keys;
- validate `flcrc` length/checksum;
- decode identity;
- classify `EXACT`, `ENGINE MATCH`, `DIFFERENT`;
- bulk-override currently multi-selected registry rows using the save's `flcrc`, with backup + read-back verify;
- import one `flcrc.bin` into multiple registry rows at once;
- rebind the save using exactly one selected registry row;
- export `flcrc.bin` from the open save.

### Data files

Configure individual paths, auto-detect, validate, and save `config.json`.

### Raw fields

Read/write payload by offset. Use this for fields that do not yet have enough semantic evidence. Raw writes can still break saves even if checksums are recomputed; use only if you understand the layout.

### Log

Records file open/save, data-file loading, registry scan/write, backup, and detailed errors.

---

## 11. Safe card-editing workflow

1. Exit the game.
2. Back up `system.dat` and registry `flcrc`.
3. Open the current encrypted `system.dat` so the identity header is preserved.
4. Check **Summary**.
5. Load `card_nameeng.bin`; load `CARD_ID.bin` if you need external ID / sentinel filtering.
6. Edit cards.
7. Keep these three default Save options enabled:
   - Repair inner checksum/magic;
   - Ensure `YUGIOH01` signature;
   - Recompute stored card total.
8. Save.
9. Re-check **Summary**.
10. Registry changes are only needed if the state is `DIFFERENT` or if you intentionally rebind identity.
11. Launch the game.

Normal card editing does not change identity and does not change `flcrc`.

---

## 12. CLI

```bat
py -3 ygo_system_dat_editor.py --self-test
py -3 ygo_system_dat_editor.py --info system.dat
py -3 ygo_system_dat_editor.py --decode system.dat system.decoded.bin
py -3 ygo_system_dat_editor.py --encode system.decoded.bin system.new.dat
```

`--info` prints the header, engine identity key, derived `flcrc`, checksums, and card total.

---

## 13. Troubleshooting

### The game still creates a new `system.dat`

Check the **Registry / Identity** tab first. If the state is `DIFFERENT`, choose one of these two paths; do not do both:

- multi-select the correct target registry rows and use **Override selected registry rows from save**; or
- select exactly one row and use **Rebind save to selected registry**, then save again.

Check in this order:

1. whether the file is in the actual path the game reads;
2. whether the game was still running during copy/save;
3. whether **Summary** shows valid checksum/magic/signature;
4. whether **Registry / Identity** is `DIFFERENT`;
5. whether the correct registry root/path/view was selected;
6. whether the game is running as admin or non-admin;
7. whether VirtualStore contains a different key;
8. whether the save was already overwritten before synchronization;
9. whether a repack/mod changed the registry path or format.

### Card names do not appear

- select the correct `card_nameeng.bin`;
- do not accidentally select `CARD_IndxENG.bin` instead;
- check the `Loaded ... names` status in **Data files**;
- verify the file matches the build;
- reload data files.

### Names exist but IDs do not match

Names come from `card_nameeng.bin` by internal index; IDs come from `CARD_ID.bin`. These are different namespaces. External Card ID is not the save-table index.

Names and external IDs come from two separate files. `card_nameeng.bin` and `CARD_ID.bin` must come from the same build/data set. If versions are mixed, the same internal index may point to different names and IDs.

### Cannot write to the registry

- run the editor with the appropriate privilege level;
- choose the HKCU/VirtualStore key if that is what the game actually uses;
- HKLM usually requires Administrator;
- the editor does not create new keys automatically;
- check **Log** and the backup folder.

### `ENGINE MATCH` but not `EXACT`

The three padding bytes differ. For the reverse-engineered checker in this build, that is still valid. No override is needed just to turn it into `EXACT`, unless you are studying a different build with different checker logic.

### Payload-only is rejected

Payload-only does not contain a real identity. Rebind it to the registry first, save it as encrypted `system.dat`, then verify the match. The editor warns when saving a source without an encrypted identity baseline; do not ignore that warning.

### Restoring the old `flcrc`

1. Open the `.txt` file in `%APPDATA%\YGOSystemDatEditor\registry_backups` to identify the root/path/view.
2. Scan the registry and select the matching row.
3. Click **Import flcrc.bin to selected registry** and choose the `.bin` file with the same timestamp.
4. Check that the dialog shows the correct target and the expected match state against the currently open save.
5. After writing, the editor automatically reads back for verification and rescans the table.

Do not import a backup from one key/view into another key/view just because the game name looks the same.

---

## 14. Testing and validation

- the editor starts with only `card_nameeng.bin` present;
- the registry list uses `selectmode=extended`;
- batch override/import presents a multi-row preview and still forces rebind to single-select.

The package includes unit tests for:

- modular codec and full save round-trip;
- outer checksum;
- inner magic/checksum/signature;
- `flcrc` encode/decode/checksum;
- exact match and engine-key match when padding differs;
- payload-only handling;
- `CARD_ID.bin` size;
- `card_nameeng.bin` parser and the first table names.

Run:

```bat
py -3 -m unittest -v
```

---

## 15. Limits and scope

- The analysis applies to the provided executable/build; a different repack may change the layout/path/checker.
- Registry write/read-back code only runs on Windows. The package has been tested for GUI/codec on Linux with Xvfb, but real `winreg` operations must be verified on the user's Windows machine.
- Some payload fields near the end of the file still lack sufficient evidence; the editor does not auto-label or auto-edit them.
- A valid checksum does not guarantee that every raw value is gameplay-valid.

See `ENGINE_ANALYSIS_VI.md` for routine addresses, codec details, and technical evidence.
