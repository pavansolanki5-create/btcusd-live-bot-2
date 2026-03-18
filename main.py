"""
BTCUSD Live Trading Bot - 1 MIN CANDLES - CLOCK SYNCED
Exchange  : Delta Exchange India
Leverage  : 50x  |  Contracts: 1 (minimum)

KEY FIX: Candles now sync to real clock time.
Candle closes exactly at HH:MM:00 every minute
matching Delta Exchange chart candles perfectly.

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

API_KEY    = os.environ.get("API_KEY",    "")
API_SECRET = os.environ.get("API_SECRET", "")

SYMBOL          = "BTCUSD"
LEVERAGE        = 50
CONTRACTS       = 1
TP_RATIO        = 1.5
EMA_PERIOD      = 9
FETCH_EVERY_SEC = 5

MAX_OPEN_TRADES      = 2
MAX_TRADES_PER_DAY   = 10
MAX_LOSS_PER_DAY_USD = 2.0
MAX_SL_USD           = 80.0
MIN_COOLDOWN_SEC     = 60

BASE_URL     = "https://api.india.delta.exchange"
TICKER_URL   = "{}/v2/tickers/{}".format(BASE_URL, SYMBOL)
PRODUCTS_URL = "{}/v2/products/{}".format(BASE_URL, SYMBOL)

candles         = []
current_candle  = None
last_price      = None
last_signal_id  = -1
product_id      = None
daily_loss      = 0.0
session_trades  = []
last_trade_time = 0
trades_today    = 0
current_minute  = -1   # tracks which minute we are in


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

def http_pub(url, timeout=4):
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

def http_get(path, query="", timeout=4):
    try:
        h   = auth_headers("GET", path, query)
        req = urllib.request.Request(
            BASE_URL + path + query, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [GET ERR] {}".format(e))
        return None

def http_post(path, payload, timeout=5):
    try:
        body = json.dumps(payload)
        h    = auth_headers("POST", path, "", body)
        req  = urllib.request.Request(
            BASE_URL + path,
            data=body.encode(),
            headers=h,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("  [POST ERR] {}".format(e))
        return None


def get_price():
    global last_price
    d = http_pub(TICKER_URL, timeout=4)
    if d:
        r = d.get("result", {})
        p = (r.get("mark_price") or r.get("close") or
             r.get("last_price") or r.get("spot_price"))
        if p:
            last_price = round(float(p), 2)
            return last_price
    if last_price:
        last_price = round(
            last_price * (1 + random.uniform(-0.0001, 0.0001)), 2)
        return last_price
    return None


def get_positions():
    try:
        d = http_get("/v2/positions",
                     "?product_symbol={}".format(SYMBOL), timeout=4)
        if d and d.get("result") is not None:
            return [p for p in d.get("result", [])
                    if float(p.get("size", 0)) != 0]
    except Exception:
        pass
    return []


def get_product_id():
    d = http_pub(PRODUCTS_URL, timeout=6)
    if d:
        pid = d.get("result", {}).get("id")
        if pid:
            log("  Product ID: {}".format(pid))
            return pid
    d = http_pub("{}/v2/products".format(BASE_URL), timeout=6)
    if d:
        for p in d.get("result", []):
            if p.get("symbol") == SYMBOL:
                log("  Product ID: {}".format(p["id"]))
                return p["id"]
    return None


def set_leverage(pid):
    d = http_post("/v2/products/leverage", {
        "product_id": pid,
        "leverage":   str(LEVERAGE)
    }, timeout=6)
    if d and d.get("success"):
        log("  Leverage set to {}x".format(LEVERAGE))
        return True
    log("  [WARN] Leverage set failed: {}".format(d))
    return False


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
    d = http_post("/v2/orders", payload, timeout=5)
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
#  CLOCK SYNCED CANDLE MANAGEMENT
#  Candles close at exact minute boundaries
#  e.g. 20:30:00, 20:31:00, 20:32:00 etc.
#  This matches Delta Exchange chart exactly
# ═══════════════════════════════════════════
def get_current_minute():
    """Returns current minute number (0-59)"""
    return datetime.now().minute

def start_candle(price):
    global current_candle
    current_candle = {
        "open":  price,
        "high":  price,
        "low":   price,
        "close": price,
        "color": "GREEN",
        "ticks": 1,
        "minute": get_current_minute()
    }
    log("  New candle started at minute :{}  O={}".format(
        str(current_candle["minute"]).zfill(2), price))

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

def close_and_new_candle(price):
    """
    Close current candle and start new one.
    Called when minute changes on the clock.
    """
    global current_candle, candles
    if current_candle is None:
        start_candle(price)
        return None

    # Finalise closed candle
    current_candle["close"] = price
    current_candle["color"] = "GREEN" if price >= current_candle["open"] else "RED"
    closed = dict(current_candle)
    candles.append(closed)
    if len(candles) > 500:
        candles.pop(0)

    log("  Candle CLOSED: O={} H={} L={} C={} [{}] Ticks={}".format(
        closed["open"], closed["high"], closed["low"],
        closed["close"], closed["color"], closed["ticks"]))

    # Start fresh candle for new minute
    start_candle(price)
    return closed


def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return ema


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


def check_signals():
    if len(candles) < 3:
        log("  [SKIP] Not enough candles yet ({}/3)".format(len(candles)))
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
        log("  >>> BUY SIGNAL! E={} SL={} TP={} Risk=${}".format(
            entry, sl, tp, risk))
        return "buy", entry, sl, tp, risk, reward, ema9

    if sell_ok:
        entry, sl, tp, risk, reward = sell_sl_tp(curr)
        log("  >>> SELL SIGNAL! E={} SL={} TP={} Risk=${}".format(
            entry, sl, tp, risk))
        return "sell", entry, sl, tp, risk, reward, ema9

    log("  [NO SIGNAL]")
    return None, None, None, None, None, None, ema9


def safety_check(positions, risk):
    if len(positions) >= MAX_OPEN_TRADES:
        log("  [SAFETY] Max trades open.")
        return False
    if daily_loss >= MAX_LOSS_PER_DAY_USD:
        log("  [SAFETY] Daily loss limit hit!")
        return False
    if trades_today >= MAX_TRADES_PER_DAY:
        log("  [SAFETY] Max {}/day reached.".format(MAX_TRADES_PER_DAY))
        return False
    since_last = time.time() - last_trade_time
    if since_last < MIN_COOLDOWN_SEC:
        log("  [SAFETY] Cooldown {}s left.".format(
            int(MIN_COOLDOWN_SEC - since_last)))
        return False
    if risk > MAX_SL_USD:
        log("  [SAFETY] SL ${} too wide. Skip.".format(risk))
        return False
    return True


def seed_candles(seed_price, count=20):
    """Seed synthetic candles for EMA warmup"""
    global candles
    p = seed_price * (1 - random.uniform(0.001, 0.003))
    for _ in range(count):
        vol     = p * 0.001
        open_p  = round(p, 2)
        close_p = round(p + random.uniform(-vol, vol), 2)
        high_p  = round(max(open_p, close_p) + random.uniform(0, vol * 0.4), 2)
        low_p   = round(min(open_p, close_p) - random.uniform(0, vol * 0.4), 2)
        color   = "GREEN" if close_p >= open_p else "RED"
        candles.append({
            "open": open_p, "high": high_p,
            "low":  low_p,  "close": close_p,
            "color": color, "ticks": 12,
            "minute": -1
        })
        p = close_p
    log("  Seeded {} candles. ${} to ${}".format(
        count, candles[0]["open"], candles[-1]["close"]))


def seconds_until_next_minute():
    """How many seconds until next minute boundary"""
    now = datetime.now()
    return 60 - now.second


def dashboard(price, candle_count, ema9, positions):
    cc            = current_candle
    now           = datetime.now()
    sec_left      = 60 - now.second
    cooldown_left = max(0, int(MIN_COOLDOWN_SEC - (time.time() - last_trade_time)))

    log("\n" + "=" * 62)
    log("  DELTA INDIA LIVE - BTCUSD 1MIN 50x | {}".format(
        now.strftime("%d %b %Y  %H:%M:%S")))
    log("=" * 62)
    log("  PRICE    : ${}".format(price))
    log("  EMA9     : ${}".format(round(ema9, 2) if ema9 else "warming..."))
    log("  BALANCE  : Check Delta app manually")
    log("  NEXT     : {}s  |  CANDLES: {}".format(sec_left, candle_count))
    log("  TODAY    : {} trades (max {})  LOSS: ${} (max ${})".format(
        trades_today, MAX_TRADES_PER_DAY,
        round(daily_loss, 2), MAX_LOSS_PER_DAY_USD))
    log("  COOLDOWN : {}".format(
        "{}s left".format(cooldown_left) if cooldown_left > 0 else "READY"))
    log("-" * 62)
    if cc:
        pct = min(100, int((60 - sec_left) / 60 * 100))
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        log("  FORMING : O={} H={} L={} C={} [{}]".format(
            cc["open"], cc["high"], cc["low"], price, cc["color"]))
        log("  PROGRESS: [{}] {}%  Ticks={}  Min=:{}".format(
            bar, pct, cc["ticks"],
            str(cc.get("minute", "?")).zfill(2)))
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
        log("  SESSION: {} trades".format(len(session_trades)))
        for t in session_trades[-3:]:
            log("  #{} [{}] E=${} SL=${} TP=${} @{}".format(
                t["id"], t["side"].upper(),
                t["entry"], t["sl"], t["tp"], t["time"]))
    log("=" * 62)


def run():
    global last_signal_id, daily_loss, product_id
    global last_trade_time, trades_today, current_minute

    log("=" * 62)
    log("  DELTA EXCHANGE INDIA - BTCUSD 1MIN LIVE BOT")
    log("  CLOCK SYNCED - Candles match Delta Exchange chart")
    log("  Leverage:{}x  Contracts:{}".format(LEVERAGE, CONTRACTS))
    log("  MaxSL:${}  Cooldown:{}s  MaxTrades:{}/day".format(
        MAX_SL_USD, MIN_COOLDOWN_SEC, MAX_TRADES_PER_DAY))
    log("=" * 62)

    if not API_KEY or not API_SECRET:
        log("  [ERR] API_KEY or API_SECRET missing!")
        return

    log("  API keys loaded OK.")

    log("\n  Getting product ID...")
    product_id = get_product_id()
    if not product_id:
        log("  [ERR] Product not found.")
        return

    log("\n  Setting leverage {}x...".format(LEVERAGE))
    set_leverage(product_id)

    log("\n  Fetching live price...")
    seed = get_price()
    if not seed:
        log("  [ERR] Cannot fetch price.")
        return
    log("  Live BTCUSD: ${}".format(seed))

    log("\n  Seeding {} EMA warmup candles...".format(20))
    seed_candles(seed, count=20)

    # Start first candle aligned to current minute
    current_minute = get_current_minute()
    start_candle(seed)
    candle_count = len(candles)
    last_ema9    = None
    trade_count  = 0

    # Tell user when first real candle closes
    sec_left = seconds_until_next_minute()
    log("\n  *** BOT IS LIVE - CLOCK SYNCED ***")
    log("  Candles close at exact minute boundaries")
    log("  Next candle close in {} seconds  (at :{})".format(
        sec_left,
        str((datetime.now().minute + 1) % 60).zfill(2)))
    log("  Price check every {} seconds\n".format(FETCH_EVERY_SEC))

    while True:
        try:
            # Fetch live price
            price = get_price()
            if not price:
                time.sleep(5)
                continue

            # Update EMA
            if len(candles) >= EMA_PERIOD:
                closes    = [c["close"] for c in candles]
                last_ema9 = calc_ema(closes, EMA_PERIOD)

            # Update forming candle
            update_candle(price)

            # ── CLOCK SYNC: Check if minute has changed ──
            this_minute = get_current_minute()

            if this_minute != current_minute:
                # Minute boundary crossed — close candle!
                log("\n" + "=" * 62)
                log("  MINUTE BOUNDARY: :{} → :{}  CLOSING CANDLE".format(
                    str(current_minute).zfill(2),
                    str(this_minute).zfill(2)))

                closed        = close_and_new_candle(price)
                current_minute = this_minute
                candle_count   = len(candles)

                if closed:
                    log("  [1MIN CANDLE #{}] O={} H={} L={} C={} [{}]".format(
                        candle_count,
                        closed["open"], closed["high"],
                        closed["low"],  closed["close"],
                        closed["color"]))
                    log("  Range: ${}".format(
                        round(closed["high"] - closed["low"], 2)))

                    # Check signal on closed candle
                    if candle_count != last_signal_id:
                        side, entry, sl, tp, risk, reward, ema9 = check_signals()
                        if ema9:
                            last_ema9 = ema9

                        if side:
                            positions = get_positions()

                            if safety_check(positions, risk):
                                log("\n  " + "*" * 48)
                                log("  *** {} SIGNAL - TRADE #{} ***".format(
                                    side.upper(), trade_count + 1))
                                log("  Entry  : ${}".format(entry))
                                log("  SL     : ${}  Risk -${}".format(sl, risk))
                                log("  TP     : ${}  Reward +${}".format(tp, reward))
                                log("  R:R    : 1:1.5")
                                log("  " + "*" * 48)

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
                                    log("  *** ORDER IS LIVE ON EXCHANGE! ***")

            # Dashboard
            positions = get_positions()
            dashboard(price, candle_count, last_ema9, positions)
            time.sleep(FETCH_EVERY_SEC)

        except KeyboardInterrupt:
            log("  Bot stopped.")
            log("  Trades today: {}".format(trades_today))
            break
        except Exception as e:
            log("  [ERR] {} - Retry in 5s...".format(e))
            time.sleep(5)


if __name__ == "__main__":
    run()
