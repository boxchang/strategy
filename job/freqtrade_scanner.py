# -*- coding: utf-8 -*-
"""Binance USDT-M Futures 籌碼面多空掃描器 v3.7"""
import json
import math
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://fapi.binance.com"


@dataclass
class Config:
    top_n: int = 150
    min_volume_m: float = 20.0
    period: str = "1h"
    history: int = 6
    workers: int = 5
    min_score: float = 50.0
    exclude: tuple = ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "BUSDUSDT")
    kline_interval: str = "1h"
    htf_interval: str = "4h"
    kline_limit: int = 99
    oi_window: int = 12
    max_24h_change_pct: float = 30.0  # None = 停用
    breakout_buffer_atr: float = 0.1
    enable_trend_veto: bool = False
    trend_veto: float = 0.3
    slope_bars: int = 5
    output_dir: str = ""  # 空白時輸出到本程式所在資料夾/pairs_list


# 四組權重：籌碼 30、資金流向 30、趨勢 25、成交量 15；總計 100
WEIGHTS = {
    "whale": 12, "trend": 8, "divergence": 10,
    "funding": 10, "taker": 10, "oi": 10,
    "trend_1h": 10, "trend_4h": 10, "breakout": 5,
    "volume": 15,
}
assert sum(WEIGHTS.values()) == 100

PX_THR = 0.003
OI_THR = 0.01
LN_1_5 = math.log(1.5)

# 倉減一律 0 分
LONG_OI = {
    ("up", "up"): 1.0, ("up", "flat"): 0.5, ("up", "down"): 0.0,
    ("flat", "up"): 0.5, ("flat", "flat"): 0.3, ("flat", "down"): 0.0,
    ("down", "up"): 0.0, ("down", "flat"): 0.0, ("down", "down"): 0.0,
}
SHORT_OI = {
    ("down", "up"): 1.0, ("down", "flat"): 0.5, ("down", "down"): 0.0,
    ("flat", "up"): 0.5, ("flat", "flat"): 0.3, ("flat", "down"): 0.0,
    ("up", "up"): 0.0, ("up", "flat"): 0.0, ("up", "down"): 0.0,
}
REGIME_LABEL = {
    ("up", "up"): "價漲倉增", ("up", "down"): "價漲倉減",
    ("down", "up"): "價跌倉增", ("down", "down"): "價跌倉減",
    ("flat", "up"): "價平倉增", ("flat", "down"): "價平倉減",
    ("flat", "flat"): "價平倉平",
}


def make_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.6,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=20))
    session.headers.update({"User-Agent": "whale-scanner/3.7"})
    return session


def get_json(session, path, params=None):
    response = session.get(BASE + path, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and "code" in data and "msg" in data:
        raise RuntimeError(f"{path}: {data['code']} {data['msg']}")
    return data


def clip01(x):
    return max(0.0, min(1.0, x))


def clip11(x):
    return max(-1.0, min(1.0, x))


def ema_series(values, period):
    if len(values) < period:
        raise ValueError(f"資料不足以計算 EMA{period}")
    alpha = 2 / (period + 1)
    value = sum(values[:period]) / period
    output = [value]
    for x in values[period:]:
        value = x * alpha + value * (1 - alpha)
        output.append(value)
    return output


def atr(highs, lows, closes, period=14):
    tr = [max(h-l, abs(h-pc), abs(l-pc))
          for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])]
    recent = tr[-period:]
    return sum(recent) / len(recent) if recent else 0.0


def classify(change, threshold):
    if change > threshold:
        return "up"
    if change < -threshold:
        return "down"
    return "flat"


