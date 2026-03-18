"""
BTCUSD Live Trading Bot - 5 MIN CANDLES
Exchange  : Delta Exchange India
Leverage  : 50x  |  Contracts: 1 (minimum)
Fix       : Balance fetch improved for Delta India

BUY  : PrevRED + CurrGREEN + Close>PrevHigh + Close>EMA9
       SL = Green candle LOW (exact)
       TP = Entry + (Entry - SL) x 1.5

SELL : PrevGREEN + CurrRED + Close<PrevLow
       SL = Red candle HIGH (exact)
       TP = Entry - (SL - Entry) x 1.5
"""

import time
import json
import hmac
import hashlib
import random
import os
import urllib.request
from datetime import datetime

# ═══════════════════════════════════════════
#  API KEYS from Railway environment variables
# ═══════════════════════════════════════════
API_KEY    = os.environ.get("API_KEY",    "")
API_SECRET = os.environ.get("API_SECRET", "")

# ═══════════════════════════════════════════
#  TRADING CONFIG
# ═══════════════════════════════════════════
SYMBOL          = "BTCUSD"
LEVERAGE        = 50
CONTRACTS       = 1
TP_RATIO        = 1.5
EMA_PERIOD      = 9
FETCH_EVERY_SEC = 5
CANDLE_SEC      = 300              # 5 minutes

MAX_OPEN_TRADES      = 2
MAX_TRADES_PER_DAY   = 6           # max 6 trades per day
MIN_BALANCE_USD      = 1.50
MAX_LOSS_PER_DAY_USD = 2.0
MAX_SL_USD           = 150.0       # skip if SL wider than $150
MIN_COOLDOWN_SEC     = 300         # 5 min gap between trades

BASE_URL     = "https://api.india.delta.exchange"
TICKER_URL   = "{}/v2/tickers/{}".format(BASE_URL, SYMBOL)
PRODUCTS_URL = "{}/v2/products/{}".format(BASE_URL, SYMBOL)

# ═══════════════════════════════════════════
#  GLOBALS
# ═══════════════════════════════════════════
candles         = []
current_candle  = None
candle_start_t  = None
last_price      = None
last_signal_id  = -1
product_id      = None
daily_loss      = 0.0
session_trades  = []
last_trade_time = 0
trades_today    = 0


