#!/usr/bin/env python3
"""StockAlert - Koersalarm PWA Server"""

import json
import urllib.request
import urllib.parse
import ssl
import os
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = Path(__file__).parent
ALERTS_FILE = Path(__file__).parent / "alerts.json"

# SSL context that works with Yahoo Finance
ssl_ctx = ssl.create_default_context()

# ── Background monitor ─────────────────────────────────────────────

monitor_thread = None
monitor_running = False
monitor_lock = threading.Lock()


def load_config():
    with open(ALERTS_FILE) as f:
        return json.load(f)


def save_config(config):
    with open(ALERTS_FILE, "w") as f:
        json.dump(config, f, indent=2)


def monitor_loop():
    """Background thread that runs check_alerts periodically."""
    global monitor_running
    # Import check_alerts from stock_monitor
    sys.path.insert(0, str(BASE_DIR))
    from stock_monitor import check_alerts

    while monitor_running:
        try:
            config = load_config()
            interval = config["settings"].get("check_interval_seconds", 60)
            check_alerts()
        except Exception as e:
            print(f"Monitor error: {e}")
            interval = 60
        # Sleep in small increments so we can stop quickly
        for _ in range(int(interval)):
            if not monitor_running:
                break
            time.sleep(1)
    print("Monitor stopped.")


def start_monitor():
    global monitor_thread, monitor_running
    with monitor_lock:
        if monitor_running:
            return False  # already running
        monitor_running = True
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        return True


def stop_monitor():
    global monitor_running
    with monitor_lock:
        if not monitor_running:
            return False  # not running
        monitor_running = False
        return True


# ── HTTP Handler ───────────────────────────────────────────────────


class StockAlertHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR / "static"), **kwargs)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/quotes"):
            self.handle_quotes()
        elif self.path == "/api/alerts":
            self.handle_get_alerts()
        elif self.path == "/api/monitor/status":
            self.handle_monitor_status()
        else:
            super().do_GET()

    def do_POST(self):
        body = self.read_body()
        if self.path == "/api/alerts":
            self.handle_add_alert(body)
        elif self.path == "/api/settings":
            self.handle_update_settings(body)
        elif self.path == "/api/alerts/toggle":
            self.handle_toggle_alert(body)
        elif self.path == "/api/alerts/reset":
            self.handle_reset_alert(body)
        elif self.path == "/api/test":
            self.handle_test_notification()
        elif self.path == "/api/monitor/start":
            self.handle_monitor_start()
        elif self.path == "/api/monitor/stop":
            self.handle_monitor_stop()
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        body = self.read_body()
        if self.path == "/api/alerts":
            self.handle_delete_alert(body)
        else:
            self.send_json({"error": "Not found"}, 404)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except Exception:
            return {}

    # ── Alert endpoints ────────────────────────────────────────────

    def handle_get_alerts(self):
        try:
            config = load_config()
            self.send_json(config)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_add_alert(self, body):
        symbol = body.get("symbol", "").upper().strip()
        condition = body.get("condition", "").lower().strip()
        target_price = body.get("target_price")

        if not symbol or condition not in ("above", "below") or target_price is None:
            self.send_json({"error": "Missing or invalid fields: symbol, condition (above/below), target_price"}, 400)
            return

        try:
            target_price = float(target_price)
        except (ValueError, TypeError):
            self.send_json({"error": "target_price must be a number"}, 400)
            return

        try:
            config = load_config()
            # Remove existing alert for same symbol+condition
            config["alerts"] = [
                a for a in config["alerts"]
                if not (a["symbol"] == symbol and a["condition"] == condition)
            ]
            config["alerts"].append({
                "symbol": symbol,
                "name": body.get("name", symbol),
                "condition": condition,
                "target_price": target_price,
                "enabled": True,
                "triggered": False,
            })
            save_config(config)
            self.send_json({"ok": True, "message": f"Alert added: {symbol} {condition} {target_price}"})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_delete_alert(self, body):
        symbol = body.get("symbol", "").upper().strip()
        condition = body.get("condition", "").lower().strip()

        if not symbol:
            self.send_json({"error": "Missing symbol"}, 400)
            return

        try:
            config = load_config()
            before = len(config["alerts"])
            if condition:
                config["alerts"] = [
                    a for a in config["alerts"]
                    if not (a["symbol"] == symbol and a["condition"] == condition)
                ]
            else:
                config["alerts"] = [a for a in config["alerts"] if a["symbol"] != symbol]

            if len(config["alerts"]) < before:
                save_config(config)
                self.send_json({"ok": True, "message": f"Alert deleted: {symbol}"})
            else:
                self.send_json({"error": f"No alert found for {symbol}"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_toggle_alert(self, body):
        symbol = body.get("symbol", "").upper().strip()
        condition = body.get("condition", "").lower().strip()
        enabled = body.get("enabled")

        if not symbol or not condition or enabled is None:
            self.send_json({"error": "Missing fields: symbol, condition, enabled"}, 400)
            return

        try:
            config = load_config()
            found = False
            for alert in config["alerts"]:
                if alert["symbol"] == symbol and alert["condition"] == condition:
                    alert["enabled"] = bool(enabled)
                    found = True
                    break
            if found:
                save_config(config)
                self.send_json({"ok": True, "message": f"Alert {'enabled' if enabled else 'disabled'}: {symbol}"})
            else:
                self.send_json({"error": f"No alert found for {symbol} {condition}"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_reset_alert(self, body):
        symbol = body.get("symbol", "").upper().strip()
        condition = body.get("condition", "").lower().strip()

        if not symbol or not condition:
            self.send_json({"error": "Missing fields: symbol, condition"}, 400)
            return

        try:
            config = load_config()
            found = False
            for alert in config["alerts"]:
                if alert["symbol"] == symbol and alert["condition"] == condition:
                    alert["triggered"] = False
                    alert["enabled"] = True
                    found = True
                    break
            if found:
                save_config(config)
                self.send_json({"ok": True, "message": f"Alert reset: {symbol}"})
            else:
                self.send_json({"error": f"No alert found for {symbol} {condition}"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_update_settings(self, body):
        try:
            config = load_config()
            settings = config["settings"]
            if "channel" in body:
                if body["channel"] in ("imessage", "signal", "whatsapp", "ntfy"):
                    settings["channel"] = body["channel"]
            if "phone_number" in body:
                settings["phone_number"] = body["phone_number"]
            if "ntfy_topic" in body:
                settings["ntfy_topic"] = body["ntfy_topic"]
            if "check_interval_seconds" in body:
                try:
                    settings["check_interval_seconds"] = int(body["check_interval_seconds"])
                except (ValueError, TypeError):
                    pass
            save_config(config)
            self.send_json({"ok": True, "message": "Settings updated"})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_test_notification(self):
        try:
            sys.path.insert(0, str(BASE_DIR))
            from stock_monitor import send_notification
            from datetime import datetime
            config = load_config()
            settings = config["settings"]
            msg = f"StockAlert test - {datetime.now().strftime('%H:%M:%S')}\nYour notifications are working!"
            success = send_notification(settings, msg)
            if success:
                self.send_json({"ok": True, "message": "Test notification sent"})
            else:
                self.send_json({"error": "Failed to send notification. Check your settings."}, 500)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    # ── Monitor endpoints ──────────────────────────────────────────

    def handle_monitor_start(self):
        if start_monitor():
            self.send_json({"ok": True, "running": True, "message": "Monitor started"})
        else:
            self.send_json({"ok": True, "running": True, "message": "Monitor already running"})

    def handle_monitor_stop(self):
        if stop_monitor():
            self.send_json({"ok": True, "running": False, "message": "Monitor stopped"})
        else:
            self.send_json({"ok": True, "running": False, "message": "Monitor was not running"})

    def handle_monitor_status(self):
        self.send_json({"running": monitor_running})

    # ── Quotes (existing) ─────────────────────────────────────────

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

    # ── Helpers ────────────────────────────────────────────────────

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
    print(f"  Alerts UI: http://localhost:{PORT}/alerts.html")
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
