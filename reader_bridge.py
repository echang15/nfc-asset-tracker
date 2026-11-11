#!/usr/bin/env python3
"""Bridge for PC/SC NFC readers (ACR122U, ACR1252U, Identiv, HID Omnikey ...).

Keyboard-emulation readers do NOT need this script - they type the UID straight
into the browser. PC/SC readers don't, so this script watches the reader and
forwards each tag's UID to the asset tracker, which passes it to the open scan
page (or login page).

    pip install -r requirements-reader.txt
    python reader_bridge.py --url http://127.0.0.1:5000 --key <reader API key>

The API key is shown in the app under Settings -> General.
"""
import argparse
import sys
import time

try:
    import requests
    from smartcard.CardMonitoring import CardMonitor, CardObserver
    from smartcard.Exceptions import CardConnectionException, NoCardException
    from smartcard.System import readers
    from smartcard.util import toHexString
except ImportError:
    sys.exit("Missing dependencies. Run: pip install -r requirements-reader.txt")

# APDU "Get Data" - returns the card UID on PC/SC contactless readers.
GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]


class TagForwarder(CardObserver):
    def __init__(self, url, key, station, reader_filter=None):
        self.url = url.rstrip("/") + "/api/reader/push"
        self.key = key
        self.station = station
        self.reader_filter = (reader_filter or "").lower()

    def update(self, observable, actions):
        added, _removed = actions
        for card in added:
            if self.reader_filter and self.reader_filter not in str(card.reader).lower():
                continue
            uid = self.read_uid(card)
            if uid:
                self.forward(uid)

    @staticmethod
    def read_uid(card):
        try:
            conn = card.createConnection()
            conn.connect()
            data, sw1, sw2 = conn.transmit(GET_UID)
            conn.disconnect()
        except (CardConnectionException, NoCardException) as exc:
            print(f"  ! could not read tag: {exc}")
            return None
        if (sw1, sw2) != (0x90, 0x00):
            print(f"  ! reader returned status {sw1:02X}{sw2:02X}")
            return None
        return toHexString(data).replace(" ", "")

    def forward(self, uid):
        try:
            r = requests.post(
                self.url,
                json={"uid": uid, "station": self.station},
                headers={"X-Api-Key": self.key},
                timeout=5,
            )
            if r.status_code == 401:
                print(f"{time.strftime('%H:%M:%S')}  {uid}  -> rejected: wrong API key")
            else:
                r.raise_for_status()
                print(f"{time.strftime('%H:%M:%S')}  {uid}  -> sent")
        except requests.RequestException as exc:
            print(f"{time.strftime('%H:%M:%S')}  {uid}  -> FAILED ({exc})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:5000", help="asset tracker base URL")
    p.add_argument("--key", required=True, help="reader API key from Settings")
    p.add_argument("--station", default="", help="optional station name for logs")
    p.add_argument("--reader", default="", help="only use readers whose name contains this text")
    args = p.parse_args()

    found = readers()
    if not found:
        print("No PC/SC readers found yet - plug one in (waiting)...")
    for r in found:
        print(f"Reader: {r}")

    monitor = CardMonitor()
    observer = TagForwarder(args.url, args.key, args.station, args.reader)
    monitor.addObserver(observer)
    print(f"Forwarding tag reads to {args.url}. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.deleteObserver(observer)


if __name__ == "__main__":
    main()
