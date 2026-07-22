# Phân tích engine `system.dat` và registry `flcrc`

Phân tích tĩnh dựa trên executable, decompile/disassembly, các bảng `CARD_*.bin`, `card_nameeng.bin` và crash dump được cung cấp. Executable không được chạy trong quá trình phân tích.

Các kết luận dưới đây áp dụng cho build đã cung cấp; build/repack khác cần đối chiếu lại địa chỉ và logic.

## 1. Đường đọc/ghi và kích thước

Các routine chính:

```text
FUN_00483150  0x00483150  write system.dat path
FUN_004832C0  0x004832C0  read/decode/validate system.dat
FUN_0045CBC0  0x0045CBC0  prepend 8-byte identity trước payload khi ghi
```

Runtime payload quan sát trong dump:

```text
0x00A53CC0, length 0x1188
```

Layout:

```text
Encrypted system.dat               0x13C6 byte
  outer checksum                   4 byte
  encoded body                     0x13C2 byte

Decoded image                      0x1190 byte
  identity header                  8 byte
  payload                          0x1188 byte
```

Công thức:

```text
(0x1190 / 8) * 9 + 4 = 0x13C6
```

## 2. Codec 8 → 9 byte

```text
0x00439C00  modular exponentiation
0x00439C90  encode block stream
0x00439D80  decode block stream
0x00439E50  write outer checksum
0x00439EF0  verify outer checksum
0x00439FB0  encoder wrapper
0x00439FF0  decoder/check wrapper
```

Thông số:

```text
clear chunk       8 byte
encoded chunk     9 byte
modulus           0x12B = 299
encode exponent   5
Decode exponent   0x35 = 53
```

Với clear byte ở vị trí `i` trong block:

```text
v9 = pow(clear[i], 5, 299)
packed |= v9 << i
```

Decoder lấy 9-bit field tương ứng rồi:

```text
clear[i] = pow(v9, 53, 299) & 0xFF
```

Cặp exponent đảo được toàn bộ miền clear byte `0..255`; self-test kiểm tra đủ 256 giá trị.

## 3. Outer checksum

Seed bốn accumulator:

```text
83 ED 76 45
```

Routine trừ từng byte encoded từ offset 4, phân phối theo `index mod 4`, modulo 256. Bốn kết quả được ghi tại file offset `0..3`.

Size truyền vào checksum là decoded size `0x1190`, dù encrypted file dài `0x13C6`. Codec tái hiện đúng hành vi build thay vì giả định checksum bao toàn bộ file.

## 4. Identity header và quá trình tạo `flcrc`

Routine liên quan:

```text
FUN_0045CA80  0x0045CA80  read/create registry flcrc
FUN_005C40B7  0x005C40B7  sinh scalar từ local/system time và timezone
FUN_0045CBC0  0x0045CBC0  gắn identity vào decoded save khi ghi
```

Khi registry có `flcrc`:

1. engine đọc REG_BINARY;
2. yêu cầu size 13 byte;
3. kiểm tra checksum nhỏ;
4. decode 9-byte body thành identity 8 byte;
5. lưu identity vào object/runtime state.

Khi registry chưa có `flcrc`:

1. clear identity buffer;
2. đặt byte 0 thành `1`;
3. gọi time-scalar routine và ghi DWORD vào bytes 4..7;
4. encode identity 8 → 9 byte;
5. tính checksum 4 byte;
6. ghi value `flcrc` 13 byte vào registry.

Không thấy đường code lấy serial ổ đĩa, CPU ID, MAC address hoặc fingerprint phần cứng. `flcrc` là token identity theo lần tạo registry/profile.

## 5. Cấu trúc `flcrc`

```text
13-byte flcrc
  +0x00  checksum, 4 byte
  +0x04  encoded identity body, 9 byte
```

Encoded body là đúng block 9 byte đầu của encoded decoded-image, tức file offsets `4..12`.

Checksum `flcrc` dùng cùng seed:

```text
83 ED 76 45
```

nhưng chỉ trừ bốn byte body đầu tương ứng. Editor validate length, checksum rồi mới decode.

## 6. Identity checker: exact bytes và engine key

Reverse engineering checker cho thấy build này so:

```text
identity[0]
*(uint32_t *)&identity[4]
```

Ba byte `identity[1..3]` không tham gia nhánh quyết định. Chúng là padding/state được bảo toàn nguyên vẹn.

Do đó điều kiện engine acceptance được mô tả chính xác hơn là:

```text
save_header[0] == registry_identity[0]
and
u32(save_header + 4) == u32(registry_identity + 4)
```

Không phải bắt buộc:

```text
all_8_bytes_equal
```

Editor phân loại:

- `EXACT`: cả 8 byte bằng nhau;
- `ENGINE MATCH`: engine key bằng nhau, padding khác;
- `DIFFERENT`: engine key khác;
- `INVALID/MISSING`: registry value không dùng được.

Điều này tránh ghi đè registry chỉ vì padding khác nhưng engine vẫn chấp nhận.

## 7. Read path và nguyên nhân save bị reset

`FUN_004832C0`:

1. đọc file;
2. verify outer checksum/decode;
3. lấy 8-byte identity header;
4. gọi checker so với identity từ registry;
5. chỉ copy payload `0x1188` vào runtime khi các bước thành công;
6. trả lỗi nếu decode/checker thất bại.

