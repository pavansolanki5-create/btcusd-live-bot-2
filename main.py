"""
ORDER PLACEMENT TEST SCRIPT
Delta Exchange India - BTCUSD
Place this as main.py on GitHub
Railway will run it and show results in logs
"""

import time
import json
import hmac
import hashlib
import os
import urllib.request
from datetime import datetime

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
        log("ERR: {}".format(e))
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
        log("ERR: {}".format(e))
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
        log("ERR: {}".format(e))
        return None


log("=" * 60)
log("  DELTA EXCHANGE INDIA - ORDER TEST")
log("  " + datetime.now().strftime("%d %b %Y  %H:%M:%S"))
log("=" * 60)

# STEP 1: Check API Keys
log("\n--- STEP 1: Check API Keys ---")
if not API_KEY or not API_SECRET:
    log("FAIL: API_KEY or API_SECRET not set!")
    log("Railway -> Variables -> Add API_KEY and API_SECRET")
    for i in range(18):
        time.sleep(5)
        log("Closing in {}s...".format(90 - (i+1)*5))
    exit()
log("Key: {}...{}".format(API_KEY[:4], API_KEY[-4:]))
log("PASS")

# STEP 2: Fetch Price
log("\n--- STEP 2: Fetch Live Price ---")
d = http_pub("{}/v2/tickers/BTCUSD".format(BASE_URL))
if not d:
    log("FAIL: No response from ticker API")
    for i in range(18):
        time.sleep(5)
        log("Closing in {}s...".format(90 - (i+1)*5))
    exit()
r     = d.get("result", {})
price = float(r.get("mark_price") or r.get("close") or r.get("last_price") or 0)
if not price:
    log("FAIL: No price. Response: {}".format(d))
    for i in range(18):
        time.sleep(5)
        log("Closing in {}s...".format(90 - (i+1)*5))
    exit()
log("Live BTCUSD: ${}".format(price))
log("PASS")

# STEP 3: Get Product ID
log("\n--- STEP 3: Get Product ID ---")
pid = None
d   = http_pub("{}/v2/products/BTCUSD".format(BASE_URL))
if d:
    pid = d.get("result", {}).get("id")
if not pid:
    d = http_pub("{}/v2/products".format(BASE_URL))
    if d:
        for p in d.get("result", []):
            if p.get("symbol") == "BTCUSD":
                pid = p["id"]
                break
if not pid:
    log("FAIL: Product ID not found!")
    for i in range(18):
        time.sleep(5)
        log("Closing in {}s...".format(90 - (i+1)*5))
    exit()
log("Product ID: {}".format(pid))
log("PASS")

# STEP 4: Set Leverage
log("\n--- STEP 4: Set Leverage 50x ---")
d = http_post("/v2/products/leverage", {
    "product_id": pid,
    "leverage":   "50"
})
if d and d.get("success"):
    log("Leverage 50x set. PASS")
else:
    log("WARN: {}".format(d))

# STEP 5: Calculate Safe Test Order
log("\n--- STEP 5: Calculate Levels ---")
entry  = round(price * 0.995, 1)
sl     = round(entry - 50,    1)
tp     = round(entry + 75,    1)
sl_lim = round(sl - 1,        1)
tp_lim = round(tp - 1,        1)
log("Market : ${}".format(price))
log("Entry  : ${} (0.5% below market, will NOT fill)".format(entry))
log("SL     : ${}".format(sl))
log("TP     : ${}".format(tp))

# STEP 6: Place Order
log("\n--- STEP 6: Place Bracket Order ---")
payload = {
    "product_id":                      pid,
    "size":                            1,
    "side":                            "buy",
    "order_type":                      "limit_order",
    "limit_price":                     str(entry),
    "bracket_stop_loss_price":         str(sl),
    "bracket_stop_loss_limit_price":   str(sl_lim),
    "bracket_take_profit_price":       str(tp),
    "bracket_take_profit_limit_price": str(tp_lim),
    "time_in_force":                   "gtc"
}
log("Placing order...")
d = http_post("/v2/orders", payload)
log("Full response:")
log(json.dumps(d, indent=2) if d else "None")

order_id = None
if d and d.get("success"):
    order_id = d.get("result", {}).get("id")
    state    = d.get("result", {}).get("state")
    log("\nSUCCESS - Order placed!")
    log("Order ID : {}".format(order_id))
    log("State    : {}".format(state))
    log("PASS - API works for trading!")
else:
    err = d.get("error", {}) if d else {}
    log("\nFAIL - Order rejected!")
    if isinstance(err, dict):
        log("Code   : {}".format(err.get("code",    "?")))
        log("Detail : {}".format(err.get("context", "?")))
    else:
        log("Error  : {}".format(err))

# STEP 7: Cancel Order
if order_id:
    log("\n--- STEP 7: Cancel Test Order ---")
    time.sleep(2)
    c = http_delete("/v2/orders/{}".format(order_id), {
        "product_id": pid
    })
    if c and c.get("success"):
        log("Order cancelled. No real trade placed. PASS")
    else:
        log("WARN: Auto-cancel failed!")
        log("Cancel manually on Delta app! Order ID: {}".format(order_id))

# SUMMARY
log("\n" + "=" * 60)
log("  RESULT SUMMARY")
log("=" * 60)
log("  1. API Keys   : PASS")
log("  2. Price feed : ${} PASS".format(price))
log("  3. Product ID : {} PASS".format(pid))
log("  4. Leverage   : 50x")
log("  5. Order      : {}".format("PASS" if order_id else "FAIL"))
log("=" * 60)
log("  Staying alive 90s - screenshot these logs now!")
log("=" * 60)

for i in range(18):
    time.sleep(5)
    remaining = 90 - (i + 1) * 5
    log("  Closing in {}s...".format(remaining))