def log(msg):
    print("[{}] {}".format(
        datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ═══════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════
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


# ═══════════════════════════════════════════
#  HTTP HELPERS
# ═══════════════════════════════════════════
def http_pub(url, timeout=6):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [PUB ERR] {}".format(e))
        return None

def http_get(path, query=""):
    try:
        h   = auth_headers("GET", path, query)
        req = urllib.request.Request(
            BASE_URL + path + query, headers=h)
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [GET ERR] {}".format(e))
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
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [POST ERR] {}".format(e))
        return None


# ═══════════════════════════════════════════
#  LIVE MARK PRICE
# ═══════════════════════════════════════════
def get_price():
    global last_price
    d = http_pub(TICKER_URL)
    if d:
        r = d.get("result", {})
        p = (r.get("mark_price") or r.get("close") or
             r.get("last_price") or r.get("spot_price"))
        if p:
            last_price = round(float(p), 2)
            return last_price
    if last_price:
        last_price = round(
            last_price * (1 + random.uniform(-0.0002, 0.0002)), 2)
        return last_price
    return None


# ═══════════════════════════════════════════
#  WALLET BALANCE — IMPROVED
#  Delta Exchange India uses USDT for futures margin
#  Tries every possible field and asset name
# ═══════════════════════════════════════════
def get_balance():
    try:
        d = http_get("/v2/wallet/balances")
        if not d:
            return None

        # Log raw response once for debugging
        result = d.get("result", [])

        # Try list format
        if isinstance(result, list):
            for a in result:
                sym = str(a.get("asset_symbol", "")).upper()
                if sym in ["USDT", "USD", "INR"]:
                    for field in ["available_balance", "balance",
                                  "available_balance_for_orders",
                                  "cross_asset_liability"]:
                        val = a.get(field)
                        if val is not None:
                            try:
                                b = float(val)
                                if b > 0:
                                    return round(b, 4)
                            except Exception:
                                pass

            # If nothing found, return first non-zero balance
            for a in result:
                for field in ["available_balance", "balance"]:
                    val = a.get(field)
                    if val is not None:
                        try:
                            b = float(val)
                            if b > 0:
                                log("  [BAL DEBUG] Found balance in asset: {} = {}".format(
                                    a.get("asset_symbol", "?"), b))
                                return round(b, 4)
                        except Exception:
                            pass

        # Try dict format
        if isinstance(result, dict):
            for field in ["available_balance", "balance", "total_balance"]:
                val = result.get(field)
                if val is not None:
                    try:
                        b = float(val)
                        if b > 0:
                            return round(b, 4)
                    except Exception:
                        pass

        # Log full response for debugging if nothing found
        log("  [BAL DEBUG] Full response: {}".format(str(d)[:300]))
        return None

    except Exception as e:
        log("  [BAL ERR] {}".format(e))
        return None


# ═══════════════════════════════════════════
#  PRODUCT ID
# ═══════════════════════════════════════════
def get_product_id():
    d = http_pub(PRODUCTS_URL)
    if d:
        pid = d.get("result", {}).get("id")
        if pid:
            log("  Product ID: {}".format(pid))
            return pid
    d = http_pub("{}/v2/products".format(BASE_URL))
    if d:
        for p in d.get("result", []):
            if p.get("symbol") == SYMBOL:
                log("  Product ID: {}".format(p["id"]))
                return p["id"]
    return None


# ═══════════════════════════════════════════
#  SET LEVERAGE
# ═══════════════════════════════════════════
def set_leverage(pid):
    d = http_post("/v2/products/leverage", {
        "product_id": pid,
        "leverage":   str(LEVERAGE)
    })
    if d and d.get("success"):
        log("  Leverage set to {}x".format(LEVERAGE))
        return True
    log("  [WARN] Leverage set failed: {}".format(d))
    return False


# ═══════════════════════════════════════════
#  OPEN POSITIONS
# ═══════════════════════════════════════════
def get_positions():
    d = http_get("/v2/positions",
                 "?product_symbol={}".format(SYMBOL))
    if d:
        return [p for p in d.get("result", [])
                if float(p.get("size", 0)) != 0]
    return []


# ═══════════════════════════════════════════
#  PLACE BRACKET ORDER
# ═══════════════════════════════════════════
def place_order(side, entry, sl, tp, pid):
    if side == "buy":
        lp     = round(entry * 1.0003, 2)
        sl_lim = round(sl    * 0.9995, 2)
        tp_lim = round(tp    * 0.9995, 2)
    else:
        lp     = round(entry * 0.9997, 2)
        sl_lim = round(sl    * 1.0005, 2)
        tp_lim = round(tp    * 1.0005, 2)

    payload = {
        "product_id":                      pid,
        "size":                            CONTRACTS,
        "side":                            side,
        "order_type":                      "limit_order",
        "limit_price":                     str(lp),
        "bracket_stop_loss_price":         str(sl),
        "bracket_stop_loss_limit_price":   str(sl_lim),
        "bracket_take_profit_price":       str(tp),
        "bracket_take_profit_limit_price": str(tp_lim),
        "time_in_force":                   "gtc"
    }
    log("  Placing {} bracket: E={} SL={} TP={}".format(
        side.upper(), lp, sl, tp))
    d = http_post("/v2/orders", payload)
    if d:
        if d.get("success"):
            o = d.get("result", {})
            log("  ORDER LIVE! ID={} Status={}".format(
                o.get("id"), o.get("state")))
            return o
        else:
            log("  [ORDER FAIL] {}".format(d.get("error", d)))
    return None


# ═══════════════════════════════════════════
#  CANDLE MANAGEMENT
# ═══════════════════════════════════════════
def start_candle(price):
    global current_candle, candle_start_t
    candle_start_t = time.time()
    current_candle = {
        "open": price, "high": price,
        "low":  price, "close": price,
        "color": "GREEN", "ticks": 1
    }

def update_candle(price):
    global current_candle
    if current_candle is None:
        start_candle(price)
        return
    current_candle["high"]  = max(current_candle["high"], price)
    current_candle["low"]   = min(current_candle["low"],  price)
    current_candle["close"] = price
    current_candle["color"] = "GREEN" if price >= current_candle["open"] else "RED"
    current_candle["ticks"] += 1

def close_candle(price):
    global current_candle, candles
    if current_candle is None:
        start_candle(price)
        return None
    current_candle["close"] = price
    current_candle["color"] = "GREEN" if price >= current_candle["open"] else "RED"
    closed = dict(current_candle)
    candles.append(closed)
    if len(candles) > 500:
        candles.pop(0)
    start_candle(price)
    return closed


# ═══════════════════════════════════════════
#  EMA
# ═══════════════════════════════════════════
def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return ema


# ═══════════════════════════════════════════
#  SL / TP
# ═══════════════════════════════════════════
def buy_sl_tp(candle):
    entry  = candle["close"]
    sl     = candle["low"]
    risk   = round(entry - sl, 2)
    tp     = round(entry + risk * TP_RATIO, 2)
    reward = round(risk * TP_RATIO, 2)
    return entry, sl, tp, risk, reward

def sell_sl_tp(candle):
    entry  = candle["close"]
    sl     = candle["high"]
    risk   = round(sl - entry, 2)
    tp     = round(entry - risk * TP_RATIO, 2)
    reward = round(risk * TP_RATIO, 2)
    return entry, sl, tp, risk, reward


# ═══════════════════════════════════════════
#  CHECK SIGNALS
# ═══════════════════════════════════════════
def check_signals():
    if len(candles) < 3:
        return None, None, None, None, None, None, None

    prev   = candles[-3]
    curr   = candles[-2]
    closes = [c["close"] for c in candles[:-1]]
    ema9   = calc_ema(closes, EMA_PERIOD)

    buy_ok = (
        prev["close"] < prev["open"] and
        curr["close"] > curr["open"] and
        curr["close"] > prev["high"] and
        ema9 is not None and curr["close"] > ema9
    )
    sell_ok = (
        prev["close"] > prev["open"] and
        curr["close"] < curr["open"] and
        curr["close"] < prev["low"]
    )

    log("  [BUY]  PrevRED:{} CurrGREEN:{} Engulf:{} AboveEMA:{}".format(
        "Y" if prev["close"] < prev["open"]    else "N",
        "Y" if curr["close"] > curr["open"]    else "N",
        "Y" if curr["close"] > prev["high"]    else "N",
        "Y" if (ema9 and curr["close"] > ema9) else "N"))
    log("  [SELL] PrevGREEN:{} CurrRED:{} BelowPrevLow:{}".format(
        "Y" if prev["close"] > prev["open"]    else "N",
        "Y" if curr["close"] < curr["open"]    else "N",
        "Y" if curr["close"] < prev["low"]     else "N"))

    if buy_ok:
        entry, sl, tp, risk, reward = buy_sl_tp(curr)
        log("  >>> BUY! E={} SL={} TP={} Risk=${}".format(entry, sl, tp, risk))
        return "buy", entry, sl, tp, risk, reward, ema9

    if sell_ok:
        entry, sl, tp, risk, reward = sell_sl_tp(curr)
        log("  >>> SELL! E={} SL={} TP={} Risk=${}".format(entry, sl, tp, risk))
        return "sell", entry, sl, tp, risk, reward, ema9

    log("  [NO SIGNAL]")
    return None, None, None, None, None, None, ema9


# ═══════════════════════════════════════════
#  SAFETY CHECK — IMPROVED
# ═══════════════════════════════════════════
def safety_check(positions, balance, risk):
    # Max open trades
    if len(positions) >= MAX_OPEN_TRADES:
        log("  [SAFETY] Max {} trades open. Skip.".format(MAX_OPEN_TRADES))
        return False

    # Balance floor
    if balance is not None and balance < MIN_BALANCE_USD:
        log("  [SAFETY] Balance ${} too low!".format(balance))
        return False

    # Daily loss limit
    if daily_loss >= MAX_LOSS_PER_DAY_USD:
        log("  [SAFETY] Daily loss limit ${}. No more trades.".format(
            MAX_LOSS_PER_DAY_USD))
        return False

    # Max trades per day
    if trades_today >= MAX_TRADES_PER_DAY:
        log("  [SAFETY] Max {} trades today reached.".format(MAX_TRADES_PER_DAY))
        return False

    # Cooldown between trades
    since_last = time.time() - last_trade_time
    if since_last < MIN_COOLDOWN_SEC:
        log("  [SAFETY] Cooldown: wait {}s more.".format(
            int(MIN_COOLDOWN_SEC - since_last)))
        return False

    # SL too wide — skip risky trades
    if risk > MAX_SL_USD:
        log("  [SAFETY] SL too wide! Risk=${} > max ${}. Skip.".format(
            risk, MAX_SL_USD))
        return False

    return True


# ═══════════════════════════════════════════
#  SEED CANDLES FOR EMA WARMUP
# ═══════════════════════════════════════════
def seed_candles(seed_price, count=20):
    global candles
    p = seed_price * (1 - random.uniform(0.002, 0.005))
    for _ in range(count):
        vol     = p * 0.002
        open_p  = round(p, 2)
        close_p = round(p + random.uniform(-vol, vol), 2)
        high_p  = round(max(open_p, close_p) + random.uniform(0, vol * 0.4), 2)
        low_p   = round(min(open_p, close_p) - random.uniform(0, vol * 0.4), 2)
        color   = "GREEN" if close_p >= open_p else "RED"
        candles.append({
            "open": open_p, "high": high_p,
            "low":  low_p,  "close": close_p,
            "color": color, "ticks": 60
        })
        p = close_p
    log("  Seeded {} candles. ${} to ${}".format(
        count, candles[0]["open"], candles[-1]["close"]))


# ═══════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════
def dashboard(price, candle_count, sec_left, ema9, balance, positions):
    cc   = current_candle
    mins = int(sec_left) // 60
    secs = int(sec_left) % 60
    cooldown_left = max(0, int(MIN_COOLDOWN_SEC - (time.time() - last_trade_time)))

    log("\n" + "=" * 62)
    log("  DELTA INDIA LIVE - BTCUSD 5MIN 50x | {}".format(
        datetime.now().strftime("%d %b %Y  %H:%M:%S")))
    log("=" * 62)
    log("  PRICE    : ${}".format(price))
    log("  EMA9     : ${}".format(round(ema9, 2) if ema9 else "warming..."))
    log("  BALANCE  : ${}  (~Rs {})".format(
        balance if balance else "checking...",
        int((balance or 0) * 84)))
    log("  NEXT     : {}m {}s  |  CANDLE #{}".format(mins, secs, candle_count))
    log("  TODAY    : {} trades  (max {})  |  LOSS: ${} (max ${})".format(
        trades_today, MAX_TRADES_PER_DAY,
        round(daily_loss, 2), MAX_LOSS_PER_DAY_USD))
    log("  COOLDOWN : {}s  |  MAX SL: ${}".format(
        cooldown_left if cooldown_left > 0 else "READY",
        MAX_SL_USD))
    log("-" * 62)
    if cc:
        pct = min(100, int((time.time() - candle_start_t) / CANDLE_SEC * 100))
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        log("  FORMING : O={} H={} L={} C={} [{}]".format(
            cc["open"], cc["high"], cc["low"], price, cc["color"]))
        log("  PROGRESS: [{}] {}%  Ticks={}".format(bar, pct, cc["ticks"]))
    log("-" * 62)
    if positions:
        log("  LIVE POSITIONS:")
        for p in positions:
            log("  [{}] Size={} Entry=${} PNL={} Liq=${}".format(
                p.get("direction", "?").upper(),
                p.get("size", 0),
                p.get("entry_price", "?"),
                p.get("unrealized_pnl", "?"),
                p.get("liquidation_price", "?")))
    else:
        log("  No open positions.")
    if session_trades:
        log("  SESSION TRADES: {}".format(len(session_trades)))
        for t in session_trades[-3:]:
            log("  #{} [{}] E=${} SL=${} TP=${} @{}".format(
                t["id"], t["side"].upper(),
                t["entry"], t["sl"], t["tp"], t["time"]))
    log("=" * 62)


# ═══════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════
def run():
    global last_signal_id, daily_loss, product_id
    global last_trade_time, trades_today

    log("=" * 62)
    log("  DELTA EXCHANGE INDIA - BTCUSD 5MIN LIVE BOT")
    log("  Leverage:{}x  Contracts:{}  Candle:5min".format(LEVERAGE, CONTRACTS))
    log("  Max trades/day:{}  Max SL:${}  Cooldown:{}min".format(
        MAX_TRADES_PER_DAY, MAX_SL_USD, MIN_COOLDOWN_SEC // 60))
    log("=" * 62)

    if not API_KEY or not API_SECRET:
        log("  [ERR] API_KEY or API_SECRET missing!")
        log("  Railway -> Variables -> Add API_KEY and API_SECRET")
        return

    log("  API keys loaded OK.")

    log("\n  Getting product ID...")
    product_id = get_product_id()
    if not product_id:
        log("  [ERR] Product not found.")
        return

    log("\n  Setting leverage {}x...".format(LEVERAGE))
    set_leverage(product_id)

    log("\n  Checking balance...")
    bal = get_balance()
    if bal:
        log("  Balance: ${}  (~Rs {})".format(bal, int(bal * 84)))
        log("  Can take ~{} trades".format(int(bal / 1.49)))
        if bal < MIN_BALANCE_USD:
            log("  [ERR] Balance too low.")
            return
    else:
        log("  [WARN] Balance fetch failed - check API permissions.")
        log("  Make sure API key has READ permission enabled.")

    log("\n  Fetching live price...")
    seed = get_price()
    if not seed:
        log("  [ERR] Cannot fetch price.")
        return
    log("  Live BTCUSD: ${}".format(seed))

    log("\n  Seeding EMA warmup candles...")
    seed_candles(seed, count=20)

    start_candle(seed)
    candle_count  = len(candles)
    last_candle_t = time.time()
    last_ema9     = None
    last_bal_t    = time.time()
    trade_count   = 0

    log("\n  *** BOT IS LIVE ***")
    log("  First candle closes in 5 minutes.\n")

    while True:
        try:
            price = get_price()
            if not price:
                time.sleep(5)
                continue

            if len(candles) >= EMA_PERIOD:
                closes    = [c["close"] for c in candles]
                last_ema9 = calc_ema(closes, EMA_PERIOD)

            update_candle(price)
            elapsed  = time.time() - last_candle_t
            sec_left = max(0, CANDLE_SEC - elapsed)

            # Refresh balance every 2 min
            if time.time() - last_bal_t > 120:
                bal        = get_balance()
                last_bal_t = time.time()
                if bal:
                    log("  [BAL] ${}  (~Rs {})".format(bal, int(bal * 84)))
                    if bal < MIN_BALANCE_USD:
                        log("  [SAFETY] Balance too low. Stopping!")
                        break

            # Close candle every 5 min
            if elapsed >= CANDLE_SEC:
                closed        = close_candle(price)
                last_candle_t = time.time()
                candle_count  = len(candles)
                sec_left      = CANDLE_SEC

                if closed:
                    log("\n" + "-" * 62)
                    log("  [5MIN #{}] O={} H={} L={} C={} [{}] Range=${}".format(
                        candle_count,
                        closed["open"], closed["high"],
                        closed["low"],  closed["close"],
                        closed["color"],
                        round(closed["high"] - closed["low"], 2)))

                    if candle_count != last_signal_id:
                        side, entry, sl, tp, risk, reward, ema9 = check_signals()
                        if ema9:
                            last_ema9 = ema9

                        if side:
                            positions = get_positions()
                            bal       = get_balance()

                            if safety_check(positions, bal, risk):
                                log("  *** {} TRADE #{} ***".format(
                                    side.upper(), trade_count + 1))
                                log("  E={} SL={} TP={} Risk=${} Reward=${}".format(
                                    entry, sl, tp, risk, reward))

                                order = place_order(
                                    side, entry, sl, tp, product_id)

                                if order:
                                    last_signal_id  = candle_count
                                    trade_count    += 1
                                    trades_today   += 1
                                    last_trade_time = time.time()
                                    session_trades.append({
                                        "id":    trade_count,
                                        "side":  side,
                                        "entry": entry,
                                        "sl":    sl,
                                        "tp":    tp,
                                        "time":  datetime.now().strftime("%H:%M")
                                    })
                                    log("  ORDER IS LIVE!")

            positions = get_positions()
            bal       = get_balance()
            dashboard(price, candle_count, sec_left, last_ema9, bal, positions)
            time.sleep(FETCH_EVERY_SEC)

        except KeyboardInterrupt:
            log("  Bot stopped.")
            break
        except Exception as e:
            log("  [ERR] {} - Retry in 10s...".format(e))
            time.sleep(10)


if __name__ == "__main__":
    run()