Higher-level initialization có thể khởi tạo payload mặc định và ghi save mới sau read failure. Vì vậy copy `system.dat` sang profile có engine key khác có thể dẫn đến file bị thay bằng save mặc định.

Hai cách hợp lệ:

### Override registry theo save

- giữ header save;
- encode header thành `flcrc`;
- backup value cũ;
- ghi REG_BINARY vào key hiện hữu;
- QueryValueEx đọc lại và so byte-for-byte.

### Rebind save theo registry

- validate/decode registry `flcrc`;
- thay decoded header 8 byte;
- encode full file;
- tính outer checksum;
- payload không đổi.

Không cần áp dụng cả hai.

## 8. Registry paths và virtualization

Các string/path được editor scan gồm HKCU VirtualStore, HKCR VirtualStore, HKLM WOW6432Node, HKLM native và HKCU product path.

Nhiều key có thể cùng tồn tại do:

- 32-bit process trên 64-bit Windows;
- UAC registry virtualization;
- admin/non-admin execution;
- leftover install/repack.

Editor không tự tạo key vì không có bằng chứng key nào là active trong một repack bất kỳ. Nó chỉ sửa key/view người dùng chọn và đã scan thấy.

## 9. Payload footer và inner checksum

Routine:

```text
0x005BE0A0  copy signature
0x005BE0C0  validate magic/checksum
0x005BE100  write magic/checksum
0x005BE160  initialize default payload
```

Layout, offset từ đầu payload:

```text
0x117A..0x1181  ASCII YUGIOH01
0x1182          uint16 magic 0xFBA5
0x1184          uint16 checksum
0x1186          uint16 trailing padding
```

Checksum:

1. magic phải là `0xFBA5`;
2. cộng `0x8C2` word little-endian từ payload offset `0x0000..0x1183` modulo 65536;
3. checksum là bù hai của tổng;
4. ghi tại `0x1184`.

Identity nằm ngoài payload nên rebind identity không cần đổi inner checksum. Full file vẫn cần encode lại và outer checksum mới.

## 10. Card collection

```text
payload +0x000A  stored total card count, uint16
payload +0x000C  card table
entry count       0x45B = 1115
entry size        2 byte
byte size         0x8B6
last index        0x45A
```

`CARD_ID.bin` và `CARD_Pack.bin` đều dài `0x8B6`, xác nhận 1.115 `uint16`.

Entry:

```text
bits  0..7   owned quantity
bits  8..9   deck counter A
bits 10..11  deck counter B
bits 12..13  deck counter C
bit  14      new/unseen
bit  15      unknown/reserved
```

Routine tăng owned count bão hòa low byte ở `0xFF` và tăng stored total. Editor khi sửa quantity bảo toàn high byte.

Lưu ý namespace:

- internal save index phải `< 0x45B`;
- `0x8B6` là byte length, không phải max card ID;
- external Card ID có thể lớn hơn `0x8B6`.

## 11. Tên card và các bảng `.bin`

Name lookup engine đã đối chiếu có dạng:

```text
name_ptr = card_nameeng_base + internal_index * 0x40
```

`card_nameeng.bin`:

- record 64 byte;
- NUL-terminated string;
- index trực tiếp bằng internal card/save index;
- file cung cấp có 1.318 record hoàn chỉnh và 8 trailing zero byte;
- editor bỏ qua tail không đủ record và dùng 1.115 record đầu.

`CARD_ID.bin`:

```text
internal index -> external Card ID
```

`CARD_Pack.bin`:

```text
internal index -> raw pack/category word
```

`CARD_IntID.bin` là reverse lookup phục vụ engine; `CARD_IndxENG.bin` là metadata/index English khác format name record. Cả hai không cần cho cột tên hoặc chỉnh bảng save.

## 12. Xác nhận bằng crash dump

Một encrypted buffer hoàn chỉnh được tìm thấy tại process VA `0x00C9D0B8`:

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

Runtime payload tại `0x00A53CC0` độc lập xác nhận signature, magic, checksum và card total.

Buffer riêng từ process memory chỉ dùng kiểm thử nội bộ, không đóng gói.

## 13. Field chưa chứng minh đủ ngữ nghĩa

- payload `+0x0006`: options bitfield; initializer thường ghi `0x00FF`;
- payload `+0x0008`: display/window field; initializer thường ghi `0x0010`;
- vùng khoảng `0x10ED..0x10F4`: gameplay/difficulty flags;
- từ `0x10F8`: packed counters có helper chia trường 11/11/10 bit;
- card bit 15 và pack word semantics chưa gán tên.

Editor chỉ expose chúng ở raw view hoặc hiển thị raw, không tự suy diễn tên gameplay.

## 14. Quy tắc ghi an toàn được implement

- giữ nguyên identity header khi chỉnh payload thông thường;
- không zero vùng chưa biết;
- bảo toàn high bits card khi đổi quantity;
- recompute stored total;
- ghi magic/signature/checksum khi tùy chọn bật;
- encode và tính outer checksum;
- atomic replace và backup file cũ;
- backup registry trước override;
- registry read-back verification;
- không tự tạo registry key;
- config đường dẫn tách khỏi dữ liệu save.

## 15. Phạm vi xác nhận

Codec, parser và GUI được kiểm tra bằng unit test và GUI smoke test. Winreg API chỉ tồn tại trên Windows; logic scan/backup/write/read-back đã được kiểm tra tĩnh, nhưng việc chọn active key và quyền ghi phải được xác nhận trên máy chạy game.
