"""
BALANCE DEBUG SCRIPT
Run this first to find which endpoint returns your balance
on Delta Exchange India
"""

import time
import json
import hmac
import hashlib
import os
import urllib.request

API_KEY    = os.environ.get("API_KEY",    "")
API_SECRET = os.environ.get("API_SECRET", "")
BASE_URL   = "https://api.india.delta.exchange"


def log(msg):
    print(msg, flush=True)


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

def http_get(path, query=""):
    try:
        h   = auth_headers("GET", path, query)
        url = BASE_URL + path + query
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  ERR: {}".format(e))
        return None


log("=" * 60)
log("  DELTA EXCHANGE INDIA - BALANCE DEBUG")
log("=" * 60)

if not API_KEY or not API_SECRET:
    log("  ERROR: API_KEY or API_SECRET not set!")
    log("  Set them in Railway Variables tab.")
else:
    log("  API_KEY loaded: {}...{}".format(API_KEY[:4], API_KEY[-4:]))

log("")

# Test 1: wallet/balances
log("--- TEST 1: /v2/wallet/balances ---")
d = http_get("/v2/wallet/balances")
if d:
    log("  Full response:")
    log(json.dumps(d, indent=2))
else:
    log("  FAILED")

log("")

# Test 2: profile
log("--- TEST 2: /v2/profile ---")
d = http_get("/v2/profile")
if d:
    log("  Full response:")
    log(json.dumps(d, indent=2))
else:
    log("  FAILED")

log("")

# Test 3: assets
log("--- TEST 3: /v2/assets ---")
d = http_get("/v2/assets")
if d:
    log("  Full response (first 500 chars):")
    log(str(d)[:500])
else:
    log("  FAILED")

log("")

# Test 4: margins
log("--- TEST 4: /v2/positions/margined ---")
d = http_get("/v2/positions/margined")
if d:
    log("  Full response:")
    log(json.dumps(d, indent=2)[:500])
else:
    log("  FAILED")

log("")
log("  Copy the output above and share it.")
log("  This will show exactly which field has your balance.")
log("=" * 60)
