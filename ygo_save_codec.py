"""Codec and structured access for Yu-Gi-Oh! Power of Chaos system.dat.

This module uses only the Python standard library.  It intentionally separates
binary handling from the Tkinter GUI so the critical codec can be tested without
opening a window.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import struct
import tempfile
import time
from typing import Iterable, Optional

# Disk/decoded layout.
CLEAR_CHUNK_SIZE = 8
ENC_CHUNK_SIZE = 9
HEADER_SIZE = 8
PAYLOAD_SIZE = 0x1188
DECODED_SIZE = HEADER_SIZE + PAYLOAD_SIZE  # 0x1190
ENCODED_SIZE = (DECODED_SIZE // CLEAR_CHUNK_SIZE) * ENC_CHUNK_SIZE + 4  # 0x13C6

# Per-byte modular transform.
MODULO = 0x12B  # 299
ENC_EXPONENT = 0x05
DEC_EXPONENT = 0x35

# Payload structure confirmed in the supplied engine.
CARD_TABLE_OFFSET = 0x000C
CARD_ENTRY_COUNT = 0x045B  # 1115
CARD_ENTRY_SIZE = 2
CARD_TABLE_SIZE = CARD_ENTRY_COUNT * CARD_ENTRY_SIZE  # 0x8B6
TOTAL_CARD_COUNT_OFFSET = 0x000A
SIGNATURE_OFFSET = 0x117A
SIGNATURE = b"YUGIOH01"
MAGIC_OFFSET = 0x1182
MAGIC_VALUE = 0xFBA5
INNER_CHECKSUM_OFFSET = 0x1184
INNER_CHECKSUM_WORDS = 0x08C2
TRAILING_PADDING_OFFSET = 0x1186

OUTER_CHECKSUM_SEED = bytes((0x83, 0xED, 0x76, 0x45))
FLCRC_SIZE = 13


class SaveFormatError(ValueError):
    """Raised when input is not a supported system.dat representation."""


@dataclass(frozen=True)
class ValidationReport:
    source_format: str
    source_size: int
    outer_checksum_valid: Optional[bool]
    inner_magic_valid: bool
    inner_checksum_valid: bool
    signature_valid: bool
    roundtrip_exact: Optional[bool]
    stored_total_cards: int
    calculated_total_cards: int

    @property
    def inner_valid(self) -> bool:
        return self.inner_magic_valid and self.inner_checksum_valid


@dataclass
class SaveImage:
    """Decoded save image: 8-byte identity header plus 0x1188-byte payload."""

    decoded: bytearray
    source_format: str = "decoded"
    source_path: Optional[Path] = None
    original_encoded: Optional[bytes] = None

    def __post_init__(self) -> None:
        if len(self.decoded) != DECODED_SIZE:
            raise SaveFormatError(
                f"Decoded image must be 0x{DECODED_SIZE:X} bytes, got 0x{len(self.decoded):X}."
            )

    @property
    def header(self) -> memoryview:
        return memoryview(self.decoded)[:HEADER_SIZE]

    @property
    def payload(self) -> memoryview:
        return memoryview(self.decoded)[HEADER_SIZE:]

    def clone(self) -> "SaveImage":
        return SaveImage(
            bytearray(self.decoded),
            source_format=self.source_format,
            source_path=self.source_path,
            original_encoded=self.original_encoded,
        )


def _require_size(data: bytes | bytearray | memoryview, expected: int, label: str) -> None:
    if len(data) != expected:
        raise SaveFormatError(
            f"{label} must be 0x{expected:X} bytes, got 0x{len(data):X}."
        )


def _transform_encode(decoded: bytes | bytearray | memoryview) -> bytearray:
    _require_size(decoded, DECODED_SIZE, "Decoded save")
    out = bytearray((DECODED_SIZE // CLEAR_CHUNK_SIZE) * ENC_CHUNK_SIZE)
    src = memoryview(decoded)
    for chunk_index in range(DECODED_SIZE // CLEAR_CHUNK_SIZE):
        src_base = chunk_index * CLEAR_CHUNK_SIZE
        dst_base = chunk_index * ENC_CHUNK_SIZE
        # bytearray is already zeroed. Each 9-bit result is shifted by its
        # byte position and ORed into two neighboring output bytes.
        for i in range(CLEAR_CHUNK_SIZE):
            mask = pow(src[src_base + i], ENC_EXPONENT, MODULO) << i
            out[dst_base + i] |= mask & 0xFF
            out[dst_base + i + 1] |= (mask >> 8) & 0xFF
    return out


def _transform_decode(encoded_body: bytes | bytearray | memoryview) -> bytearray:
    expected = (DECODED_SIZE // CLEAR_CHUNK_SIZE) * ENC_CHUNK_SIZE
    _require_size(encoded_body, expected, "Encoded body")
    out = bytearray(DECODED_SIZE)
    src = memoryview(encoded_body)
    for chunk_index in range(DECODED_SIZE // CLEAR_CHUNK_SIZE):
        src_base = chunk_index * ENC_CHUNK_SIZE
        dst_base = chunk_index * CLEAR_CHUNK_SIZE
        for i in range(CLEAR_CHUNK_SIZE):
            pair = src[src_base + i] | (src[src_base + i + 1] << 8)
            value9 = (pair >> i) & 0x1FF
            out[dst_base + i] = pow(value9, DEC_EXPONENT, MODULO) & 0xFF
    return out


def calculate_outer_checksum(encoded: bytes | bytearray | memoryview) -> bytes:
    """Calculate the four-byte checksum stored at encoded offsets 0..3.

    The engine intentionally checks only bytes 4..0x118F even though the file
    is longer than the decoded image.
    """
    if len(encoded) < DECODED_SIZE:
        raise SaveFormatError(
            f"Encoded buffer must contain at least 0x{DECODED_SIZE:X} bytes."
        )
    acc = bytearray(OUTER_CHECKSUM_SEED)
    for i in range(4, DECODED_SIZE):
        slot = i & 3
        acc[slot] = (acc[slot] - encoded[i]) & 0xFF
    return bytes(acc)


def validate_outer_checksum(encoded: bytes | bytearray | memoryview) -> bool:
    _require_size(encoded, ENCODED_SIZE, "Encrypted system.dat")
    return bytes(encoded[:4]) == calculate_outer_checksum(encoded)


def encode_decoded(decoded: bytes | bytearray | memoryview) -> bytes:
    """Encode a complete 0x1190-byte clear image to a 0x13C6-byte system.dat."""
    body = _transform_encode(decoded)
    out = bytearray(4 + len(body))
    out[4:] = body
    out[:4] = calculate_outer_checksum(out)
    return bytes(out)


def decode_encoded(encoded: bytes | bytearray | memoryview) -> bytearray:
    """Decode a complete 0x13C6-byte system.dat.

    Decoding is allowed even when the outer checksum is invalid so damaged
    files can be inspected and repaired by the GUI.
    """
    _require_size(encoded, ENCODED_SIZE, "Encrypted system.dat")
    return _transform_decode(memoryview(encoded)[4:])


def _encode_identity_chunk(identity: bytes | bytearray | memoryview) -> bytes:
    """Encode one 8-byte identity block to the engine's 9-byte body."""
    _require_size(identity, HEADER_SIZE, "Identity header")
    out = bytearray(ENC_CHUNK_SIZE)
    for i, value in enumerate(identity):
        mask = pow(value, ENC_EXPONENT, MODULO) << i
        out[i] |= mask & 0xFF
        out[i + 1] |= (mask >> 8) & 0xFF
    return bytes(out)


