"""
BTCUSD Live Trading Bot - 1 MIN CANDLES
Exchange  : Delta Exchange India
Leverage  : 50x  |  Contracts: 1 (minimum)

FIXES IN THIS VERSION:
  1. Reduced seed candles to 9 (just enough for EMA9)
     So real candles take over EMA calculation faster
  2. Full condition debug on every candle close
  3. Safety check reasons printed clearly
  4. Correct candle index (curr=last closed, prev=before that)

BUY  : PREV RED + CURR GREEN + CURR close > PREV high + CURR close > EMA9
SELL : PREV GREEN + CURR RED + CURR close < PREV low
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
open_trades     = []
last_trade_time = 0
trades_today    = 0
current_minute  = -1


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
    log("  [WARN] Leverage: {}".format(d))
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


def update_open_trades(price):
    global open_trades, daily_loss
    still_open = []
    for t in open_trades:
        if t["side"] == "buy":
            if price >= t["tp"]:
                pnl = round(t["tp"] - t["entry"], 2)
                log("  [TP HIT] BUY #{} +${}".format(t["id"], pnl))
            elif price <= t["sl"]:
                pnl = round(t["entry"] - t["sl"], 2)
                daily_loss = round(daily_loss + pnl, 2)
                log("  [SL HIT] BUY #{} -${}".format(t["id"], pnl))
            else:
                still_open.append(t)
        else:
            if price <= t["tp"]:
                pnl = round(t["entry"] - t["tp"], 2)
                log("  [TP HIT] SELL #{} +${}".format(t["id"], pnl))
            elif price >= t["sl"]:
                pnl = round(t["entry"] - t["sl"], 2)
                daily_loss = round(daily_loss + pnl, 2)
                log("  [SL HIT] SELL #{} -${}".format(t["id"], pnl))
            else:
                still_open.append(t)
    open_trades = still_open


def get_current_minute():
    return datetime.now().minute

def start_candle(price):
    global current_candle
    current_candle = {
        "open":   price, "high": price,
        "low":    price, "close": price,
        "color":  "GREEN", "ticks": 1,
        "minute": get_current_minute()
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

def close_and_new_candle(price):
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
    """
    candles list (only closed candles):
      candles[-1] = CURR = most recently closed candle
      candles[-2] = PREV = candle before CURR
    current_candle = still forming, NOT in this list
    """
    if len(candles) < 2:
        log("  [SKIP] Need 2 closed candles, have {}".format(len(candles)))
        return None, None, None, None, None, None, None

    curr = candles[-1]   # most recently closed
    prev = candles[-2]   # one before that

    closes = [c["close"] for c in candles]
    ema9   = calc_ema(closes, EMA_PERIOD)

    # Individual condition values
    c1_buy  = prev["close"] < prev["open"]        # prev RED
    c2_buy  = curr["close"] > curr["open"]        # curr GREEN
    c3_buy  = curr["close"] > prev["high"]        # engulf up
    c4_buy  = ema9 is not None and curr["close"] > ema9  # above EMA9

    c1_sell = prev["close"] > prev["open"]        # prev GREEN
    c2_sell = curr["close"] < curr["open"]        # curr RED
    c3_sell = curr["close"] < prev["low"]         # engulf down

    buy_ok  = c1_buy  and c2_buy  and c3_buy  and c4_buy
    sell_ok = c1_sell and c2_sell and c3_sell

    # Full debug log
    log("  ── SIGNAL CHECK ──────────────────────────")
    log("  PREV: O={} H={} L={} C={} [{}]".format(
        prev["open"], prev["high"], prev["low"], prev["close"], prev["color"]))
    log("  CURR: O={} H={} L={} C={} [{}]".format(
        curr["open"], curr["high"], curr["low"], curr["close"], curr["color"]))
    log("  EMA9: {}".format(round(ema9, 2) if ema9 else "not ready"))
    log("  BUY  [{}] PrevRED:{} CurrGREEN:{} Close{}>{}: {} Close{}>{}: {}".format(
        "PASS" if buy_ok else "FAIL",
        "Y" if c1_buy else "N",
        "Y" if c2_buy else "N",
        curr["close"], prev["high"], "Y" if c3_buy else "N",
        curr["close"], round(ema9, 2) if ema9 else "?",
        "Y" if c4_buy else "N"))
    log("  SELL [{}] PrevGREEN:{} CurrRED:{} Close{}<{}: {}".format(
        "PASS" if sell_ok else "FAIL",
        "Y" if c1_sell else "N",
        "Y" if c2_sell else "N",
        curr["close"], prev["low"], "Y" if c3_sell else "N"))

    if buy_ok:
        entry, sl, tp, risk, reward = buy_sl_tp(curr)
        log("  >>> BUY! E={} SL={} TP={} Risk=${}".format(entry, sl, tp, risk))
        return "buy", entry, sl, tp, risk, reward, ema9

    if sell_ok:
        entry, sl, tp, risk, reward = sell_sl_tp(curr)
        log("  >>> SELL! E={} SL={} TP={} Risk=${}".format(entry, sl, tp, risk))
        return "sell", entry, sl, tp, risk, reward, ema9

    return None, None, None, None, None, None, ema9


def safety_check(risk):
    if len(open_trades) >= MAX_OPEN_TRADES:
        log("  [SAFETY BLOCK] Max {} trades open.".format(MAX_OPEN_TRADES))
        return False
    if daily_loss >= MAX_LOSS_PER_DAY_USD:
        log("  [SAFETY BLOCK] Daily loss ${} limit.".format(daily_loss))
        return False
    if trades_today >= MAX_TRADES_PER_DAY:
        log("  [SAFETY BLOCK] Max {}/day.".format(MAX_TRADES_PER_DAY))
        return False
    since_last = time.time() - last_trade_time
    if since_last < MIN_COOLDOWN_SEC:
        log("  [SAFETY BLOCK] Cooldown {}s left.".format(
            int(MIN_COOLDOWN_SEC - since_last)))
        return False
    if risk > MAX_SL_USD:
        log("  [SAFETY BLOCK] SL ${} > max ${}. Skipping.".format(
            risk, MAX_SL_USD))
        return False
    return True


def seed_candles(seed_price, count=9):
    """
    Only seed EMA_PERIOD candles (9).
    Real candles will replace synthetic ones faster.
    """
    global candles
    p = seed_price
    for _ in range(count):
        vol     = p * 0.0008
        open_p  = round(p, 2)
        close_p = round(p + random.uniform(-vol, vol), 2)
        high_p  = round(max(open_p, close_p) + random.uniform(0, vol * 0.3), 2)
        low_p   = round(min(open_p, close_p) - random.uniform(0, vol * 0.3), 2)
        color   = "GREEN" if close_p >= open_p else "RED"
        candles.append({
            "open": open_p, "high": high_p,
            "low":  low_p,  "close": close_p,
            "color": color, "ticks": 12, "minute": -1
        })
        p = close_p
    log("  Seeded {} candles near live price ${}".format(count, seed_price))


def dashboard(price, candle_count, ema9):
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
    log("  NEXT     : {}s  |  CANDLES: {}".format(sec_left, candle_count))
    log("  TODAY    : {} trades (max {})  LOSS: ${} (max ${})".format(
        trades_today, MAX_TRADES_PER_DAY,
        round(daily_loss, 2), MAX_LOSS_PER_DAY_USD))
    log("  COOLDOWN : {}  |  MAX SL: ${}".format(
        "{}s left".format(cooldown_left) if cooldown_left > 0 else "READY",
        MAX_SL_USD))
    log("-" * 62)
    if cc:
        pct = min(100, int((60 - sec_left) / 60 * 100))
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        log("  FORMING : O={} H={} L={} C={} [{}]".format(
            cc["open"], cc["high"], cc["low"], price, cc["color"]))
        log("  PROGRESS: [{}] {}%  Ticks={}".format(bar, pct, cc["ticks"]))
    if len(candles) >= 2:
        log("  CURR(last closed): O={} H={} L={} C={} [{}]".format(
            candles[-1]["open"], candles[-1]["high"],
            candles[-1]["low"],  candles[-1]["close"],
            candles[-1]["color"]))
        log("  PREV(before curr): O={} H={} L={} C={} [{}]".format(
            candles[-2]["open"], candles[-2]["high"],
            candles[-2]["low"],  candles[-2]["close"],
            candles[-2]["color"]))
    log("-" * 62)
    if open_trades:
        log("  OPEN TRADES:")
        for t in open_trades:
            unreal = round(
                (price - t["entry"]) if t["side"] == "buy"
                else (t["entry"] - price), 2)
            s = "+" if unreal >= 0 else ""
            log("  #{} [{}] E=${} SL=${} TP=${} PNL:{}${}".format(
                t["id"], t["side"].upper(),
                t["entry"], t["sl"], t["tp"], s, unreal))
    else:
        log("  No open trades.")
    if session_trades:
        log("  SESSION: {}".format(len(session_trades)))
        for t in session_trades[-3:]:
            log("  #{} [{}] E=${} SL=${} TP=${} @{}".format(
                t["id"], t["side"].upper(),
                t["entry"], t["sl"], t["tp"], t["time"]))
    log("=" * 62)


def run():
    global last_signal_id, product_id
    global last_trade_time, trades_today, current_minute

    log("=" * 62)
    log("  DELTA EXCHANGE INDIA - BTCUSD 1MIN LIVE BOT")
    log("  Leverage:{}x  MaxSL:${}  Cooldown:{}s".format(
        LEVERAGE, MAX_SL_USD, MIN_COOLDOWN_SEC))
    log("=" * 62)

    if not API_KEY or not API_SECRET:
        log("  [ERR] API keys missing!")
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

    # Seed only 9 candles (minimum for EMA9) near live price
    log("\n  Seeding minimal candles near live price...")
    seed_candles(seed, count=9)

    current_minute = get_current_minute()
    start_candle(seed)
    candle_count = len(candles)
    last_ema9    = None
    trade_count  = 0

    sec_left = 60 - datetime.now().second
    log("\n  *** BOT IS LIVE ***")
    log("  Next candle close in {}s".format(sec_left))
    log("  Full signal debug on every candle close\n")

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
            update_open_trades(price)

            this_minute = get_current_minute()

            if this_minute != current_minute:
                log("\n" + "=" * 62)
                log("  CANDLE CLOSE :{} → :{}".format(
                    str(current_minute).zfill(2),
                    str(this_minute).zfill(2)))

                closed         = close_and_new_candle(price)
                current_minute = this_minute
                candle_count   = len(candles)

                if closed:
                    log("  CLOSED #{}  O={} H={} L={} C={} [{}] Range=${}".format(
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
                            if safety_check(risk):
                                log("\n  " + "*" * 48)
                                log("  *** {} TRADE #{} ***".format(
                                    side.upper(), trade_count + 1))
                                log("  Entry  : ${}".format(entry))
                                log("  SL     : ${}  Risk -${}".format(sl, risk))
                                log("  TP     : ${}  Reward+${}".format(tp, reward))
                                log("  " + "*" * 48)

                                order = place_order(
                                    side, entry, sl, tp, product_id)

                                if order:
                                    last_signal_id  = candle_count
                                    trade_count    += 1
                                    trades_today   += 1
                                    last_trade_time = time.time()
                                    new_trade = {
                                        "id":    trade_count,
                                        "side":  side,
                                        "entry": entry,
                                        "sl":    sl,
                                        "tp":    tp,
                                        "time":  datetime.now().strftime("%H:%M")
                                    }
                                    session_trades.append(new_trade)
                                    open_trades.append(dict(new_trade))
                                    log("  *** ORDER IS LIVE! ***")

            dashboard(price, candle_count, last_ema9)
            time.sleep(FETCH_EVERY_SEC)

        except KeyboardInterrupt:
            log("  Bot stopped.")
            break
        except Exception as e:
            log("  [ERR] {} - Retry in 5s...".format(e))
            time.sleep(5)


if __name__ == "__main__":
    run()
