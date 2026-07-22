# YGO `system.dat` Editor 1.0

Công cụ Python/Tkinter để giải mã, kiểm tra, chỉnh sửa và mã hóa lại `system.dat` của executable đã được phân tích.

Công cụ chỉ dùng thư viện chuẩn Python. Không cần cài package ngoài.

## 1. Chạy nhanh trên Windows

1. Cài Python 3.10 trở lên; bật thành phần **Tcl/Tk and IDLE**.
2. Giải nén **toàn bộ** package vào một thư mục.
3. Tắt game hoàn toàn.
4. Chạy:

```bat
RUN_EDITOR.bat
```

hoặc:

```bat
py -3 ygo_system_dat_editor.py
```

5. Vào tab **Data files** để kiểm tra đường dẫn.
6. Mở `system.dat`.
7. Kiểm tra tab **Summary**. Outer checksum, inner magic, inner checksum và signature nên là `OK`.
8. Trước khi chuyển save giữa máy/profile, mở tab **Registry / Identity** và đọc mục 3–5 bên dưới.

Editor luôn tạo backup khi ghi đè save:

```text
system.dat.bak-YYYYMMDD-HHMMSS
```

Luôn giữ một bản sao ngoài thư mục game trước khi thử.

---

## 2. `flcrc` có gắn với phần cứng máy không?

Không. Trong đường code của build được phân tích, `flcrc` **không lấy** serial ổ đĩa, CPU ID, MAC address, SID máy hoặc hardware fingerprint.

Nó là token identity được tạo theo registry/profile khi game chưa có value `flcrc`:

```text
clear identity, 8 byte
  byte 0       loại/version, thường thấy 01
  bytes 1..3   padding được bảo toàn
  bytes 4..7   DWORD được sinh từ thời gian local lúc khởi tạo

registry flcrc, 13 byte
  bytes 0..3   checksum riêng
  bytes 4..12  9-byte encoded identity
```

Khi registry chưa có `flcrc`, engine:

1. tạo identity 8 byte;
2. đặt byte đầu thành `1`;
3. tạo DWORD cuối từ routine thời gian local;
4. mã hóa identity thành 9 byte;
5. thêm checksum 4 byte thành `flcrc` 13 byte;
6. ghi `flcrc` vào registry;
7. dùng cùng identity làm 8 byte decoded đầu của `system.dat`.

Vì vậy token không phụ thuộc phần cứng, nhưng có thể khác giữa:

- hai máy;
- hai Windows user/profile;
- hai lần cài hoặc hai registry hive độc lập;
- lần chạy admin và non-admin nếu Windows virtualization đưa game tới key khác;
- repack/mod dùng registry path khác.

---

## 3. Engine thực sự so sánh gì?

Routine identity checker của build này không yêu cầu cả 8 byte phải giống tuyệt đối. Nó so:

```text
identity byte 0
identity DWORD tại bytes 4..7
```

Ba byte `1..3` là padding và không tham gia quyết định chấp nhận save trong routine đã phân tích. Editor vẫn bảo toàn chúng.

Tab **Registry / Identity** hiển thị một trong các trạng thái:

| Trạng thái | Ý nghĩa |
|---|---|
| `EXACT` | Cả 8 byte identity giải mã từ registry giống hoàn toàn header save. |
| `ENGINE MATCH` | Byte 0 và DWORD bytes 4..7 giống; chỉ padding bytes 1..3 khác. Build này vẫn chấp nhận. |
| `DIFFERENT` | Engine identity key khác. Save có thể bị từ chối và game có thể khởi tạo `system.dat` mới. |
| `INVALID` | Value không phải `flcrc` 13 byte hợp lệ hoặc checksum sai. |
| `MISSING` | Key tồn tại nhưng chưa có value `flcrc`. |
| `UNCHECKED` | Chưa mở save để so sánh. |

Do đó không cần override registry khi trạng thái đã là `EXACT` hoặc `ENGINE MATCH`.

---

## 4. Vì sao copy riêng `system.dat` sang máy khác bị game tạo lại?

Decoded `system.dat` bắt đầu bằng identity 8 byte. Registry giữ identity tương ứng dưới dạng `flcrc`.

Read path của engine:

1. đọc encrypted file;
2. kiểm tra outer checksum và giải mã;
3. lấy identity 8 byte đầu;
4. so identity key với registry;
5. chỉ copy payload gameplay vào runtime khi các kiểm tra thành công.

Nếu save và registry là `DIFFERENT`, read path trả lỗi. Tầng khởi tạo phía trên có thể tạo payload mặc định rồi ghi một `system.dat` mới. Đây là lý do việc copy **chỉ file save** có thể không đủ.

Cần đồng bộ bằng **một** trong hai workflow dưới đây. Không cần làm cả hai.

---

## 5. Hai cách chuyển save đúng

### Cách A — Override registry theo save

Dùng khi muốn giữ nguyên identity của file save được chuyển sang.

1. Tắt game.
2. Backup save hiện tại của máy đích.
3. Copy `system.dat` cần chuyển sang đúng thư mục game/save.
4. Mở file đó trong editor.
5. Mở **Registry / Identity**.
6. Bấm **Scan registry**.
7. Chọn đúng key/view mà game dùng.
8. Nếu trạng thái là `DIFFERENT`, bấm **Override selected registry from save**.
9. Editor backup `flcrc` cũ, ghi value mới rồi đọc lại để verify.
10. Scan lại. Trạng thái nên là `EXACT`.
11. Chạy game.

Editor không tự tạo registry key mới. Nó chỉ ghi vào key đã tồn tại để giảm nguy cơ ghi nhầm product/path.

Backup registry được đặt tại:

```text
%APPDATA%\YGOSystemDatEditor\registry_backups\
```

Mỗi lần ghi có:

- file `.bin` chứa value cũ nếu value tồn tại;
- file `.txt` ghi root, key path, registry view và hex value.

Để khôi phục một backup `.bin`, scan registry, chọn đúng **cùng root/path/view**, rồi dùng
**Import flcrc.bin to selected registry**. Editor kiểm tra đúng 13 byte, checksum, backup
value hiện tại và read-back verify sau khi ghi. File `.bin` là raw REG_BINARY 13 byte,
không phải file `.reg` và không mở bằng Registry Editor.

Nếu metadata backup ghi `flcrc=MISSING`, trước thao tác đó value chưa tồn tại nên không có
`.bin` để import. Muốn rollback tuyệt đối về trạng thái thiếu value phải xóa `flcrc` thủ công
trong Regedit tại đúng root/path/view; chỉ làm sau khi đã đối chiếu file `.txt`.

### Cách B — Rebind save theo registry máy đích

Dùng khi muốn giữ nguyên identity đã có trên máy/profile đích.

1. Tắt game.
2. Mở file save cần chuyển.
3. Vào **Registry / Identity** và bấm **Scan registry**.
4. Chọn key/view có `flcrc` hợp lệ mà game đang dùng.
5. Bấm **Rebind save to selected registry**.
6. Editor thay 8-byte identity header trong bộ nhớ bằng identity giải mã từ registry.
7. Bấm **Save** để encode và ghi lại file.
8. Scan/compare lại. Trạng thái nên là `EXACT`.
9. Chạy game.

Payload gameplay và inner checksum không chứa identity; rebind chỉ thay header ngoài payload. Save vẫn được encode lại và outer checksum được tính lại.

### Không làm cả hai

- Cách A giữ identity của save và thay registry.
- Cách B giữ identity của registry và thay save.

Hai cách giải cùng một mismatch. Chọn một theo dữ liệu muốn giữ.

### Nếu game đã ghi đè save chuyển sang

1. Tắt game ngay.
2. Không tiếp tục dùng file mới làm nguồn.
3. Khôi phục file save gốc từ backup.
4. Đồng bộ identity trước khi mở game lần nữa.

---

## 6. Registry paths và Windows virtualization

Editor scan các vị trí thường gặp sau:

```text
HKCU\Software\Classes\VirtualStore\MACHINE\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKCR\VirtualStore\MACHINE\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKLM\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKLM\SOFTWARE\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
HKCU\Software\KONAMI\Yu-Gi-Oh! Power Of Chaos\system
```

Editor thử default, 32-bit và 64-bit registry view khi API Windows hỗ trợ.