def _decode_identity_chunk(encoded_body: bytes | bytearray | memoryview) -> bytes:
    """Decode one 9-byte registry/save identity block."""
    _require_size(encoded_body, ENC_CHUNK_SIZE, "Encoded identity body")
    out = bytearray(HEADER_SIZE)
    for i in range(HEADER_SIZE):
        pair = encoded_body[i] | (encoded_body[i + 1] << 8)
        value9 = (pair >> i) & 0x1FF
        out[i] = pow(value9, DEC_EXPONENT, MODULO) & 0xFF
    return bytes(out)


def calculate_flcrc_checksum(encoded_identity_body: bytes | bytearray | memoryview) -> bytes:
    """Return the four checksum bytes used by registry value ``flcrc``.

    The engine subtracts only body bytes 0..3 from the four-byte seed. The
    remaining five encoded identity bytes are covered indirectly by decoding
    semantics, not by this small checksum.
    """
    _require_size(encoded_identity_body, ENC_CHUNK_SIZE, "Encoded identity body")
    return bytes(
        (OUTER_CHECKSUM_SEED[i] - encoded_identity_body[i]) & 0xFF
        for i in range(4)
    )


def encode_registry_flcrc(identity: bytes | bytearray | memoryview) -> bytes:
    """Encode an 8-byte clear identity into the 13-byte REG_BINARY ``flcrc``."""
    body = _encode_identity_chunk(identity)
    return calculate_flcrc_checksum(body) + body


