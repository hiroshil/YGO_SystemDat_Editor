from pathlib import Path
import tempfile
import unittest

from ygo_save_codec import (
    CARD_ENTRY_COUNT,
    DECODED_SIZE,
    ENCODED_SIZE,
    HEADER_SIZE,
    PAYLOAD_SIZE,
    SIGNATURE,
    SIGNATURE_OFFSET,
    SaveImage,
    calculate_registry_flcrc,
    decode_encoded,
    decode_registry_flcrc,
    encode_decoded,
    encode_registry_flcrc,
    identity_engine_key,
    load_save_bytes,
    parse_u16_table,
    prepare_encoded,
    registry_flcrc_matches_header,
    repair_inner_checksum,
    self_test,
    validate_image,
    validate_outer_checksum,
    validate_registry_flcrc,
)
from ygo_system_dat_editor import EditorApp

HERE = Path(__file__).resolve().parent


def make_decoded(header: bytes = bytes.fromhex('01 00 00 00 EA 49 5F 6A')) -> bytearray:
    decoded = bytearray(DECODED_SIZE)
    decoded[:HEADER_SIZE] = header
    payload = memoryview(decoded)[HEADER_SIZE:]
    payload[SIGNATURE_OFFSET:SIGNATURE_OFFSET + len(SIGNATURE)] = SIGNATURE
    repair_inner_checksum(payload)
    return decoded


class CodecTests(unittest.TestCase):
    def test_builtin_self_test(self) -> None:
        self_test()

    def test_complete_save_roundtrip(self) -> None:
        decoded = make_decoded()
        encoded = encode_decoded(decoded)
        self.assertEqual(len(encoded), ENCODED_SIZE)
        self.assertTrue(validate_outer_checksum(encoded))
        self.assertEqual(decode_encoded(encoded), decoded)
        image = load_save_bytes(encoded)
        report = validate_image(image)
        self.assertTrue(report.inner_magic_valid)
        self.assertTrue(report.inner_checksum_valid)
        self.assertTrue(report.signature_valid)
        self.assertTrue(report.roundtrip_exact)

    def test_flcrc_roundtrip(self) -> None:
        header = bytes.fromhex('01 00 00 00 EA 49 5F 6A')
        flcrc = encode_registry_flcrc(header)
        self.assertEqual(len(flcrc), 13)
        self.assertTrue(validate_registry_flcrc(flcrc))
        self.assertEqual(decode_registry_flcrc(flcrc), header)
        self.assertEqual(calculate_registry_flcrc(encode_decoded(make_decoded(header))), flcrc)

    def test_engine_match_ignores_padding_only(self) -> None:
        save_header = bytes.fromhex('01 00 00 00 EA 49 5F 6A')
        registry_header = bytes.fromhex('01 11 22 33 EA 49 5F 6A')
        flcrc = encode_registry_flcrc(registry_header)
        self.assertEqual(identity_engine_key(save_header), identity_engine_key(registry_header))
        self.assertTrue(registry_flcrc_matches_header(flcrc, save_header))
        self.assertFalse(registry_flcrc_matches_header(flcrc, save_header, exact=True))

    def test_engine_match_rejects_key_difference(self) -> None:
        save_header = bytes.fromhex('01 00 00 00 EA 49 5F 6A')
        different = bytes.fromhex('01 00 00 00 EB 49 5F 6A')
        self.assertFalse(registry_flcrc_matches_header(encode_registry_flcrc(different), save_header))

    def test_prepare_encoded_repairs_payload(self) -> None:
        decoded = make_decoded()
        decoded[-10] ^= 0x55
        image = SaveImage(decoded)
        encoded = prepare_encoded(image)
        report = validate_image(load_save_bytes(encoded))
        self.assertTrue(report.inner_checksum_valid)
        self.assertTrue(report.signature_valid)

    def test_payload_only_is_supported(self) -> None:
        decoded = make_decoded()
        image = load_save_bytes(bytes(decoded[HEADER_SIZE:]))
        self.assertEqual(image.source_format, 'payload-only')
        self.assertEqual(len(image.payload), PAYLOAD_SIZE)
        self.assertEqual(bytes(image.header), b'\0' * HEADER_SIZE)

    def test_supplied_u16_tables(self) -> None:
        self.assertEqual(len(parse_u16_table(HERE / 'CARD_ID.bin')), CARD_ENTRY_COUNT)

    def test_card_name_records(self) -> None:
        names, total_records = EditorApp._parse_card_names(HERE / 'card_nameeng.bin')
        self.assertGreaterEqual(total_records, CARD_ENTRY_COUNT)
        self.assertEqual(len(names), CARD_ENTRY_COUNT)
        self.assertEqual(names[0], '')
        self.assertEqual(names[1], 'Earthbound Spirit')
        self.assertEqual(names[2], 'Felgrand Dragon')

    def test_atomic_workflow_shape(self) -> None:
        decoded = make_decoded()
        encoded = encode_decoded(decoded)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'system.dat'
            target.write_bytes(encoded)
            image = load_save_bytes(target.read_bytes(), source_path=target)
            rebuilt = prepare_encoded(image)
            self.assertEqual(rebuilt, encoded)


if __name__ == '__main__':
    unittest.main(verbosity=2)
