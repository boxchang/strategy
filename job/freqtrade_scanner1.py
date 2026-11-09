import csv
import json
import math
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://fapi.binance.com"


# ----------------------------- 設定 ----------------------------- #
@dataclass
class Config:
    top_n: int = 100  # 取成交額前 100 名
    min_volume_m: float = 20.0  # 最低 24h 成交額（百萬 USDT）
    period: str = "1h"  # 預設序列週期
    history: int = 6  # 序列取最近幾根計算趨勢
    workers: int = 5  # 併發數
    min_score: float = 50.0  # 進入候選名單的最低分
    save_csv: bool = False  # 是否輸出 CSV
    exclude: tuple = ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "BUSDUSDT")

    # --- K 線 / OI ---
    kline_interval: str = "1h"  # 主要結構週期
    htf_interval: str = "4h"  # 高一級週期趨勢確認
    kline_limit: int = 99  # 確保足夠計算基礎結構
    oi_window: int = 12  # OI 與價格比較的窗口（根數）
    max_24h_change_pct: float = 30.0


# 各因子權重（總和 100，Taker 20 分）
WEIGHTS = {
    "whale": 20,  # 大戶持倉多空比
    "trend": 5,  # 大戶比例近期變化
    "divergence": 10,  # 大戶 vs 散戶 分歧
    "funding": 15,  # 資金費率（8h 等效）
    "taker": 20,  # 主動買賣比
    "oi": 10,  # 持倉量 × 價格 的組合狀態
    "trend_1h": 5,  # 主週期技術結構
    "trend_4h": 5,  # 高週期結構
    "breakout": 5,  # 區間突破程度
    "volume": 5,  # 放量方向
}

PX_THR = 0.003
OI_THR = 0.01

LONG_OI = {
    # 價漲
    ("up", "up"): 1.0,       # 價漲倉增：新多進場
    ("up", "flat"): 0.5,     # 價漲倉平：中性
    ("up", "down"): 0.0,     # 價漲倉減：空頭回補，不得分

    # 價平
    ("flat", "up"): 0.5,     # 價平倉增
    ("flat", "flat"): 0.3,
    ("flat", "down"): 0.0,   # 價平倉減，不得分

    # 價跌
    ("down", "up"): 0.0,     # 價跌倉增：新空進場
    ("down", "flat"): 0.0,
    ("down", "down"): 0.0,   # 價跌倉減：多頭出清，不得分
}

SHORT_OI = {
    # 價跌
    ("down", "up"): 1.0,     # 價跌倉增：新空進場
    ("down", "flat"): 0.5,
    ("down", "down"): 0.0,   # 價跌倉減：多頭出清，不得分

    # 價平
    ("flat", "up"): 0.5,
    ("flat", "flat"): 0.3,
    ("flat", "down"): 0.0,   # 價平倉減，不得分

    # 價漲
    ("up", "down"): 0.0,     # 價漲倉減：空頭回補，不得分
    ("up", "flat"): 0.0,
    ("up", "up"): 0.0,       # 價漲倉增：新多進場
}
REGIME_LABEL = {("up", "up"): "價漲倉增(新多進場)", ("up", "down"): "價漲倉減(空頭回補)",
                ("down", "up"): "價跌倉增(新空進場)", ("down", "down"): "價跌倉減(多頭出清)"}


# ----------------------------- 網路層 ----------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=20))
    s.headers.update({"User-Agent": "whale-scanner/3.5"})
    return s


def get_json(session, path, params=None):
    r = session.get(BASE + path, params=params, timeout=8)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "code" in data and "msg" in data:
        raise RuntimeError(f"{path}: {data['code']} {data['msg']}")
    return data


# ----------------------------- 指標工具 ----------------------------- #
def clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def clip11(x: float) -> float:
    return max(-1.0, min(1.0, x))


def ema(values, n):
    k = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def atr(highs, lows, closes, n=14):
    trs = [max(h - l, abs(h - pc), abs(l - pc)) for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])]
    return sum(trs[-n:]) / n


def classify(change, thr):
    return "up" if change > thr else "down" if change < -thr else "flat"