def validate_registry_flcrc(value: bytes | bytearray | memoryview) -> bool:
    """Validate the 13-byte flcrc length and checksum."""
    if len(value) != FLCRC_SIZE:
        return False
    return bytes(value[:4]) == calculate_flcrc_checksum(value[4:])


def decode_registry_flcrc(
    value: bytes | bytearray | memoryview, *, require_valid_checksum: bool = True
) -> bytes:
    """Decode a 13-byte registry ``flcrc`` to its clear 8-byte identity."""
    _require_size(value, FLCRC_SIZE, "Registry flcrc")
    if require_valid_checksum and not validate_registry_flcrc(value):
        raise SaveFormatError("Registry flcrc checksum is invalid")
    return _decode_identity_chunk(value[4:])


def identity_engine_key(identity: bytes | bytearray | memoryview) -> tuple[int, int]:
    """Return the fields the supplied engine actually compares.

    Reverse engineering of the identity checker shows it compares byte 0 and
    the little-endian DWORD at bytes 4..7. Bytes 1..3 are padding and are
    preserved, but do not determine acceptance for this build.
    """
    _require_size(identity, HEADER_SIZE, "Identity header")
    return int(identity[0]), struct.unpack_from("<I", identity, 4)[0]


def registry_flcrc_matches_header(
    flcrc: bytes | bytearray | memoryview,
    header: bytes | bytearray | memoryview,
    *,
    exact: bool = False,
) -> bool:
    """Compare registry identity with a save header.

    ``exact=False`` mirrors the engine's byte-0/DWORD comparison. ``exact=True``
    additionally requires all eight decoded identity bytes to match.
    """
    try:
        decoded = decode_registry_flcrc(flcrc)
        _require_size(header, HEADER_SIZE, "Identity header")
    except SaveFormatError:
        return False
    if exact:
        return decoded == bytes(header)
    return identity_engine_key(decoded) == identity_engine_key(header)


def calculate_registry_flcrc(encoded: bytes | bytearray | memoryview) -> bytes:
    """Build the 13-byte registry value paired with an encrypted system.dat."""
    _require_size(encoded, ENCODED_SIZE, "Encrypted system.dat")
    # Offsets 4..12 are the encoded first clear block (the identity header).
    body = bytes(encoded[4:13])
    return calculate_flcrc_checksum(body) + body


def payload_from_decoded(decoded: bytes | bytearray | memoryview) -> memoryview:
    _require_size(decoded, DECODED_SIZE, "Decoded save")
    return memoryview(decoded)[HEADER_SIZE:]


def read_u16(payload: bytes | bytearray | memoryview, offset: int) -> int:
    if offset < 0 or offset + 2 > len(payload):
        raise IndexError(f"u16 offset 0x{offset:X} is out of range")
    return struct.unpack_from("<H", payload, offset)[0]


def write_u16(payload: bytearray | memoryview, offset: int, value: int) -> None:
    if offset < 0 or offset + 2 > len(payload):
        raise IndexError(f"u16 offset 0x{offset:X} is out of range")
    struct.pack_into("<H", payload, offset, value & 0xFFFF)


def read_u32(payload: bytes | bytearray | memoryview, offset: int) -> int:
    if offset < 0 or offset + 4 > len(payload):
        raise IndexError(f"u32 offset 0x{offset:X} is out of range")
    return struct.unpack_from("<I", payload, offset)[0]


def write_u32(payload: bytearray | memoryview, offset: int, value: int) -> None:
    if offset < 0 or offset + 4 > len(payload):
        raise IndexError(f"u32 offset 0x{offset:X} is out of range")
    struct.pack_into("<I", payload, offset, value & 0xFFFFFFFF)


