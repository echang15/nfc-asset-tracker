#!/usr/bin/env python3
"""Bridge for PC/SC NFC readers.

Works with any reader that shows up as a standard smart-card (CCID / PC/SC)
reader, including 2-in-1 "CAC + NFC" contact/contactless readers (Arsandyn and
similar), ACR122U, ACR1252U, Identiv and HID Omnikey.

Keyboard-emulation readers do NOT need this script - they type the UID straight
into the browser. PC/SC readers don't, so this script watches the reader and
forwards each tag's UID to the asset tracker, which passes it to the open scan
page (or login page).

    pip install -r requirements-reader.txt
    python reader_bridge.py --list                       # show detected readers
    python reader_bridge.py --key <reader API key>       # start forwarding

The API key is shown in the app under Settings -> General. It can also be put in
the ASSET_TRACKER_KEY environment variable.

2-in-1 readers expose two readers (a contact slot and a contactless antenna).
The bridge automatically listens to the contactless one and ignores cards
inserted into the contact slot (CAC, bank cards...). Use --reader to pick a
reader by name yourself.
"""
import argparse
import os
import re
import sys
import time

# APDU "Get Data" (PC/SC part 3) - returns the UID of the card on the antenna.
GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]

# Reader names that indicate the contactless interface of a combo reader.
CONTACTLESS_HINT = re.compile(r"picc|contactless|\bcl\b|nfc|rfid|\bpcd\b", re.I)
CONTACT_HINT = re.compile(r"\bsam\b|\bicc\b|contact(?!less)", re.I)


def is_contactless_atr(atr):
    """True for the synthetic ATR a PC/SC reader builds for a contactless card.

    PC/SC part 3 contactless ATRs start 3B 8x 80 01; contact cards (CAC/PIV,
    EMV bank cards) carry their own ATR, which does not have this shape.
    """
    atr = list(atr or [])
    return (len(atr) >= 4 and atr[0] == 0x3B and (atr[1] & 0xF0) == 0x80
            and atr[2] == 0x80 and atr[3] == 0x01)


def choose_readers(names, wanted=""):
    """Pick the reader names to listen to.

    With ``wanted`` set, return readers whose name contains it. Otherwise prefer
    readers that look contactless; if no name says so, use every reader except
    ones that clearly are contact-only / SAM slots (the ATR check still filters
    contact cards).
    """
    names = [str(n) for n in names]
    if wanted:
        return [n for n in names if wanted.lower() in n.lower()]
    contactless = [n for n in names
                   if CONTACTLESS_HINT.search(n) and not CONTACT_HINT.search(n)]
    if contactless:
        return contactless
    usable = [n for n in names if not CONTACT_HINT.search(n)]
    return usable or names


def format_uid(data):
    return "".join(f"{b:02X}" for b in data)


def _stamp():
    return time.strftime("%H:%M:%S")


class Forwarder:
    def __init__(self, url, key, station=""):
        import requests

        self.requests = requests
        self.url = url.rstrip("/") + "/api/reader/push"
        self.key = key
        self.station = station

    def send(self, uid):
        for attempt in range(3):
            try:
                r = self.requests.post(
                    self.url, json={"uid": uid, "station": self.station},
                    headers={"X-Api-Key": self.key}, timeout=5,
                )
                if r.status_code == 401:
                    print(f"{_stamp()}  {uid}  -> rejected: wrong API key "
                          "(copy it from Settings in the app)")
                    return False
                r.raise_for_status()
                print(f"{_stamp()}  {uid}  -> sent")
                return True
            except self.requests.RequestException as exc:
                if attempt == 2:
                    print(f"{_stamp()}  {uid}  -> FAILED, is the app running? ({exc})")
                else:
                    time.sleep(0.5)
        return False


def make_observer(forwarder, allowed, verbose=False):
    from smartcard.CardMonitoring import CardObserver
    from smartcard.Exceptions import CardConnectionException, NoCardException

    class TagObserver(CardObserver):
        def update(self, observable, actions):
            added, _removed = actions
            for card in added:
                reader = str(card.reader)
                if reader not in allowed:
                    if verbose:
                        print(f"{_stamp()}  (ignored card on '{reader}')")
                    continue
                if not is_contactless_atr(card.atr):
                    print(f"{_stamp()}  Contact card in '{reader}' ignored - "
                          "tap NFC tags on the contactless antenna instead.")
                    continue
                uid = self.read_uid(card)
                if uid:
                    forwarder.send(uid)

        @staticmethod
        def read_uid(card):
            try:
                conn = card.createConnection()
                conn.connect()
                try:
                    data, sw1, sw2 = conn.transmit(GET_UID)
                finally:
                    conn.disconnect()
            except (CardConnectionException, NoCardException) as exc:
                print(f"{_stamp()}  ! tag moved away too fast, tap again ({exc})")
                return None
            if (sw1, sw2) != (0x90, 0x00) or not data:
                print(f"{_stamp()}  ! reader could not return a UID "
                      f"(status {sw1:02X}{sw2:02X})")
                return None
            return format_uid(data)

    return TagObserver()


def start(url, key, reader_filter="", station="", verbose=False, names=None):
    """Start forwarding tag reads in the background; returns the CardMonitor.

    Waits until a reader is plugged in. Used by main() and by
    ``python run.py --pcsc``.
    """
    from smartcard.CardMonitoring import CardMonitor
    from smartcard.System import readers

    while not names:
        try:
            names = [str(r) for r in readers()]
        except Exception:
            names = []
        if not names:
            print("No smart-card readers found - plug the reader in (retrying)...")
            time.sleep(3)

    allowed = set(choose_readers(names, reader_filter))
    if not allowed:
        print(f"No reader matches --reader '{reader_filter}'. Found: {names}")
        return None
    for n in names:
        print(f"{'Listening' if n in allowed else 'Ignoring '}: {n}")

    observer = make_observer(Forwarder(url, key, station), allowed, verbose)
    monitor = CardMonitor()
    monitor.addObserver(observer)
    print(f"Forwarding tag reads to {url}.")
    return monitor


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("ASSET_TRACKER_URL", "http://127.0.0.1:5000"),
                   help="asset tracker base URL (default %(default)s)")
    p.add_argument("--key", default=os.environ.get("ASSET_TRACKER_KEY"),
                   help="reader API key from Settings (or ASSET_TRACKER_KEY)")
    p.add_argument("--station", default="", help="optional station name for logs")
    p.add_argument("--reader", default="",
                   help="only use readers whose name contains this text")
    p.add_argument("--list", action="store_true", help="list readers and exit")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    try:
        from smartcard.System import readers
    except ImportError:
        sys.exit("Missing dependencies. Run: pip install -r requirements-reader.txt")

    try:
        names = [str(r) for r in readers()]
    except Exception as exc:  # pcscd not running, etc.
        sys.exit(f"Could not talk to the smart-card service: {exc}\n"
                 "Linux: sudo apt install pcscd && sudo systemctl start pcscd")

    if args.list:
        chosen = choose_readers(names, args.reader)
        if not names:
            print("No readers found. Is the reader plugged in?")
        for n in names:
            print(f"{'*' if n in chosen else ' '} {n}")
        if names:
            print("\n* = readers the bridge would listen to")
        return

    if not args.key:
        p.error("--key is required (find it under Settings in the app)")

    if start(args.url, args.key, args.reader, args.station, args.verbose, names) is None:
        sys.exit(1)
    print("Tap a tag to test. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