Có thể thấy nhiều hàng vì:

- game 32-bit trên Windows 64-bit;
- UAC registry virtualization;
- game từng chạy admin và non-admin;
- key cũ còn sót lại;
- repack tạo nhiều path.

Cách chọn key đúng:

1. ưu tiên key có `flcrc` hiện hữu;
2. so trạng thái với save game đang chạy được;
3. kiểm tra key nào thay đổi sau khi game tạo save mới;
4. dùng cùng quyền chạy với game khi cần;
5. không ghi hàng loạt vào mọi key.

Ghi HKLM có thể cần quyền Administrator. Editor báo lỗi nếu Windows từ chối; không bypass permission.

---

## 7. Tab **Data files**: cần nạp file nào và tại sao?

| File | Vai trò trong editor | Bắt buộc? | Cấu trúc đã dùng |
|---|---|---:|---|
| `card_nameeng.bin` | Hiển thị cột **Card name**, tìm theo tên. | Khuyến nghị | Record cố định `0x40` byte theo internal index. |
| `CARD_ID.bin` | Hiển thị external Card ID hex/decimal; giúp bỏ qua sentinel `0000/FFFF` khi bulk edit. | Không | `0x45B` little-endian `uint16`; file dài `0x8B6`. |
| `system.dat` | Save mặc định để mở nhanh. | Không | Encrypted `0x13C6`, decoded `0x1190` hoặc payload `0x1188`. |

### `card_nameeng.bin`

Engine name lookup dùng:

```text
record_address = base + internal_index * 0x40
```

Mỗi record là chuỗi tên được NUL-terminate trong vùng 64 byte. Editor decode theo Windows-1252 (`cp1252`) và ánh xạ trực tiếp internal index của bảng save.

File được cung cấp dài `0x14988` byte, gồm 1.318 record đầy đủ và 8 byte zero ở cuối. Editor chủ động bỏ qua phần đuôi không đủ một record. Save chỉ có `0x45B = 1115` entry, nên editor dùng 1.115 record đầu.

Ví dụ:

```text
index 0x0000  blank
index 0x0001  Earthbound Spirit
index 0x0002  Felgrand Dragon
```

### Cấu hình đường dẫn

Tab **Data files** cho phép:

- chọn từng file riêng;
- chọn một folder và tự điền các file tìm thấy;
- auto-detect cạnh script, current working directory và folder save;
- reload/validate từng file;
- lưu cấu hình tại:

```text
%APPDATA%\YGOSystemDatEditor\config.json
```

Các `.bin` không cần nằm cạnh script nếu đã cấu hình đường dẫn đúng.

---

## 8. Format `system.dat`

### Kích thước

```text
Encrypted system.dat               0x13C6 byte
  outer checksum                   4 byte
  encoded body                     0x13C2 byte

Decoded image                      0x1190 byte
  identity header                  8 byte
  payload                          0x1188 byte
```

Công thức encode:

```text
(0x1190 / 8) * 9 + 4 = 0x13C6
```

Editor cũng nhận:

- decoded image `0x1190` byte;
- payload-only `0x1188` byte.

Payload-only không có identity header. Editor tạo header zero để cho phép phân tích, nhưng file đó phải được rebind hoặc đồng bộ `flcrc` trước khi game chấp nhận.

### Codec 8 → 9 byte

```text
clear chunk       8 byte
encoded chunk     9 byte
modulus           299 (0x12B)
encode exponent   5
Decode exponent   53 (0x35)
```

Mỗi clear byte được modular exponentiation thành giá trị 9-bit, shift theo vị trí 0..7 và pack vào 9 byte. Decoder tách lại từng giá trị 9-bit và áp exponent 53 modulo 299.

### Outer checksum

Seed:

```text
83 ED 76 45
```

Engine phân phối byte encoded vào bốn accumulator theo `offset mod 4` và trừ modulo 256. Bốn byte checksum nằm đầu file.

### Payload footer

Offset tính từ đầu payload:

```text
0x117A..0x1181  ASCII "YUGIOH01"
0x1182          uint16 magic 0xFBA5
0x1184          uint16 inner checksum
0x1186          uint16 trailing padding
```