def calculate_inner_checksum(payload: bytes | bytearray | memoryview) -> int:
    _require_size(payload, PAYLOAD_SIZE, "Save payload")
    total = 0
    for i in range(INNER_CHECKSUM_WORDS):
        total = (total + read_u16(payload, i * 2)) & 0xFFFF
    return (-total) & 0xFFFF


def validate_inner_magic(payload: bytes | bytearray | memoryview) -> bool:
    _require_size(payload, PAYLOAD_SIZE, "Save payload")
    return read_u16(payload, MAGIC_OFFSET) == MAGIC_VALUE


def validate_inner_checksum(payload: bytes | bytearray | memoryview) -> bool:
    _require_size(payload, PAYLOAD_SIZE, "Save payload")
    return read_u16(payload, INNER_CHECKSUM_OFFSET) == calculate_inner_checksum(payload)


def repair_inner_checksum(
    payload: bytearray | memoryview, *, ensure_signature: bool = False
) -> int:
    _require_size(payload, PAYLOAD_SIZE, "Save payload")
    write_u16(payload, MAGIC_OFFSET, MAGIC_VALUE)
    if ensure_signature:
        payload[SIGNATURE_OFFSET : SIGNATURE_OFFSET + len(SIGNATURE)] = SIGNATURE
    checksum = calculate_inner_checksum(payload)
    write_u16(payload, INNER_CHECKSUM_OFFSET, checksum)
    return checksum


def get_card_raw(payload: bytes | bytearray | memoryview, index: int) -> int:
    if not 0 <= index < CARD_ENTRY_COUNT:
        raise IndexError(f"Card index {index} is outside 0..{CARD_ENTRY_COUNT - 1}")
    return read_u16(payload, CARD_TABLE_OFFSET + index * CARD_ENTRY_SIZE)


def set_card_raw(payload: bytearray | memoryview, index: int, raw: int) -> None:
    if not 0 <= index < CARD_ENTRY_COUNT:
        raise IndexError(f"Card index {index} is outside 0..{CARD_ENTRY_COUNT - 1}")
    write_u16(payload, CARD_TABLE_OFFSET + index * CARD_ENTRY_SIZE, raw)


def get_card_count(payload: bytes | bytearray | memoryview, index: int) -> int:
    return get_card_raw(payload, index) & 0xFF


def set_card_count(payload: bytearray | memoryview, index: int, count: int) -> None:
    if not 0 <= count <= 0xFF:
        raise ValueError("Card count must be in range 0..255")
    raw = get_card_raw(payload, index)
    set_card_raw(payload, index, (raw & 0xFF00) | count)


def set_card_new_flag(payload: bytearray | memoryview, index: int, enabled: bool) -> None:
    raw = get_card_raw(payload, index)
    raw = (raw | 0x4000) if enabled else (raw & ~0x4000)
    set_card_raw(payload, index, raw)


def calculated_card_total(payload: bytes | bytearray | memoryview) -> int:
    return sum(get_card_count(payload, i) for i in range(CARD_ENTRY_COUNT))


def stored_card_total(payload: bytes | bytearray | memoryview) -> int:
    return read_u16(payload, TOTAL_CARD_COUNT_OFFSET)


def recompute_stored_card_total(payload: bytearray | memoryview) -> tuple[int, int]:
    actual = calculated_card_total(payload)
    stored = actual & 0xFFFF
    write_u16(payload, TOTAL_CARD_COUNT_OFFSET, stored)
    return actual, stored


def parse_u16_table(path: str | os.PathLike[str], expected_count: int = CARD_ENTRY_COUNT) -> list[int]:
    data = Path(path).read_bytes()
    expected_size = expected_count * 2
    if len(data) != expected_size:
        raise SaveFormatError(
            f"{Path(path).name} must be 0x{expected_size:X} bytes "
            f"({expected_count} little-endian u16 values), got 0x{len(data):X}."
        )
    return list(struct.unpack(f"<{expected_count}H", data))