def analyze_klines(rows, cfg):
    # 排除最後一根未收盤 K 線；timestamp 是 K 線 open time
    rows = rows[:-1]
    if len(rows) < max(55, 21 + cfg.slope_bars):
        raise ValueError(f"已收盤 K 線不足（{len(rows)} 根）")

    timestamps = [int(r[0]) for r in rows]
    o = [float(r[1]) for r in rows]
    h = [float(r[2]) for r in rows]
    l = [float(r[3]) for r in rows]
    c = [float(r[4]) for r in rows]
    v = [float(r[5]) for r in rows]

    e20s, e50s = ema_series(c, 20), ema_series(c, 50)
    e20, e50 = e20s[-1], e50s[-1]
    e20_prev = e20s[-1-cfg.slope_bars]
    a = atr(h, l, c)

    if a > 0:
        dist = clip11((c[-1] - e20) / (2*a))
        spread = clip11((e20 - e50) / (2*a))
        slope = clip11((e20 - e20_prev) / a)
        trend = 0.4*dist + 0.4*spread + 0.2*slope
    else:
        trend = 0.0

    hi, lo = max(h[-21:-1]), min(l[-21:-1])
    pos = clip11(2*(c[-1]-lo)/(hi-lo)-1) if hi > lo else 0.0

    # 突破需超出前 20 根高低點至少 0.1 ATR
    buffer = cfg.breakout_buffer_atr * a
    if a > 0 and c[-1] > hi + buffer:
        breakout = 0.5 + 0.5*clip01((c[-1]-hi-buffer)/a)
    elif a > 0 and c[-1] < lo - buffer:
        breakout = -(0.5 + 0.5*clip01((lo-buffer-c[-1])/a))
    else:
        breakout = 0.0

    vol_avg = sum(v[-21:-1]) / 20
    vr = v[-1] / vol_avg if vol_avg > 0 else 1.0
    body = 1 if c[-1] > o[-1] else -1 if c[-1] < o[-1] else 0
    vol_dir = body * clip01(vr - 1.0)

    return {
        "timestamps": timestamps, "closes": c, "close": c[-1], "atr": a,
        "atr_pct": a/c[-1]*100 if c[-1] else 0.0,
        "trend": trend, "pos": pos, "breakout": breakout,
        "vol_ratio": vr, "vol_dir": vol_dir,
    }


def analyze_oi_aligned(oi_rows, k1, window):
    """
    用 sumOpenInterest（持倉數量），不用 sumOpenInterestValue（名義價值）。
    OI timestamp 與已收盤 1H K 線 open timestamp 精確對齊。
    window 個變化區間需要 window+1 個時間點。
    """
    oi_by_ts = {
        int(row["timestamp"]): float(row["sumOpenInterest"])
        for row in oi_rows
        if float(row.get("sumOpenInterest", 0)) > 0
    }
    close_by_ts = dict(zip(k1["timestamps"], k1["closes"]))
    common = sorted(set(oi_by_ts).intersection(close_by_ts))
    needed = window + 1
    if len(common) < needed:
        raise ValueError(
            f"OI/K線共同 timestamp 只有 {len(common)} 個，至少需要 {needed} 個；"
            "此標的略過，避免使用未對齊資料"
        )

    selected = common[-needed:]
    oi_start, oi_end = oi_by_ts[selected[0]], oi_by_ts[selected[-1]]
    px_start, px_end = close_by_ts[selected[0]], close_by_ts[selected[-1]]
    if min(oi_start, px_start) <= 0:
        raise ValueError("OI 或價格起始值無效")

    return {
        "oi_change": oi_end/oi_start - 1,
        "price_change": px_end/px_start - 1,
        "matched_points": len(selected),
    }