def analyze_klines(rows):
    rows = rows[:-1]
    if len(rows) < 55:
        raise ValueError(f"K 線不足（{len(rows)} 根）")
    o = [float(r[1]) for r in rows]
    h = [float(r[2]) for r in rows]
    l = [float(r[3]) for r in rows]
    c = [float(r[4]) for r in rows]
    v = [float(r[5]) for r in rows]

    e20, e50 = ema(c, 20), ema(c, 50)
    a = atr(h, l, c)

    trend = 0.5 * (1 if c[-1] > e20 else -1) + 0.5 * (1 if e20 > e50 else -1)
    hi, lo = max(h[-21:-1]), min(l[-21:-1])
    pos = clip11(2 * (c[-1] - lo) / (hi - lo) - 1) if hi > lo else 0.0

    vol_avg = sum(v[-21:-1]) / 20
    vr = v[-1] / vol_avg if vol_avg > 0 else 1.0
    body = 1 if c[-1] > o[-1] else -1 if c[-1] < o[-1] else 0
    vol_dir = body * clip01(vr - 1.0)

    return {
        "close": c[-1], "atr": a,
        "atr_pct": a / c[-1] * 100 if c[-1] else 0.0,
        "trend": trend, "pos": pos, "vol_ratio": vr, "vol_dir": vol_dir,
        "closes": c,
    }


def analyze_oi(oi_rows, closes, window):
    oi_rows = sorted(oi_rows, key=lambda x: x["timestamp"])
    vals = [float(x["sumOpenInterestValue"]) for x in oi_rows]
    if len(vals) < 2 or vals[0] <= 0:
        raise ValueError("OI 資料不足")
    n = min(window, len(vals), len(closes))
    return vals[-1] / vals[-n] - 1, closes[-1] / closes[-n] - 1


# ----------------------------- 資料抓取 ----------------------------- #
def get_universe(session, cfg: Config):
    tickers = get_json(session, "/fapi/v1/ticker/24hr")
    premium = {x["symbol"]: x for x in get_json(session, "/fapi/v1/premiumIndex")}

    intervals = {}
    try:
        for x in get_json(session, "/fapi/v1/fundingInfo"):
            intervals[x["symbol"]] = int(x.get("fundingIntervalHours", 8))
    except Exception as e:
        print(f"[警告] 取得 fundingInfo 失敗: {e}")

    rows = []
    for t in tickers:
        sym = t["symbol"]
        if not sym.endswith("USDT") or sym in cfg.exclude or sym not in premium:
            continue
        vol_m = float(t["quoteVolume"]) / 1_000_000
        if vol_m < cfg.min_volume_m:
            continue
        hours = intervals.get(sym, 8)
        raw_fr = float(premium[sym]["lastFundingRate"])
        rows.append({
            "symbol": sym, "volume_m": vol_m,
            "price_change": float(t["priceChangePercent"]),
            "last_price": float(t.get("lastPrice") or 0),
            "funding_raw": raw_fr, "funding_8h": raw_fr * 8 / hours, "funding_hours": hours,
        })
    rows.sort(key=lambda x: x["volume_m"], reverse=True)
    return rows[: cfg.top_n]


def get_series(session, path, symbol, period, limit, key):
    try:
        data = get_json(session, path, {"symbol": symbol, "period": period, "limit": limit})
        if not data:
            return []
        data.sort(key=lambda x: x["timestamp"])
        return [max(float(x[key]), 1e-6) for x in data]
    except Exception:
        return []


