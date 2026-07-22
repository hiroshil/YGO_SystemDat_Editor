# `system.dat` engine analysis and registry `flcrc`

Static analysis based on the provided executable, decompile/disassembly, the `CARD_*.bin` tables, `card_nameeng.bin`, and the supplied crash dump. The executable was not run during analysis.

The conclusions below apply to the provided build; other builds or repacks must be checked again against their own addresses and logic.

## 1. Read/write path and sizes

Main routines:

```text
FUN_00483150  0x00483150  write system.dat path
FUN_004832C0  0x004832C0  read/decode/validate system.dat
FUN_0045CBC0  0x0045CBC0  prepend 8-byte identity before payload on write
```

Runtime payload observed in the dump:

```text
0x00A53CC0, length 0x1188
```

Layout:

```text
Encrypted system.dat               0x13C6 bytes
  outer checksum                   4 bytes
  encoded body                     0x13C2 bytes

Decoded image                      0x1190 bytes
  identity header                  8 bytes
  payload                          0x1188 bytes
```

Formula:

```text
(0x1190 / 8) * 9 + 4 = 0x13C6
```

## 2. 8 → 9 byte codec

```text
0x00439C00  modular exponentiation
0x00439C90  encode block stream
0x00439D80  decode block stream
0x00439E50  write outer checksum
0x00439EF0  verify outer checksum
0x00439FB0  encoder wrapper
0x00439FF0  decoder/check wrapper
```

Parameters:

```text
clear chunk       8 bytes
encoded chunk     9 bytes
modulus           0x12B = 299
encode exponent   5
decode exponent   0x35 = 53
```

For a clear byte at position `i` in a block:

```text
v9 = pow(clear[i], 5, 299)
packed |= v9 << i
```

The decoder extracts the corresponding 9-bit field and then:

```text
clear[i] = pow(v9, 53, 299) & 0xFF
```

The exponent pair is invertible over the full clear-byte domain `0..255`; the self-test checks all 256 values.

## 3. Outer checksum

Four-accumulator seed:

```text
83 ED 76 45
```

The routine subtracts each encoded byte starting at offset 4, distributes it by `index mod 4`, and wraps modulo 256. The four results are written to file offsets `0..3`.

The size passed into checksum calculation is the decoded size `0x1190`, even though the encrypted file length is `0x13C6`. The codec reproduces the build's exact behavior rather than assuming the checksum covers the full file.

## 4. Identity header and `flcrc` creation

Relevant routines:

```text
FUN_0045CA80  0x0045CA80  read/create registry flcrc
FUN_005C40B7  0x005C40B7  generate scalar from local/system time and timezone
FUN_0045CBC0  0x0045CBC0  attach identity to decoded save on write
```

When the registry already has `flcrc`:

1. the engine reads the `REG_BINARY`;
2. it requires size 13 bytes;
3. it checks the small checksum;
4. it decodes the 9-byte body into an 8-byte identity;
5. it stores the identity into object/runtime state.

When the registry does not yet have `flcrc`:

1. clear the identity buffer;
2. set byte 0 to `1`;
3. call the time-scalar routine and write the DWORD into bytes 4..7;
4. encode the identity 8 → 9 bytes;
5. calculate the 4-byte checksum;
6. write the 13-byte `flcrc` value to the registry.

No code path was found that uses disk serial, CPU ID, MAC address, or hardware fingerprint. `flcrc` is an identity token tied to registry/profile creation, not to machine hardware.

## 5. `flcrc` structure

```text
13-byte flcrc
  +0x00  checksum, 4 bytes
  +0x04  encoded identity body, 9 bytes
```

The encoded body is exactly the first 9-byte block of the encoded decoded-image, i.e. file offsets `4..12`.

The `flcrc` checksum uses the same seed:

```text
83 ED 76 45
```

but subtracts only the corresponding first four bytes of the body. The editor validates length and checksum before decoding.

## 6. Identity checker: exact bytes vs engine key

Reverse engineering of the checker shows that this build compares:

```text
identity[0]
*(uint32_t *)&identity[4]
```

The three bytes `identity[1..3]` do not participate in the branch decision. They are padding/state and are preserved verbatim.

So the engine acceptance condition is more accurately described as:

```text
save_header[0] == registry_identity[0]
and
u32(save_header + 4) == u32(registry_identity + 4)
```

Not necessarily:

```text
all_8_bytes_equal
```

The editor classifies:

- `EXACT`: all 8 bytes are identical;
- `ENGINE MATCH`: engine key matches, padding differs;
- `DIFFERENT`: engine key differs;
- `INVALID/MISSING`: registry value cannot be used.

This avoids overwriting the registry just because padding differs while the engine would still accept the save.

## 7. Read path and why saves get reset

`FUN_004832C0`:

1. reads the file;
2. verifies outer checksum / decodes;
3. reads the 8-byte identity header;
4. calls the checker against the registry identity;
5. copies the `0x1188` payload into runtime only if all steps succeed;
6. returns failure if decode/checker fails.

Higher-level initialization may create a default payload and write a new save after a read failure. Therefore copying `system.dat` into a profile with a different engine key can cause the file to be replaced by a default save.

Two valid solutions:

### Override the registry from the save