def get_universe(session, cfg):
    tickers = get_json(session, "/fapi/v1/ticker/24hr")
    premium = {x["symbol"]: x for x in get_json(session, "/fapi/v1/premiumIndex")}

    intervals = {}
    try:
        for x in get_json(session, "/fapi/v1/fundingInfo"):
            intervals[x["symbol"]] = int(x.get("fundingIntervalHours", 8))
    except Exception as exc:
        print(f"[警告] fundingInfo 取得失敗，使用 8h 預設值：{exc}")

    rows = []
    for t in tickers:
        sym = t["symbol"]
        if not sym.endswith("USDT") or sym in cfg.exclude or sym not in premium:
            continue
        vol_m = float(t["quoteVolume"]) / 1_000_000
        if vol_m < cfg.min_volume_m:
            continue

        change_24h = float(t["priceChangePercent"])
        if cfg.max_24h_change_pct is not None:
            if abs(change_24h) > cfg.max_24h_change_pct:
                continue

        hours = intervals.get(sym, 8)
        if hours <= 0:
            hours = 8
        raw_fr = float(premium[sym]["lastFundingRate"])
        rows.append({
            "symbol": sym, "volume_m": vol_m,
            "price_change_24h": change_24h,
            "last_price": float(t.get("lastPrice") or 0),
            "funding_raw": raw_fr,
            "funding_8h": raw_fr * 8 / hours,
            "funding_hours": hours,
        })

    rows.sort(key=lambda x: x["volume_m"], reverse=True)
    return rows[:cfg.top_n]


def get_series(session, path, symbol, period, limit, key):
    data = get_json(session, path, {
        "symbol": symbol, "period": period, "limit": limit
    })
    if not data:
        return []
    data.sort(key=lambda x: int(x["timestamp"]))
    return [float(x[key]) for x in data]


def fetch_symbol(session, base, cfg):
    sym = base["symbol"]

    # Top = 頂級交易者持倉多空比；非全體大戶資金流
    top_position = get_series(
        session, "/futures/data/topLongShortPositionRatio",
        sym, cfg.period, cfg.history, "longShortRatio"
    )
    # Crowd 改稱全體帳戶多空比；不是純散戶比例
    global_accounts = get_series(
        session, "/futures/data/globalLongShortAccountRatio",
        sym, cfg.period, cfg.history, "longShortRatio"
    )
    taker = get_series(
        session, "/futures/data/takerlongshortRatio",
        sym, "1h", 3, "buySellRatio"
    )

    if not top_position:
        raise ValueError("缺少頂級交易者持倉多空比")
    if not global_accounts:
        raise ValueError("缺少全體帳戶多空比")
    if not taker:
        raise ValueError("缺少 Taker 買賣比")

    tk = sum(taker[-2:]) / len(taker[-2:])

    rows_1h = get_json(session, "/fapi/v1/klines", {
        "symbol": sym, "interval": cfg.kline_interval,
        "limit": cfg.kline_limit
    })
    rows_4h = get_json(session, "/fapi/v1/klines", {
        "symbol": sym, "interval": cfg.htf_interval,
        "limit": cfg.kline_limit
    })
    k1 = analyze_klines(rows_1h, cfg)
    k4 = analyze_klines(rows_4h, cfg)

    # window 個區間需要 window+1 筆 OI
    # 多抓 6 筆，避免最新未完成時段被排除後，交集不足
    oi_fetch_limit = min(cfg.oi_window + 6, 500)

    oi_rows = get_json(session, "/futures/data/openInterestHist", {
        "symbol": sym,
        "period": cfg.kline_interval,
        "limit": oi_fetch_limit
    })
    oi = analyze_oi_aligned(oi_rows, k1, cfg.oi_window)

    top_now = top_position[-1]
    top_prev = sum(top_position[:-1])/len(top_position[:-1]) if len(top_position) > 1 else top_now
    crowd_now = global_accounts[-1]
    if min(top_now, top_prev, crowd_now) <= 0:
        raise ValueError("Top/Crowd 多空比資料無效")

    m = dict(base)
    m.update({
        "top_position_ratio": top_now,
        "top_position_trend": math.log(top_now) - math.log(top_prev),
        "global_account_ratio": crowd_now,
        "taker_ratio": tk,
        "close": k1["close"], "atr": k1["atr"], "atr_pct": k1["atr_pct"],
        "trend_1h": k1["trend"], "trend_4h": k4["trend"],
        "pos": k1["pos"], "breakout": k1["breakout"],
        "vol_ratio": k1["vol_ratio"], "vol_dir": k1["vol_dir"],
        "oi_change_pct": oi["oi_change"]*100,
        "price_change_window_pct": oi["price_change"]*100,
        "price_state": classify(oi["price_change"], PX_THR),
        "oi_state": classify(oi["oi_change"], OI_THR),
        "oi_matched_points": oi["matched_points"],
    })
    return m


