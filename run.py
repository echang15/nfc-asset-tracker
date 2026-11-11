"""Start the asset tracker.

    python run.py                 # http://127.0.0.1:5000 (this laptop only)
    python run.py --host 0.0.0.0  # also reachable from other machines on the LAN
"""
import argparse

from app import create_app

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NFC asset tracker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    try:
        from waitress import serve  # optional production-grade server
    except ImportError:
        serve = None
    if serve and not args.debug:
        print(f"Serving on http://{args.host}:{args.port}")
        serve(app, host=args.host, port=args.port, threads=8)
    else:
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