- keep the save header;
- encode that header into `flcrc`;
- back up the old value;
- write the `REG_BINARY` into the existing key;
- use `QueryValueEx` to read it back and compare byte-for-byte.

### Rebind the save to the registry

- validate/decode registry `flcrc`;
- replace the decoded 8-byte header;
- encode the full file;
- recompute the outer checksum;
- leave the payload unchanged.

There is no need to apply both.

## 8. Registry paths and virtualization

The strings/paths scanned by the editor include HKCU VirtualStore, HKCR VirtualStore, HKLM WOW6432Node, HKLM native, and the HKCU product path.

Multiple keys may coexist because of:

- a 32-bit process on 64-bit Windows;
- UAC registry virtualization;
- admin vs non-admin execution;
- leftover installs or repacks.

The editor does not create keys automatically because there is no proof that any particular key is active for an arbitrary repack. It only modifies the key/view explicitly chosen by the user and already found during scanning.

## 9. Payload footer and inner checksum

Routines:

```text
0x005BE0A0  copy signature
0x005BE0C0  validate magic/checksum
0x005BE100  write magic/checksum
0x005BE160  initialize default payload
```

Layout, offsets from the start of the payload:

```text
0x117A..0x1181  ASCII YUGIOH01
0x1182          uint16 magic 0xFBA5
0x1184          uint16 checksum
0x1186          uint16 trailing padding
```

Checksum:

1. magic must be `0xFBA5`;
2. sum `0x8C2` little-endian words from payload offset `0x0000..0x1183` modulo 65536;
3. checksum is the two's complement of that sum;
4. write it at `0x1184`.

Identity sits outside the payload, so rebinding identity does not require changing the inner checksum. The full file still needs to be re-encoded and assigned a new outer checksum.

## 10. Card collection

```text
payload +0x000A  stored total card count, uint16
payload +0x000C  card table
entry count       0x45B = 1115
entry size        2 bytes
byte size         0x8B6
last index        0x45A
```

`CARD_ID.bin` and `CARD_Pack.bin` are both `0x8B6` bytes long, confirming 1,115 `uint16` entries.

Entry format:

```text
bits  0..7   owned quantity
bits  8..9   deck counter A
bits 10..11  deck counter B
bits 12..13  deck counter C
bit  14      new/unseen
bit  15      unknown/reserved
```

The routine that increments owned count saturates the low byte at `0xFF` and increments the stored total. When the editor changes quantity, it preserves the high byte.

Namespace notes:

- internal save index must be `< 0x45B`;
- `0x8B6` is the byte length, not the maximum card ID;
- external Card IDs can be greater than `0x8B6`.

## 11. Card names and `.bin` tables

The verified engine name lookup has the form:

```text
name_ptr = card_nameeng_base + internal_index * 0x40
```

`card_nameeng.bin`:

- 64-byte records;
- NUL-terminated strings;
- direct indexing by internal card/save index;
- the supplied file has 1,318 complete records and 8 trailing zero bytes;
- the editor ignores the incomplete tail and uses the first 1,115 records.

`CARD_ID.bin`:

```text
internal index -> external Card ID
```

`CARD_Pack.bin`:

```text
internal index -> raw pack/category word
```

`CARD_IntID.bin` is a reverse lookup used by the engine; `CARD_IndxENG.bin` is other English metadata/index data with a different format from the name records. Neither is required for the name column or save-table editing.

## 12. Confirmation from the crash dump

One complete encrypted buffer was found at process VA `0x00C9D0B8`:

```text
size                 0x13C6
SHA-256              de1d735625dedc50db37379f262e07aac33a810d72a849d4835318bb704f112a
outer checksum       valid
inner magic          valid
inner checksum       valid
signature            valid
exact decode/encode  byte-identical
identity header      01 00 00 00 EA 49 5F 6A
matching flcrc       82 ED 76 45 01 00 00 00 80 46 1E 09 2A
stored card total    43
calculated total     43
```

The independent runtime payload at `0x00A53CC0` also confirms the signature, magic, checksum, and card total.

The separate process-memory buffer was used only for internal testing and is not packaged.

## 13. Fields without sufficient semantic proof yet

- payload `+0x0006`: options bitfield; initializer often writes `0x00FF`;
- payload `+0x0008`: display/window field; initializer often writes `0x0010`;
- area around `0x10ED..0x10F4`: gameplay/difficulty flags;
- from `0x10F8`: packed counters with a helper that splits fields 11/11/10 bits;
- card bit 15 and pack-word semantics remain unnamed.

The editor only exposes them in raw view or shows raw values; it does not infer gameplay labels automatically.

## 14. Implemented safe-write rules

- preserve the identity header during normal payload edits;
- do not zero unknown regions;
- preserve card high bits when changing quantity;
- recompute stored total;
- write magic/signature/checksum when the options are enabled;
- encode and compute outer checksum;
- atomically replace the file and back up the old one;
- back up the registry before override;
- verify registry writes by reading back;
- do not create registry keys automatically;
- keep path configuration separate from save data.

## 15. Validation scope

The codec, parser, and GUI were checked with unit tests and GUI smoke tests. The Winreg API exists only on Windows; scan/backup/write/read-back logic has been checked statically, but choosing the active key and confirming write permissions must be validated on the machine that actually runs the game.