def score_symbol(m, cfg):
    ln_top = math.log(m["top_position_ratio"])
    # 這是頂級交易者持倉比 vs 全體帳戶比，不應稱為大戶 vs 散戶
    ln_div = ln_top - math.log(m["global_account_ratio"])
    fr, tk = m["funding_8h"], m["taker_ratio"]
    key = (m["price_state"], m["oi_state"])

    oi_strength = 0.5 + 0.5*clip01(abs(m["oi_change_pct"])/5.0)

    long_f = {
        "whale": clip01(ln_top/LN_1_5),
        "trend": clip01(m["top_position_trend"]/0.10),
        "divergence": clip01(ln_div/LN_1_5),
        # 對稱 Funding：-0.01% 起分，-0.04% 滿分
        "funding": clip01((-fr-0.0001)/0.0003),
        "taker": clip01((tk-0.95)/0.25),
        "oi": LONG_OI.get(key, 0.0)*oi_strength,
        "trend_1h": clip01(m["trend_1h"]),
        "trend_4h": clip01(m["trend_4h"]),
        "breakout": clip01(m["breakout"]),
        "volume": clip01(m["vol_dir"]),
    }
    short_f = {
        "whale": clip01(-ln_top/LN_1_5),
        "trend": clip01(-m["top_position_trend"]/0.10),
        "divergence": clip01(-ln_div/LN_1_5),
        # 對稱 Funding：+0.01% 起分，+0.04% 滿分
        "funding": clip01((fr-0.0001)/0.0003),
        "taker": clip01((1.05-tk)/0.25),
        "oi": SHORT_OI.get(key, 0.0)*oi_strength,
        "trend_1h": clip01(-m["trend_1h"]),
        "trend_4h": clip01(-m["trend_4h"]),
        "breakout": clip01(-m["breakout"]),
        "volume": clip01(-m["vol_dir"]),
    }

    def total(factors):
        return sum(factors[k]*w for k, w in WEIGHTS.items())

    long_total, short_total = total(long_f), total(short_f)

    if cfg.enable_trend_veto:
        if m["trend_4h"] <= -cfg.trend_veto:
            long_total = 0.0
        if m["trend_4h"] >= cfg.trend_veto:
            short_total = 0.0

    return long_total, short_total, long_f, short_f


def disp_width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(s))


def pad(s, width, align="<"):
    s = str(s)
    gap = max(0, width-disp_width(s))
    return s + " "*gap if align == "<" else " "*gap + s


COLS = [
    ("幣種", 14, "<"), ("成交額", 9, ">"),
    ("Top持倉", 8, ">"), ("全體帳戶", 8, ">"),
    ("Funding/週期", 14, ">"), ("Taker", 7, ">"),
    ("OI數量變化", 10, ">"), ("價格變化", 9, ">"),
    ("OI×價格", 14, "<"), ("多分", 7, ">"), ("空分", 7, ">"),
]


def table_row(cells):
    return " | ".join(pad(v, w, a) for v, (_, w, a) in zip(cells, COLS))


def print_table_header():
    print(table_row([c[0] for c in COLS]))
    print("-+-".join("-"*w for _, w, _ in COLS))


