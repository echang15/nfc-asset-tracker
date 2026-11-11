"""Reading and writing NFC Forum Type 2 tags (NTAG213/215/216, MIFARE
Ultralight) through a PC/SC contactless reader.

Everything here works on a ``transmit(apdu) -> (data, sw1, sw2)`` callable, so
it has no dependency on pyscard and can be tested with a simulated tag.

Tags are programmed with a single NDEF Text record such as ``asset:A-00001``
or ``user:alice``. Any phone can read that text. The app itself still
identifies tags by their factory UID (which cannot be rewritten), so writing
fake text onto a tag does not let anyone impersonate a user.
"""

# PC/SC part 3 pseudo-APDUs for storage cards.
GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]


def apdu_read(page, length=16):
    return [0xFF, 0xB0, 0x00, page & 0xFF, length]


def apdu_write(page, data4):
    return [0xFF, 0xD6, 0x00, page & 0xFF, 0x04, *data4]


class TagError(Exception):
    """A problem the user can fix (wrong tag type, locked tag, moved too fast...)."""


# --------------------------------------------------------------------------
# Tag identification
# --------------------------------------------------------------------------

# PC/SC part 3 "card name" (NN) values from the ATR.
CARD_NAMES = {
    0x0001: "MIFARE Classic 1K",
    0x0002: "MIFARE Classic 4K",
    0x0003: "MIFARE Ultralight / NTAG",
    0x0026: "MIFARE Mini",
    0x003A: "MIFARE Ultralight C",
    0x0036: "MIFARE Classic 1K",
    0x0037: "MIFARE Classic 4K",
}
TYPE2_NAMES = {0x0003, 0x003A}


def card_name_from_atr(atr):
    """Return the PC/SC 'card name' code from a contactless storage-card ATR.

    Layout: 3B 8F 80 01 80 4F 0C A0 00 00 03 06 SS NN NN 00 00 00 00 TCK.
    Returns None for other ATRs (e.g. ISO 14443-4 cards such as DESFire,
    bank cards and phones).
    """
    atr = list(atr or [])
    if len(atr) >= 15 and atr[4:7] == [0x80, 0x4F, 0x0C] and atr[7:12] == [0xA0, 0x00, 0x00, 0x03, 0x06]:
        return (atr[13] << 8) | atr[14]
    return None


def describe_atr(atr):
    nn = card_name_from_atr(atr)
    if nn is None:
        return "ISO 14443-4 card (DESFire, bank card, phone...)"
    return CARD_NAMES.get(nn, f"storage card 0x{nn:04X}")


def is_type2(atr):
    """True if the tag is (or may be) an NFC Forum Type 2 tag."""
    return card_name_from_atr(atr) in TYPE2_NAMES


def format_uid(data):
    return "".join(f"{b:02X}" for b in data)


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


def _check(sw1, sw2, what):
    if (sw1, sw2) != (0x90, 0x00):
        if (sw1, sw2) in ((0x63, 0x00), (0x65, 0x81), (0x69, 0x82)):
            raise TagError(f"{what} was refused (status {sw1:02X}{sw2:02X}). "
                           "The tag may be locked or read-only.")
        raise TagError(f"{what} failed (status {sw1:02X}{sw2:02X}). "
                       "Hold the tag still on the reader and try again.")


def read_uid(transmit):
    data, sw1, sw2 = transmit(GET_UID)
    _check(sw1, sw2, "Reading the tag UID")
    if not data:
        raise TagError("The reader returned an empty UID.")
    return format_uid(data)


