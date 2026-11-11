"""Start the asset tracker.

    python run.py                 # http://127.0.0.1:5000 (this laptop only)
    python run.py --host 0.0.0.0  # also reachable from other machines on the LAN
    python run.py --pcsc          # also start the PC/SC reader bridge (smart-card
                                  # style readers, e.g. 2-in-1 CAC + NFC readers)
"""
import argparse
import threading

from app import create_app

app = create_app()


def start_bridge(args):
    import reader_bridge
    from app.models import get_setting

    with app.app_context():
        key = get_setting("reader_api_key")
    host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{host}:{args.port}"
    try:
        import smartcard  # noqa: F401
    except ImportError:
        raise SystemExit("--pcsc needs: pip install -r requirements-reader.txt")
    threading.Thread(
        target=reader_bridge.start, args=(url, key, args.reader), daemon=True,
        name="pcsc-bridge",
    ).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NFC asset tracker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--pcsc", action="store_true",
                        help="start the PC/SC reader bridge in the background")
    parser.add_argument("--reader", default="",
                        help="with --pcsc: only use readers whose name contains this")
    args = parser.parse_args()
    if args.pcsc:
        start_bridge(args)
    try:
        from waitress import serve  # optional production-grade server
    except ImportError:
        serve = None
    if serve and not args.debug:
        print(f"Serving on http://{args.host}:{args.port}")
        serve(app, host=args.host, port=args.port, threads=8)
    else:
        # The debug reloader would start a second copy of the reader bridge.
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True,
                use_reloader=args.debug and not args.pcsc)
