"""Tests for NDEF read/write against a simulated NTAG / Ultralight tag."""
import pytest

from nfc_tags import (
    TagError,
    card_name_from_atr,
    decode_first_record,
    encode_ndef_tlv,
    encode_text_record,
    find_ndef_message,
    is_type2,
    read_text,
    read_uid,
    write_text,
)

NTAG_ATR = [0x3B, 0x8F, 0x80, 0x01, 0x80, 0x4F, 0x0C, 0xA0, 0x00, 0x00, 0x03, 0x06,
            0x03, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x68]
CLASSIC_ATR = NTAG_ATR[:13] + [0x00, 0x01] + NTAG_ATR[15:]
DESFIRE_ATR = [0x3B, 0x81, 0x80, 0x01, 0x80, 0x80]


class FakeTag:
    """Simulates a Type 2 tag behind a PC/SC reader (FF CA / FF B0 / FF D6)."""

    def __init__(self, pages=135, cc=(0xE1, 0x10, 0x3E, 0x00), uid=(0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66),
                 fail_write_at=None):
        self.mem = [[0, 0, 0, 0] for _ in range(pages)]
        self.mem[3] = list(cc)
        self.uid = list(uid)
        self.fail_write_at = fail_write_at
        self.writes = 0

    def transmit(self, apdu):
        cla, ins, _p1, p2 = apdu[:4]
        assert cla == 0xFF
        if ins == 0xCA:
            return self.uid, 0x90, 0x00
        if ins == 0xB0:
            if p2 >= len(self.mem):
                return [], 0x6A, 0x82
            out = []
            for i in range(4):
                out += self.mem[(p2 + i) % len(self.mem)]
            return out[: apdu[4]], 0x90, 0x00
        if ins == 0xD6:
            self.writes += 1
            if self.fail_write_at is not None and self.writes == self.fail_write_at:
                return [], 0x63, 0x00
            data = apdu[5:9]
            if p2 < 3 or p2 >= len(self.mem):
                return [], 0x63, 0x00
            if p2 == 3:  # OTP: bits can only be set
                self.mem[3] = [a | b for a, b in zip(self.mem[3], data)]
            else:
                self.mem[p2] = list(data)
            return [], 0x90, 0x00
        return [], 0x6D, 0x00


def test_atr_parsing():
    assert card_name_from_atr(NTAG_ATR) == 0x0003
    assert is_type2(NTAG_ATR)
    assert not is_type2(CLASSIC_ATR)
    assert card_name_from_atr(DESFIRE_ATR) is None


def test_ndef_roundtrip_encoding():
    tlv = encode_ndef_tlv(encode_text_record("asset:A-00001"))
    assert tlv[0] == 0x03 and len(tlv) % 4 == 0
    assert decode_first_record(find_ndef_message(list(tlv))) == "asset:A-00001"


def test_write_and_read_ntag():
    tag = FakeTag()
    assert read_uid(tag.transmit) == "04112233445566"
    assert read_text(tag.transmit, NTAG_ATR) is None  # blank
    write_text(tag.transmit, "asset:A-00042", NTAG_ATR)
    assert read_text(tag.transmit, NTAG_ATR) == "asset:A-00042"
    # Rewriting with something shorter/longer works.
    write_text(tag.transmit, "user:alice", NTAG_ATR)
    assert read_text(tag.transmit, NTAG_ATR) == "user:alice"
    long_text = "asset:" + "X" * 100
    write_text(tag.transmit, long_text, NTAG_ATR)
    assert read_text(tag.transmit, NTAG_ATR) == long_text


def test_unformatted_ultralight_gets_capability_container():
    tag = FakeTag(pages=16, cc=(0, 0, 0, 0))
    write_text(tag.transmit, "asset:A-1", NTAG_ATR)
    assert tag.mem[3] == [0xE1, 0x10, 0x06, 0x00]
    assert read_text(tag.transmit, NTAG_ATR) == "asset:A-1"


def test_rejections():
    with pytest.raises(TagError, match="MIFARE Classic"):
        write_text(FakeTag().transmit, "asset:A-1", CLASSIC_ATR)
    with pytest.raises(TagError, match="Only NTAG"):
        write_text(FakeTag().transmit, "asset:A-1", DESFIRE_ATR)
    with pytest.raises(TagError, match="read-only"):
        write_text(FakeTag(cc=(0xE1, 0x10, 0x3E, 0x0F)).transmit, "asset:A-1", NTAG_ATR)
    with pytest.raises(TagError, match="only holds"):
        write_text(FakeTag(pages=16, cc=(0xE1, 0x10, 0x06, 0x00)).transmit, "asset:" + "X" * 60, NTAG_ATR)
    with pytest.raises(TagError, match="refused"):
        write_text(FakeTag(fail_write_at=2).transmit, "asset:A-1", NTAG_ATR)
