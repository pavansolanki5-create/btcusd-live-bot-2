"""
ORDER PLACEMENT TEST SCRIPT
Delta Exchange India - BTCUSD

This script:
1. Fetches live BTCUSD mark price
2. Places ONE real BUY limit order with SL and TP
3. Shows full API response
4. Cancels the order immediately after

Run this to verify API keys work for trading.
"""

import time
import json
import hmac
import hashlib
import os
import urllib.request
from datetime import datetime

# ── API KEYS ────────────────────────────────
API_KEY    = os.environ.get("API_KEY",    "")
API_SECRET = os.environ.get("API_SECRET", "")
BASE_URL   = "https://api.india.delta.exchange"


def log(msg):
    print("[{}] {}".format(
        datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def sign(method, path, query, body):
    ts  = str(int(time.time()))
    msg = method + ts + path + query + body
    sig = hmac.new(
        API_SECRET.encode(),
        msg.encode(),
        hashlib.sha256
    ).hexdigest()
    return ts, sig

def auth_headers(method, path, query="", body=""):
    ts, sig = sign(method, path, query, body)
    return {
        "api-key":      API_KEY,
        "timestamp":    ts,
        "signature":    sig,
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "User-Agent":   "Mozilla/5.0"
    }

def http_pub(url):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [ERR] {}".format(e))
        return None

def http_get(path, query=""):
    try:
        h   = auth_headers("GET", path, query)
        req = urllib.request.Request(
            BASE_URL + path + query, headers=h)
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [ERR] {}".format(e))
        return None

def http_post(path, payload):
    try:
        body = json.dumps(payload)
        h    = auth_headers("POST", path, "", body)
        req  = urllib.request.Request(
            BASE_URL + path,
            data=body.encode(),
            headers=h,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [ERR] {}".format(e))
        return None

def http_delete(path, payload):
    try:
        body = json.dumps(payload)
        h    = auth_headers("DELETE", path, "", body)
        req  = urllib.request.Request(
            BASE_URL + path,
            data=body.encode(),
            headers=h,
            method="DELETE"
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [ERR] {}".format(e))
        return None


log("=" * 60)
log("  DELTA EXCHANGE INDIA - ORDER TEST")
log("=" * 60)

# ── STEP 1: Validate keys ───────────────────
if not API_KEY or not API_SECRET:
    log("  [ERR] API_KEY or API_SECRET not set!")
    log("  Set them in Railway Variables tab.")
    exit()
log("  API Key: {}...{}".format(API_KEY[:4], API_KEY[-4:]))

# ── STEP 2: Get live price ──────────────────
log("\n  STEP 1: Fetching live BTCUSD mark price...")
d = http_pub("{}/v2/tickers/BTCUSD".format(BASE_URL))
if not d:
    log("  [ERR] Cannot fetch price!")
    exit()

result = d.get("result", {})
price  = float(result.get("mark_price") or result.get("close") or 0)
if not price:
    log("  [ERR] No price in response: {}".format(result))
    exit()
log("  Live BTCUSD: ${}".format(price))

# ── STEP 3: Get product ID ──────────────────
log("\n  STEP 2: Getting BTCUSD product ID...")
d = http_pub("{}/v2/products/BTCUSD".format(BASE_URL))
pid = None
if d:
    pid = d.get("result", {}).get("id")
if not pid:
    # search all products
    d = http_pub("{}/v2/products".format(BASE_URL))
    if d:
        for p in d.get("result", []):
            if p.get("symbol") == "BTCUSD":
                pid = p["id"]
                break
if not pid:
    log("  [ERR] Cannot find product ID!")
    exit()
log("  Product ID: {}".format(pid))

# ── STEP 4: Set leverage ────────────────────
log("\n  STEP 3: Setting leverage to 50x...")
d = http_post("/v2/products/leverage", {
    "product_id": pid,
    "leverage":   "50"
})
if d and d.get("success"):
    log("  Leverage set to 50x OK")
else:
    log("  [WARN] Leverage response: {}".format(d))

# ── STEP 5: Calculate test order levels ─────
log("\n  STEP 4: Calculating test order levels...")

# Place a BUY limit order BELOW current price
# so it won't fill immediately (safe test)
entry = round(price * 0.995, 1)    # 0.5% below current price
sl    = round(entry - 50, 1)       # $50 below entry
tp    = round(entry + 75, 1)       # $75 above entry (1:1.5)

log("  Current price : ${}".format(price))
log("  Test entry    : ${} (0.5% below market - won't fill)".format(entry))
log("  Test SL       : ${}".format(sl))
log("  Test TP       : ${}".format(tp))

# ── STEP 6: Place bracket order ─────────────
log("\n  STEP 5: Placing test bracket order...")
payload = {
    "product_id":                      pid,
    "size":                            1,
    "side":                            "buy",
    "order_type":                      "limit_order",
    "limit_price":                     str(entry),
    "bracket_stop_loss_price":         str(sl),
    "bracket_stop_loss_limit_price":   str(round(sl - 1, 1)),
    "bracket_take_profit_price":       str(tp),
    "bracket_take_profit_limit_price": str(round(tp - 1, 1)),
    "time_in_force":                   "gtc"
}

log("  Payload: {}".format(json.dumps(payload, indent=2)))

d = http_post("/v2/orders", payload)
log("\n  Full API Response:")
log(json.dumps(d, indent=2))

if not d:
    log("\n  [RESULT] No response from API!")
elif d.get("success"):
    order_id = d.get("result", {}).get("id")
    state    = d.get("result", {}).get("state")
    log("\n  [SUCCESS] Order placed!")
    log("  Order ID : {}".format(order_id))
    log("  State    : {}".format(state))

    # ── STEP 7: Cancel immediately ───────────
    log("\n  STEP 6: Cancelling test order immediately...")
    time.sleep(2)
    c = http_delete("/v2/orders/{}".format(order_id), {
        "product_id": pid
    })
    log("  Cancel response: {}".format(c))
    if c and c.get("success"):
        log("  [SUCCESS] Order cancelled. No real trade placed.")
    else:
        log("  [WARN] Could not cancel. Check Delta app and cancel manually!")
        log("  Order ID to cancel: {}".format(order_id))
else:
    error = d.get("error", {})
    code  = error.get("code", "unknown") if isinstance(error, dict) else error
    msg   = error.get("context", "") if isinstance(error, dict) else ""
    log("\n  [FAILED] Order placement failed!")
    log("  Error code : {}".format(code))
    log("  Message    : {}".format(msg))
    log("  Full error : {}".format(d))

log("\n" + "=" * 60)
log("  TEST COMPLETE")
log("  Share the output above and we will fix any issues.")
log("=" * 60)