def fetch_symbol(session, base: dict, cfg: Config):
    sym, n = base["symbol"], cfg.history

    top = get_series(session, "/futures/data/topLongShortPositionRatio", sym, cfg.period, n, "longShortRatio")
    crowd = get_series(session, "/futures/data/globalLongShortAccountRatio", sym, cfg.period, n, "longShortRatio")

    taker_1h = get_series(session, "/futures/data/takerlongshortRatio", sym, "1h", 3, "buySellRatio")
    tk_1h = sum(taker_1h[-2:]) / len(taker_1h[-2:]) if taker_1h else 1.0

    k1 = analyze_klines(
        get_json(session, "/fapi/v1/klines", {"symbol": sym, "interval": cfg.kline_interval, "limit": cfg.kline_limit}))
    k4 = analyze_klines(
        get_json(session, "/fapi/v1/klines", {"symbol": sym, "interval": cfg.htf_interval, "limit": cfg.kline_limit}))
    oi_rows = get_json(session, "/futures/data/openInterestHist",
                       {"symbol": sym, "period": cfg.kline_interval, "limit": cfg.oi_window})
    oi_chg, px_chg = analyze_oi(oi_rows, k1["closes"], cfg.oi_window)

    top_now = top[-1] if top else 1.0
    top_prev = sum(top[:-1]) / len(top[:-1]) if len(top) > 1 else top_now

    m = dict(base)
    m.update({
        "top_ratio": top_now,
        "top_trend": math.log(top_now) - math.log(top_prev) if top_prev > 0 else 0,
        "crowd_ratio": crowd[-1] if crowd else 1.0,
        "taker_ratio": tk_1h,
        "close": k1["close"], "atr": k1["atr"], "atr_pct": k1["atr_pct"],
        "trend_1h": k1["trend"], "trend_4h": k4["trend"],
        "pos": k1["pos"], "vol_ratio": k1["vol_ratio"], "vol_dir": k1["vol_dir"],
        "oi_chg_pct": oi_chg * 100, "px_chg_win_pct": px_chg * 100,
        "px_state": classify(px_chg, PX_THR), "oi_state": classify(oi_chg, OI_THR),
    })
    return m


# ----------------------------- 評分邏輯 ----------------------------- #
LN_1_5 = math.log(1.5)


def score_symbol(m: dict):
    ln_top = math.log(m["top_ratio"]) if m["top_ratio"] > 0 else 0
    ln_div = ln_top - math.log(m["crowd_ratio"]) if m["crowd_ratio"] > 0 else 0
    fr, tk = m["funding_8h"], m["taker_ratio"]

    key = (m["px_state"], m["oi_state"])
    oi_strength = 0.5 + 0.5 * clip01(abs(m["oi_chg_pct"]) / 5.0)

    long_f = {
        "whale": clip01(ln_top / LN_1_5),
        "trend": clip01(m["top_trend"] / 0.10),
        "divergence": clip01(ln_div / LN_1_5),
        "funding": clip01((0.0001 - fr) / 0.0004),
        "taker": clip01((tk - 0.95) / 0.25),
        "oi": LONG_OI.get(key, 0.3) * oi_strength,
        "trend_1h": clip01(m["trend_1h"]),
        "trend_4h": clip01(m["trend_4h"]),
        "breakout": clip01(m["pos"]),
        "volume": clip01(m["vol_dir"]),
    }
    short_f = {
        "whale": clip01(-ln_top / LN_1_5),
        "trend": clip01(-m["top_trend"] / 0.10),
        "divergence": clip01(-ln_div / LN_1_5),
        "funding": clip01((fr - 0.0001) / 0.0003),
        "taker": clip01((1.05 - tk) / 0.25),
        "oi": SHORT_OI.get(key, 0.3) * oi_strength,
        "trend_1h": clip01(-m["trend_1h"]),
        "trend_4h": clip01(-m["trend_4h"]),
        "breakout": clip01(-m["pos"]),
        "volume": clip01(-m["vol_dir"]),
    }

    def total(f):
        return sum(f[k] * w for k, w in WEIGHTS.items())

    return total(long_f), total(short_f), long_f, short_f


# ----------------------------- 表格與輸出 ----------------------------- #
def disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def pad(s, width: int, align: str = "<") -> str:
    s = str(s)
    gap = max(0, width - disp_width(s))
    return (s + " " * gap) if align == "<" else (" " * gap + s)


COLS = [("幣種", 12, "<"), ("成交額", 9, ">"), ("大戶", 6, ">"), ("散戶", 6, ">"),
        ("費率/週期", 12, ">"), ("taker", 6, ">"), ("OI變化", 8, ">"), ("價變化", 8, ">"),
        ("OI×價狀態", 18, "<"), ("多", 5, ">"), ("空", 5, ">")]


def table_row(cells) -> str:
    return " | ".join(pad(v, w, a) for v, (_, w, a) in zip(cells, COLS))