def read_pages(transmit, page, count):
    """Read ``count`` 4-byte pages starting at ``page``."""
    out = []
    while len(out) < count * 4:
        data, sw1, sw2 = transmit(apdu_read(page + len(out) // 4))
        _check(sw1, sw2, f"Reading page {page + len(out) // 4}")
        if len(data) < 4:
            raise TagError("The reader returned a short read.")
        out.extend(data[:16])
    return out[: count * 4]


def write_page(transmit, page, data4):
    _, sw1, sw2 = transmit(apdu_write(page, data4))
    _check(sw1, sw2, f"Writing page {page}")


# --------------------------------------------------------------------------
# NDEF encoding
# --------------------------------------------------------------------------

URI_PREFIXES = ["", "http://www.", "https://www.", "http://", "https://", "tel:", "mailto:"]


def encode_text_record(text, lang="en"):
    payload = bytes([len(lang)]) + lang.encode("ascii") + text.encode("utf-8")
    if len(payload) > 255:
        raise TagError("Text is too long for a tag.")
    # MB=1 ME=1 SR=1 TNF=1 (well-known), type "T"
    return bytes([0xD1, 0x01, len(payload)]) + b"T" + payload


def encode_ndef_tlv(message):
    if len(message) < 0xFF:
        tlv = bytes([0x03, len(message)]) + message
    else:
        tlv = bytes([0x03, 0xFF, len(message) >> 8, len(message) & 0xFF]) + message
    tlv += b"\xFE"
    if len(tlv) % 4:
        tlv += b"\x00" * (4 - len(tlv) % 4)
    return tlv


def decode_first_record(message):
    """Return the first record of an NDEF message as text, or None."""
    if len(message) < 3:
        return None
    header = message[0]
    tnf = header & 0x07
    short = bool(header & 0x10)
    has_id = bool(header & 0x08)
    type_len = message[1]
    pos = 2
    if short:
        payload_len = message[pos]
        pos += 1
    else:
        payload_len = int.from_bytes(message[pos:pos + 4], "big")
        pos += 4
    id_len = 0
    if has_id:
        id_len = message[pos]
        pos += 1
    rtype = bytes(message[pos:pos + type_len])
    pos += type_len + id_len
    payload = bytes(message[pos:pos + payload_len])
    if tnf == 0x01 and rtype == b"T" and payload:
        lang_len = payload[0] & 0x3F
        encoding = "utf-16" if payload[0] & 0x80 else "utf-8"
        return payload[1 + lang_len:].decode(encoding, errors="replace")
    if tnf == 0x01 and rtype == b"U" and payload:
        prefix = URI_PREFIXES[payload[0]] if payload[0] < len(URI_PREFIXES) else ""
        return prefix + payload[1:].decode("utf-8", errors="replace")
    return None


def locate_ndef(data):
    """Walk the TLV blocks of a Type 2 tag data area (starting at page 4).

    Returns (start, length) of the NDEF message, None if there is none, or
    "more" if ``data`` is too short to tell yet.
    """
    pos = 0
    while True:
        if pos >= len(data):
            return "more"
        t = data[pos]
        if t == 0x00:  # NULL TLV
            pos += 1
            continue
        if t == 0xFE:  # terminator
            return None
        if pos + 1 >= len(data):
            return "more"
        length = data[pos + 1]
        pos += 2
        if length == 0xFF:
            if pos + 1 >= len(data):
                return "more"
            length = (data[pos] << 8) | data[pos + 1]
            pos += 2
        if t == 0x03:
            return pos, length
        pos += length  # lock / memory control / proprietary TLVs


def find_ndef_message(data):
    loc = locate_ndef(data)
    if not isinstance(loc, tuple):
        return None
    start, length = loc
    return bytes(data[start:start + length])


# --------------------------------------------------------------------------
# High level
# --------------------------------------------------------------------------


def read_capability(transmit):
    """Return (capacity_bytes, writable) from the Capability Container (page 3)."""
    cc = read_pages(transmit, 3, 1)
    if cc[0] != 0xE1:
        return None, None
    return cc[2] * 8, (cc[3] & 0x0F) == 0x00


def read_text(transmit, atr=None):
    """Read the first NDEF record of a Type 2 tag as text. Returns None if the
    tag is blank, not NDEF-formatted or not a Type 2 tag."""
    if atr is not None and not is_type2(atr):
        return None
    try:
        capacity, _ = read_capability(transmit)
        if not capacity:
            return None
        data = []
        while len(data) < capacity:
            data.extend(read_pages(transmit, 4 + len(data) // 4, 4))
            loc = locate_ndef(data)
            if loc is None:
                return None
            if loc != "more" and loc[0] + loc[1] <= len(data):
                return decode_first_record(find_ndef_message(data))
        return None
    except (TagError, IndexError, ValueError):
        return None


def write_text(transmit, text, atr=None):
    """Write ``text`` as a single NDEF Text record and verify it.

    Raises TagError with a user-friendly message on failure.
    """
    if atr is not None and not is_type2(atr):
        raise TagError(
            f"This tag is a {describe_atr(atr)}. Only NTAG213/215/216 and "
            "MIFARE Ultralight tags can be programmed. Use NTAG215 stickers."
        )
    cc = read_pages(transmit, 3, 1)
    if cc == [0, 0, 0, 0]:
        # Unformatted Ultralight: write a Capability Container (one-time
        # programmable). 0x06 * 8 = 48 bytes, the smallest Type 2 data area.
        write_page(transmit, 3, [0xE1, 0x10, 0x06, 0x00])
        cc = [0xE1, 0x10, 0x06, 0x00]
    if cc[0] != 0xE1:
        raise TagError("This tag is not NFC Forum (NDEF) formatted and cannot "
                       "be programmed. Use NTAG213/215/216 tags.")
    if cc[3] & 0x0F:  # low nibble = write access (0x0 = writable)
        raise TagError("This tag has been made read-only and cannot be reprogrammed.")
    capacity = cc[2] * 8
    tlv = encode_ndef_tlv(encode_text_record(text))
    if len(tlv) > capacity:
        raise TagError(f"The text needs {len(tlv)} bytes but the tag only holds {capacity}.")
    for i in range(0, len(tlv), 4):
        write_page(transmit, 4 + i // 4, list(tlv[i:i + 4]))
    # Verify.
    back = read_pages(transmit, 4, len(tlv) // 4)
    if bytes(back) != tlv:
        raise TagError("Verification failed - the tag moved during writing. Tap it again.")
    if decode_first_record(find_ndef_message(back)) != text:
        raise TagError("Verification failed - the tag content does not match.")
    return True