def load_save_bytes(data: bytes, *, source_path: Optional[Path] = None) -> SaveImage:
    if len(data) == ENCODED_SIZE:
        decoded = decode_encoded(data)
        return SaveImage(
            decoded,
            source_format="encrypted",
            source_path=source_path,
            original_encoded=bytes(data),
        )
    if len(data) == DECODED_SIZE:
        return SaveImage(
            bytearray(data), source_format="decoded", source_path=source_path
        )
    if len(data) == PAYLOAD_SIZE:
        decoded = bytearray(DECODED_SIZE)
        decoded[HEADER_SIZE:] = data
        return SaveImage(
            decoded, source_format="payload-only", source_path=source_path
        )
    raise SaveFormatError(
        "Unsupported file size. Expected encrypted 0x13C6, decoded 0x1190, "
        f"or payload-only 0x1188 bytes; got 0x{len(data):X}."
    )


def load_save_file(path: str | os.PathLike[str]) -> SaveImage:
    p = Path(path)
    return load_save_bytes(p.read_bytes(), source_path=p)


def validate_image(image: SaveImage) -> ValidationReport:
    payload = image.payload
    outer: Optional[bool] = None
    roundtrip: Optional[bool] = None
    if image.original_encoded is not None:
        outer = validate_outer_checksum(image.original_encoded)
        roundtrip = encode_decoded(image.decoded) == image.original_encoded
    return ValidationReport(
        source_format=image.source_format,
        source_size=(
            len(image.original_encoded)
            if image.original_encoded is not None
            else (DECODED_SIZE if image.source_format == "decoded" else PAYLOAD_SIZE)
        ),
        outer_checksum_valid=outer,
        inner_magic_valid=validate_inner_magic(payload),
        inner_checksum_valid=validate_inner_checksum(payload),
        signature_valid=bytes(payload[SIGNATURE_OFFSET : SIGNATURE_OFFSET + 8]) == SIGNATURE,
        roundtrip_exact=roundtrip,
        stored_total_cards=stored_card_total(payload),
        calculated_total_cards=calculated_card_total(payload),
    )


def prepare_encoded(
    image: SaveImage,
    *,
    repair_checksum: bool = True,
    ensure_signature: bool = True,
    recompute_total: bool = True,
) -> bytes:
    if recompute_total:
        recompute_stored_card_total(image.payload)
    if repair_checksum:
        repair_inner_checksum(image.payload, ensure_signature=ensure_signature)
    return encode_decoded(image.decoded)