Inner checksum là bù hai 16-bit của tổng `0x8C2` word đầu payload, sau khi magic được đặt đúng.

---

## 9. Bảng card trong save

```text
payload +0x000A  stored total card count, uint16
payload +0x000C  card table
entry count       0x45B = 1115
entry size        2 byte
last index        0x45A
byte size         0x8B6
```

Cần phân biệt:

- `0x8B6` là **số byte** của bảng, không phải ID lớn nhất;
- internal index hợp lệ là `0x0000..0x045A`;
- external Card ID có thể lớn hơn `0x8B6` vì đó là namespace khác.

Mỗi entry `uint16`:

```text
bits  0..7   owned quantity, 0..255
bits  8..9   deck counter A
bits 10..11  deck counter B
bits 12..13  deck counter C
bit  14      new/unseen flag
bit  15      chưa xác định
```

Khi sửa quantity, editor chỉ thay low byte và bảo toàn deck counters/bit 14/bit 15. Khi sửa `New`, editor chỉ thay bit 14.

Bulk operation có tùy chọn bỏ qua external ID `0000` và `FFFF`. Tùy chọn này có ý nghĩa nhất khi `CARD_ID.bin` đã được nạp.

---

## 10. Các tab trong GUI

### Summary

Hiển thị:

- format và kích thước source;
- outer checksum;
- inner magic/checksum;
- signature;
- exact decode/encode round-trip;
- 8-byte identity header;
- `flcrc` suy ra từ save;
- engine identity key;
- stored/calculated card total;
- trạng thái data files.

### Cards

- tìm theo tên, internal index, external ID hex hoặc decimal;
- hiển thị tên, ID, owned count, deck counters, flags và raw value;
- sửa từng card;
- bulk set owned count;
- mark/clear `new`;
- tính lại total.

### Registry / Identity

- scan các key chuẩn;
- kiểm tra length/checksum `flcrc`;
- decode identity;
- phân loại `EXACT`, `ENGINE MATCH`, `DIFFERENT`;
- override hàng loạt các registry row đang multi-select bằng `flcrc` của save, có backup + read-back verify;
- import một `flcrc.bin` vào nhiều registry row cùng lúc;
- rebind save theo đúng một registry row được chọn;
- export `flcrc.bin` từ save đang mở.

### Data files

Cấu hình riêng từng đường dẫn, auto-detect, validate và lưu `config.json`.

### Raw fields

Cho đọc/ghi payload theo offset. Dùng cho field chưa đủ bằng chứng ngữ nghĩa. Raw write có thể phá save dù checksum được tính lại; chỉ dùng khi hiểu layout.

### Log

Ghi thao tác mở/lưu, data-file load, registry scan/write, backup và lỗi chi tiết.

---

## 11. Quy trình chỉnh card an toàn

1. Tắt game.
2. Backup `system.dat` và registry `flcrc`.
3. Mở encrypted `system.dat` hiện tại để giữ identity header.
4. Kiểm tra Summary.
5. Nạp `card_nameeng.bin`; nạp `CARD_ID.bin` nếu cần external ID/sentinel filtering.
6. Chỉnh card.
7. Giữ ba tùy chọn Save mặc định bật:
   - Repair inner checksum/magic;
   - Ensure `YUGIOH01` signature;
   - Recompute stored card total.
8. Save.
9. Kiểm tra lại Summary.
10. Registry chỉ cần thay đổi nếu trạng thái là `DIFFERENT` hoặc bạn chủ động rebind identity.
11. Chạy game.

Chỉnh card bình thường không đổi identity và không đổi `flcrc`.

---

## 12. CLI

```bat
py -3 ygo_system_dat_editor.py --self-test
py -3 ygo_system_dat_editor.py --info system.dat
py -3 ygo_system_dat_editor.py --decode system.dat system.decoded.bin
py -3 ygo_system_dat_editor.py --encode system.decoded.bin system.new.dat
```

`--info` in cả header, engine identity key, derived `flcrc`, checksum và card total.

---

## 13. Khắc phục sự cố

### Game vẫn tạo `system.dat` mới

Kiểm tra tab **Registry / Identity** trước. Nếu trạng thái là `DIFFERENT`, chọn một trong hai đường sau; không làm cả hai:

