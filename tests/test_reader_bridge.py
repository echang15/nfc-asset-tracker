"""Hardware-free tests for reader_bridge.py helper logic."""
from reader_bridge import choose_readers, format_uid, is_contactless_atr


def hexbytes(s):
    return [int(b, 16) for b in s.split()]


def test_contactless_atr_detection():
    ntag = hexbytes("3B 8F 80 01 80 4F 0C A0 00 00 03 06 03 00 03 00 00 00 00 68")
    desfire = hexbytes("3B 81 80 01 80 80")
    cac = hexbytes("3B DB 96 00 80 1F 03 00 31 C0 64 B0 F3 10 00 07 90 00 80")
    emv = hexbytes("3B 68 00 00 00 73 C8 40 12 00 90 00")
    assert is_contactless_atr(ntag)
    assert is_contactless_atr(desfire)
    assert not is_contactless_atr(cac)
    assert not is_contactless_atr(emv)
    assert not is_contactless_atr([])


def test_choose_readers_prefers_contactless_interface():
    names = ["ACS ACR1252 1S CL Reader PICC 0", "ACS ACR1252 1S CL Reader SAM 0"]
    assert choose_readers(names) == ["ACS ACR1252 1S CL Reader PICC 0"]
    names = ["Generic Smart Card Reader Interface", "Generic Contactless Reader"]
    assert choose_readers(names) == ["Generic Contactless Reader"]


def test_choose_readers_fallbacks():
    # No hint in the names: keep everything (the ATR check filters contact cards).
    names = ["Generic USB2.0-CRW 0", "Generic USB2.0-CRW 1"]
    assert choose_readers(names) == names
    # Explicit filter wins.
    assert choose_readers(names, "crw 1") == ["Generic USB2.0-CRW 1"]
    assert choose_readers(names, "nope") == []


def test_format_uid():
    assert format_uid([0x04, 0xA1, 0x0B]) == "04A10B"
