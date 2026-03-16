#!/usr/bin/env python3
"""StockAlert - Koersalarm PWA Server"""

import json
import urllib.request
import urllib.parse
import ssl
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = Path(__file__).parent

# SSL context that works with Yahoo Finance
ssl_ctx = ssl.create_default_context()


class StockAlertHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR / "static"), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/quotes"):
            self.handle_quotes()
        else:
            super().do_GET()

    def handle_quotes(self):
        """Proxy Yahoo Finance v8 chart API (no auth required)."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        symbols = params.get("symbols", [""])[0]

        if not symbols:
            self.send_json({"error": "No symbols provided"}, 400)
            return

        result = {}
        symbol_list = symbols.split(",")

        for sym in symbol_list:
            sym = sym.strip()
            if not sym:
                continue
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/"
                f"{urllib.parse.quote(sym)}?interval=1d&range=1d"
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            })
            try:
                with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    chart = data.get("chart", {}).get("result", [])
                    if not chart:
                        continue
                    meta = chart[0].get("meta", {})
                    price = meta.get("regularMarketPrice")
                    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
                    change_pct = None
                    change = None
                    if price and prev and prev != 0:
                        change = price - prev
                        change_pct = (change / prev) * 100

                    result[sym] = {
                        "symbol": sym,
                        "price": price,
                        "previousClose": prev,
                        "changePercent": round(change_pct, 4) if change_pct is not None else None,
                        "change": round(change, 4) if change is not None else None,
                        "currency": meta.get("currency", "USD"),
                        "name": meta.get("shortName", sym),
                    }
            except Exception as e:
                print(f"Error fetching {sym}: {e}")
                continue

        self.send_json(result)

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(format, *args)


def main():
    server = HTTPServer(("0.0.0.0", PORT), StockAlertHandler)
    # Get local IP for phone access
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"\n{'='*50}")
    print(f"  StockAlert Server gestart!")
    print(f"{'='*50}")
    print(f"  Computer:  http://localhost:{PORT}")
    print(f"  iPhone:    http://{local_ip}:{PORT}")
    print(f"{'='*50}")
    print(f"  Open de URL op je iPhone in Safari")
    print(f"  Tik op 'Deel' > 'Zet op beginscherm'")
    print(f"{'='*50}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer gestopt.")
        server.server_close()


if __name__ == "__main__":
    main()