def print_table_header():
    print(table_row([c[0] for c in COLS]))
    print("-+-".join("-" * w for _, w, _ in COLS))


def scan(cfg: Config = Config()):
    t0 = time.time()
    session = make_session()
    print("=== 🚀 幣安合約 籌碼面多空分類掃描器 v3.5（純籌碼多空判定 + JSON 輸出）===")

    universe = get_universe(session, cfg)
    print(f"資金焦點幣種 ({len(universe)} 檔, ≥${cfg.min_volume_m:.0f}M)\n")

    results, failed = [], []
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        futs = {pool.submit(fetch_symbol, session, b, cfg): b["symbol"] for b in universe}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                failed.append(sym)
                print(f"[略過] {sym}: {e}")

    longs, shorts = [], []
    print_table_header()
    for m in sorted(results, key=lambda x: x["volume_m"], reverse=True):
        ls, ss, lf, sf = score_symbol(m)
        m["long_score"], m["short_score"] = round(ls, 1), round(ss, 1)
        regime = REGIME_LABEL.get((m["px_state"], m["oi_state"]), "中性/盤整")
        print(table_row([
            m["symbol"], f"${m['volume_m']:.1f}M", f"{m['top_ratio']:.2f}", f"{m['crowd_ratio']:.2f}",
            f"{m['funding_raw'] * 100:+.3f}%/{m['funding_hours']}h", f"{m['taker_ratio']:.2f}",
            f"{m['oi_chg_pct']:+.1f}%", f"{m['px_chg_win_pct']:+.1f}%", regime,
            f"{ls:.1f}", f"{ss:.1f}",
        ]))

        # 純粹透過籌碼與分數判定多空類別
        if lf["whale"] > 0 and ls >= cfg.min_score and ls > ss:
            m["signal_score"] = round(ls, 1)
            longs.append(m)
        elif sf["whale"] > 0 and ss >= cfg.min_score and ss > ls:
            m["signal_score"] = round(ss, 1)
            shorts.append(m)

    longs.sort(key=lambda x: x["signal_score"], reverse=True)
    shorts.sort(key=lambda x: x["signal_score"], reverse=True)

    def show(title, items):
        print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)
        if not items:
            print(f"目前無符合條件的標的。")
        for x in items:
            print(f"👉 {x['symbol']}  分數 {x['signal_score']:.1f}   收盤 {x['close']:.6g}")

    show("🟢 【多方】籌碼候選標的", longs)
    show("🔴 【空方】籌碼候選標的", shorts)

    # === 產出 Freqtrade JSON Watchlist 檔案 ===
    from pathlib import Path

    # 自動定位：以當前腳本所在資料夾為基準，往上一層找到 user_data/pairs_list
    base_dir = Path(__file__).resolve().parent.parent
    pairs_dir = base_dir / "user_data" / "pairs_list"
    pairs_dir.mkdir(parents=True, exist_ok=True)  # 如果資料夾不存在則自動建立

    current_timestamp = int(time.time())
    long_symbols = [f"{x['symbol'][:-4]}/USDT:USDT" for x in longs]
    short_symbols = [f"{x['symbol'][:-4]}/USDT:USDT" for x in shorts]
    combined_symbols = list(set(long_symbols + short_symbols))

    # 寫入檔案
    with open(pairs_dir / "long_watchlist.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": current_timestamp, "pairs": long_symbols}, f, indent=2, ensure_ascii=False)

    with open(pairs_dir / "short_watchlist.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": current_timestamp, "pairs": short_symbols}, f, indent=2, ensure_ascii=False)

    with open(pairs_dir / "watchlist.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": current_timestamp, "pairs": combined_symbols}, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 64)
    print("🤖 已成功產生 Freqtrade Watchlist 檔案：")
    print("   ├─ long_watchlist.json")
    print("   └─ short_watchlist.json")
    print("   └─ watchlist.json")
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ----------------------------------------")
    print("=" * 64)

    print(f"\n完成，耗時 {time.time() - t0:.1f}s" + (f"，失敗 {len(failed)} 檔" if failed else ""))


if __name__ == "__main__":
    scan()