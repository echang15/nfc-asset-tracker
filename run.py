"""Start the asset tracker (Windows, macOS and Linux).

Easiest: double-click ``start-windows.bat`` (Windows) or ``start-mac.command``
(macOS). They set everything up and run:

    python run.py --pcsc --open

Options:
    python run.py                 # http://127.0.0.1:5050 (this computer only)
    python run.py --pcsc          # also start the PC/SC reader bridge (smart-card
                                  # style readers, e.g. 2-in-1 CAC + NFC readers);
                                  # needed for programming tags
    python run.py --open          # open the browser when ready
    python run.py --host 0.0.0.0  # also reachable from other computers on the LAN
"""
import argparse
import sys
import threading
import webbrowser

if sys.version_info < (3, 9):
    sys.exit("Python 3.9 or newer is required (3.12 recommended). "
             "Download it from https://www.python.org/downloads/")

from app import create_app  # noqa: E402

app = create_app()


# Not 5000: macOS 12+ uses port 5000 for AirPlay Receiver.
DEFAULT_PORT = 5050


def port_is_free(host, port):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1" if host in ("0.0.0.0", "localhost") else host, port))
        except OSError:
            return False
    return True


def find_free_port(host, start):
    for port in range(start, start + 20):
        if port_is_free(host, port):
            if port != start:
                print(f"Port {start} is busy, using {port} instead.")
            return port
    sys.exit(f"No free port found between {start} and {start + 19}.")


def start_bridge(args):
    """Run reader_bridge in a background thread. Returns True if started."""
    try:
        import smartcard  # noqa: F401

        import reader_bridge
    except ImportError:
        print("! Reader bridge not started: pyscard is not installed.\n"
              "  Run: pip install -r requirements-reader.txt\n"
              "  (Keyboard-type readers still work; smart-card readers and tag "
              "programming need the bridge.)")
        return False
    from app.models import get_setting

    with app.app_context():
        key = get_setting("reader_api_key")
    host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{host}:{args.port}"

    def run():
        try:
            reader_bridge.start(url, key, args.reader)
        except Exception as exc:  # e.g. smart-card service not available
            print(f"! Reader bridge stopped: {exc}\n"
                  "  Windows: make sure the 'Smart Card' service is running.\n"
                  "  Linux: sudo apt install pcscd && sudo systemctl start pcscd")

    threading.Thread(target=run, daemon=True, name="pcsc-bridge").start()
    return True


def main():
    parser = argparse.ArgumentParser(description="NFC asset tracker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None,
                        help=f"default {DEFAULT_PORT}, or the next free port")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--pcsc", action="store_true",
                        help="start the PC/SC reader bridge in the background")
    parser.add_argument("--reader", default="",
                        help="with --pcsc: only use readers whose name contains this")
    parser.add_argument("--open", action="store_true",
                        help="open the app in the default browser")
    args = parser.parse_args()
    if args.port is None:
        args.port = find_free_port(args.host, DEFAULT_PORT)
    elif not port_is_free(args.host, args.port):
        sys.exit(f"Port {args.port} is already in use. Is the app already running? "
                 "Choose another with --port.")

    browse_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{browse_host}:{args.port}"
    print("=" * 64)
    print(f"  Asset Tracker running at {url}")
    print("  Keep this window open while using the app. Ctrl+C to stop.")
    print("=" * 64)

    if args.pcsc:
        start_bridge(args)
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()

    try:
        from waitress import serve  # production-grade server, all platforms
    except ImportError:
        serve = None
    if serve and not args.debug:
        serve(app, host=args.host, port=args.port, threads=8, _quiet=True)
    else:
        # The debug reloader would start a second copy of the reader bridge.
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True,
                use_reloader=args.debug and not args.pcsc)


if __name__ == "__main__":
    main()