- multi-select các row registry đúng đích rồi dùng **Override selected registry rows from save**; hoặc
- chọn đúng một row rồi dùng **Rebind save to selected registry** và lưu lại save.


Kiểm tra theo thứ tự:

1. file có đúng path game thực sự đọc không;
2. game có đang chạy khi copy/save không;
3. Summary có checksum/magic/signature hợp lệ không;
4. Registry / Identity có `DIFFERENT` không;
5. đã chọn đúng registry root/path/view chưa;
6. game chạy admin hay non-admin;
7. VirtualStore có key khác không;
8. save đã bị ghi đè trước khi đồng bộ chưa;
9. repack/mod có thay registry path hoặc format không.

### Không thấy tên card


- chọn đúng `card_nameeng.bin`;
- không chọn `CARD_IndxENG.bin` thay thế;
- kiểm tra status `Loaded ... names` trong Data files;
- kiểm tra file đúng build;
- reload data files.

### Có tên nhưng ID không khớp

Tên lấy theo internal index từ `card_nameeng.bin`; ID lấy từ `CARD_ID.bin`. Hai namespace này khác nhau. External Card ID không phải index của bảng save.


Tên và external ID đến từ hai file độc lập. `card_nameeng.bin` và `CARD_ID.bin` phải cùng build/data set. Nếu trộn version, cùng internal index có thể chỉ tới tên và ID khác nhau.

### Không ghi được registry

- chạy editor với quyền phù hợp;
- chọn key HKCU/VirtualStore nếu game thực tế dùng nó;
- HKLM thường cần Administrator;
- editor không tự tạo key mới;
- xem Log và backup folder.

### `ENGINE MATCH` nhưng không `EXACT`

Ba byte padding khác nhau. Với checker đã reverse-engineer, điều này vẫn hợp lệ. Không cần override chỉ để chuyển thành `EXACT`, trừ khi đang nghiên cứu một build khác có logic checker khác.

### Payload-only bị từ chối

Payload-only không có identity thật. Rebind theo registry trước, Save thành encrypted `system.dat`, rồi kiểm tra match. Bản editor sẽ cảnh báo khi Save nguồn không có encrypted identity baseline; không bỏ qua cảnh báo đó.

### Khôi phục `flcrc` cũ

1. Mở file `.txt` trong `%APPDATA%\YGOSystemDatEditor\registry_backups` để xác định root/path/view.
2. Scan registry và chọn đúng hàng tương ứng.
3. Bấm **Import flcrc.bin to selected registry** và chọn file `.bin` cùng timestamp.
4. Kiểm tra dialog hiển thị đúng target và trạng thái match với save đang mở.
5. Sau khi ghi, editor tự đọc lại để verify và scan lại bảng.

Không import backup của một key/view sang key/view khác chỉ vì tên game giống nhau.

---

## 14. Kiểm thử và xác nhận

- editor khởi động khi chỉ có `card_nameeng.bin`;
- registry list ở `selectmode=extended`;
- batch override/import dựng preview nhiều row và vẫn ép rebind về single-select.


Bộ package có unit tests cho:

- modular codec và full save round-trip;
- outer checksum;
- inner magic/checksum/signature;
- `flcrc` encode/decode/checksum;
- exact match và engine-key match khi padding khác;
- payload-only handling;
- kích thước `CARD_ID.bin`;
- parser `card_nameeng.bin` và các tên đầu bảng.

Chạy:

```bat
py -3 -m unittest -v
```

---

## 15. Giới hạn và phạm vi

- Phân tích áp dụng cho executable/build được cung cấp; repack khác có thể thay layout/path/checker.
- Registry write/read-back code chỉ có thể chạy trên Windows. Package được kiểm tra GUI/codec trên môi trường Linux có Xvfb, nhưng thao tác winreg thực tế cần xác nhận trên máy Windows của người dùng.
- Một số payload field cuối file chưa đủ bằng chứng; editor không tự đặt tên hoặc tự sửa.
- Checksum hợp lệ không bảo đảm mọi raw value có ý nghĩa hợp lệ với gameplay.

Xem `ENGINE_ANALYSIS_VI.md` để biết địa chỉ routine, codec và bằng chứng kỹ thuật.