def atomic_write_with_backup(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    create_backup: bool = True,
) -> Optional[Path]:
    """Atomically write data. Return backup path when one was created."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Optional[Path] = None
    if create_backup and target.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.bak-{stamp}")
        suffix = 1
        while backup.exists():
            backup = target.with_name(f"{target.name}.bak-{stamp}-{suffix}")
            suffix += 1
        shutil.copy2(target, backup)

    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return backup


def hexdump(data: bytes | bytearray | memoryview, start: int = 0, length: Optional[int] = None) -> str:
    if start < 0:
        raise ValueError("start must be non-negative")
    if length is None:
        length = max(0, len(data) - start)
    end = min(len(data), start + max(0, length))
    lines: list[str] = []
    for offset in range(start, end, 16):
        chunk = bytes(data[offset : min(offset + 16, end)])
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:04X}  {hex_part:<47}  |{ascii_part}|")
    return "\n".join(lines)


def parse_int(text: str, *, bits: Optional[int] = None) -> int:
    value = int(text.strip(), 0)
    if value < 0:
        raise ValueError("Value must not be negative")
    if bits is not None and value >= (1 << bits):
        raise ValueError(f"Value must fit in {bits} bits")
    return value


def parse_hex_bytes(text: str) -> bytes:
    cleaned = "".join(ch for ch in text if ch not in " \t\r\n,;:-_")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) % 2:
        raise ValueError("Hex byte string must contain an even number of digits")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError("Invalid hex byte string") from exc


def self_test() -> None:
    """Fast deterministic codec checks. Raises AssertionError on failure."""
    # Encoding/decoding exponents must be inverse for every clear byte.
    for value in range(256):
        encoded = pow(value, ENC_EXPONENT, MODULO)
        decoded = pow(encoded, DEC_EXPONENT, MODULO) & 0xFF
        assert decoded == value, (value, encoded, decoded)

    sample = bytearray(DECODED_SIZE)
    # Deterministic nontrivial pattern; leave room for a valid payload footer.
    for i in range(DECODED_SIZE):
        sample[i] = (i * 73 + (i >> 3) * 19 + 0x5A) & 0xFF
    payload = memoryview(sample)[HEADER_SIZE:]
    payload[SIGNATURE_OFFSET : SIGNATURE_OFFSET + 8] = SIGNATURE
    repair_inner_checksum(payload)
    encoded = encode_decoded(sample)
    assert len(encoded) == ENCODED_SIZE
    assert validate_outer_checksum(encoded)
    decoded = decode_encoded(encoded)
    assert decoded == sample
    assert validate_inner_magic(payload)
    assert validate_inner_checksum(payload)
    flcrc = calculate_registry_flcrc(encoded)
    assert len(flcrc) == FLCRC_SIZE
    assert validate_registry_flcrc(flcrc)
    assert decode_registry_flcrc(flcrc) == bytes(sample[:HEADER_SIZE])
    assert encode_registry_flcrc(sample[:HEADER_SIZE]) == flcrc
    assert registry_flcrc_matches_header(flcrc, sample[:HEADER_SIZE])
    # Padding bytes 1..3 are ignored by the engine checker for this build.
    padded = bytearray(sample[:HEADER_SIZE])
    padded[1:4] = bytes((padded[1] ^ 0x55, padded[2] ^ 0xAA, padded[3] ^ 0x33))
    padded_flcrc = encode_registry_flcrc(padded)
    assert registry_flcrc_matches_header(padded_flcrc, sample[:HEADER_SIZE])
    assert not registry_flcrc_matches_header(padded_flcrc, sample[:HEADER_SIZE], exact=True)

    # Publicly documented fixed vector for the encoded 8-byte identity header.
    fixed_chunk = bytes.fromhex("01 f8 ed 02 a0 40 10 2f 72")
    fixed_clear = bytearray(8)
    for i in range(8):
        pair = fixed_chunk[i] | (fixed_chunk[i + 1] << 8)
        fixed_clear[i] = pow((pair >> i) & 0x1FF, DEC_EXPONENT, MODULO) & 0xFF
    assert fixed_clear == bytes.fromhex("01 fc 12 00 2b dd c5 3f")
    fixed_reencoded = bytearray(9)
    for i, value in enumerate(fixed_clear):
        mask = pow(value, ENC_EXPONENT, MODULO) << i
        fixed_reencoded[i] |= mask & 0xFF
        fixed_reencoded[i + 1] |= (mask >> 8) & 0xFF
    assert fixed_reencoded == fixed_chunk
    fixed_flcrc = bytearray.fromhex("82 f5 89 43 01 f8 ed 02 a0 40 10 2f 72")
    assert bytes(fixed_flcrc[:4]) == bytes((
        (0x83 - fixed_flcrc[4]) & 0xFF,
        (0xED - fixed_flcrc[5]) & 0xFF,
        (0x76 - fixed_flcrc[6]) & 0xFF,
        (0x45 - fixed_flcrc[7]) & 0xFF,
    ))


__all__ = [name for name in globals() if name.isupper()] + [
    "SaveFormatError",
    "ValidationReport",
    "SaveImage",
    "calculate_outer_checksum",
    "validate_outer_checksum",
    "encode_decoded",
    "decode_encoded",
    "calculate_registry_flcrc",
    "calculate_flcrc_checksum",
    "encode_registry_flcrc",
    "validate_registry_flcrc",
    "decode_registry_flcrc",
    "identity_engine_key",
    "registry_flcrc_matches_header",
    "payload_from_decoded",
    "read_u16",
    "write_u16",
    "read_u32",
    "write_u32",
    "calculate_inner_checksum",
    "validate_inner_magic",
    "validate_inner_checksum",
    "repair_inner_checksum",
    "get_card_raw",
    "set_card_raw",
    "get_card_count",
    "set_card_count",
    "set_card_new_flag",
    "calculated_card_total",
    "stored_card_total",
    "recompute_stored_card_total",
    "parse_u16_table",
    "load_save_bytes",
    "load_save_file",
    "validate_image",
    "prepare_encoded",
    "atomic_write_with_backup",
    "hexdump",
    "parse_int",
    "parse_hex_bytes",
    "self_test",
]