def write_watchlists(longs, shorts, cfg):
    out_dir = Path(cfg.output_dir).expanduser().resolve() if cfg.output_dir else (
        Path(__file__).resolve().parent / "pairs_list"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())
    long_pairs = [f"{x['symbol'][:-4]}/USDT:USDT" for x in longs]
    short_pairs = [f"{x['symbol'][:-4]}/USDT:USDT" for x in shorts]
    combined = list(dict.fromkeys(long_pairs + short_pairs))

    for filename, pairs in (
        ("long_watchlist.json", long_pairs),
        ("short_watchlist.json", short_pairs),
        ("watchlist.json", combined),
    ):
        target = out_dir / filename
        temp = out_dir / (filename + ".tmp")
        with open(temp, "w", encoding="utf-8") as f:
            json.dump({"updated_at": timestamp, "pairs": pairs},
                      f, indent=2, ensure_ascii=False)
        temp.replace(target)

    print(f"\nWatchlist 輸出位置：{out_dir}")
    print(f"多方 {len(long_pairs)} 檔 / 空方 {len(short_pairs)} 檔 / 合併 {len(combined)} 檔")


def scan(cfg=None):
    cfg = cfg or Config()
    t0 = time.time()
    session = make_session()

    print("=== Binance USDT-M Futures 籌碼面多空掃描器 v3.7 ===")
    print(f"24H 漲跌幅過濾：{'停用' if cfg.max_24h_change_pct is None else '±' + str(cfg.max_24h_change_pct) + '%'}")
    print("評分群組：籌碼 30 / 資金流向 30 / 趨勢結構 25 / 成交量 15\n")

    universe = get_universe(session, cfg)
    print(f"通過成交額及 24H 漲跌幅篩選：{len(universe)} 檔\n")

    results, failed = [], []
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        futures = {pool.submit(fetch_symbol, session, b, cfg): b["symbol"] for b in universe}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                failed.append((symbol, str(exc)))
                print(f"[略過] {symbol}: {exc}")

    longs, shorts = [], []
    print_table_header()

    for m in sorted(results, key=lambda x: x["volume_m"], reverse=True):
        ls, ss, lf, sf = score_symbol(m, cfg)
        regime = REGIME_LABEL.get((m["price_state"], m["oi_state"]), "未知")
        print(table_row([
            m["symbol"], f"${m['volume_m']:.1f}M",
            f"{m['top_position_ratio']:.2f}", f"{m['global_account_ratio']:.2f}",
            f"{m['funding_raw']*100:+.3f}%/{m['funding_hours']}h",
            f"{m['taker_ratio']:.2f}", f"{m['oi_change_pct']:+.2f}%",
            f"{m['price_change_window_pct']:+.2f}%", regime,
            f"{ls:.1f}", f"{ss:.1f}",
        ]))

        # 保留原本候選條件：Whale 有方向、達最低分、且多空分數較高
        if ls >= cfg.min_score and ls > ss:
            m["signal_score"] = round(ls, 1)
            longs.append(m)
        elif ss >= cfg.min_score and ss > ls:
            m["signal_score"] = round(ss, 1)
            shorts.append(m)

    longs.sort(key=lambda x: x["signal_score"], reverse=True)
    shorts.sort(key=lambda x: x["signal_score"], reverse=True)

    def show(title, items):
        print("\n" + "="*68 + "\n" + title + "\n" + "="*68)
        if not items:
            print("目前無符合條件的標的。")
        for x in items:
            print(
                f"{x['symbol']:<16} 分數 {x['signal_score']:>5.1f} "
                f"收盤 {x['close']:.8g} "
                f"OI {x['oi_change_pct']:+.2f}% "
                f"價格 {x['price_change_window_pct']:+.2f}%"
            )

    show("🟢 多方候選標的", longs)
    show("🔴 空方候選標的", shorts)
    write_watchlists(longs, shorts, cfg)

    if failed:
        print(f"\n失敗/略過 {len(failed)} 檔（最多列出 20 檔）：")
        for symbol, reason in failed[:20]:
            print(f"  {symbol}: {reason}")

    print(
        f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"完成，耗時 {time.time()-t0:.1f}s；"
        f"成功 {len(results)} 檔，失敗 {len(failed)} 檔。"
    )


if __name__ == "__main__":
    scan()
