# -*- coding: utf-8 -*-
"""Binance Tradifi Equity Futures 前 100 大成交額清單產生器（Freqtrade 專用）"""
import json
import time
from pathlib import Path
import requests

BASE = "https://fapi.binance.com"


def get_top_tradifi_equity_symbols(top_n=100):
    """1. 取得幣安合約規格與 24 小時成交額，篩選出 TRADIFI_PERPETUAL + EQUITY 並排序"""
    info_url = f"{BASE}/fapi/v1/exchangeInfo"
    ticker_url = f"{BASE}/fapi/v1/ticker/24hr"

    try:
        print("正在向幣安請求合約規格與 24hr 行情資料...")
        info_res = requests.get(info_url, timeout=10)
        info_res.raise_for_status()
        exchange_info = info_res.json()

        # 篩選出符合條件的 symbol
        filtered_symbols = set()
        for s in exchange_info.get("symbols", []):
            if (
                    s.get("status") == "TRADING"
                    and s.get("contractType") == "TRADIFI_PERPETUAL"
                    and s.get("underlyingType") == "EQUITY"
            ):
                filtered_symbols.add(s["symbol"])

        if not filtered_symbols:
            print("[警告] 目前沒有找到符合條件的 TRADIFI_PERPETUAL / EQUITY 交易對。")
            return []

        # 取得 24 小時行情資料以獲取成交額 (quoteVolume)
        ticker_res = requests.get(ticker_url, timeout=10)
        ticker_res.raise_for_status()
        tickers = ticker_res.json()

        symbol_volumes = []
        for t in tickers:
            sym = t.get("symbol")
            if sym in filtered_symbols:
                quote_volume = float(t.get("quoteVolume", 0.0))
                symbol_volumes.append({"symbol": sym, "quoteVolume": quote_volume})

        # 依照成交額由大到小排序 (Descending)
        symbol_volumes.sort(key=lambda x: x["quoteVolume"], reverse=True)

        # 取前 N 大
        top_symbols = symbol_volumes[:top_n]

        print(f"\n成功篩選出符合條件的標的共 {len(symbol_volumes)} 個，取前 {len(top_symbols)} 大：")
        print("-" * 55)
        print(f"{'排名':<6} {'交易對 (Symbol)':<15} {'24h 成交額 (USDT)':<25}")
        print("-" * 55)

        for idx, item in enumerate(top_symbols, 1):
            print(f"{idx:<6} {item['symbol']:<15} ${item['quoteVolume']:>20,.2f}")

        return [item["symbol"] for item in top_symbols]

    except Exception as e:
        print(f"API 請求失敗: {e}")
        return []


def write_watchlist(symbols):
    """2. 將抓到的清單轉換為 Freqtrade 格式並寫入 user_data/pairs_list/top_watchlist.json"""
    # 確保以本程式所在目錄為基準，精準指向 user_data/pairs_list
    base_dir = Path(__file__).resolve().parent.parent

    out_dir = base_dir / "user_data" / "pairs_list"
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())

    # 轉換為 Freqtrade 支援的格式：TSMUSDT -> TSM/USDT:USDT
    freqtrade_pairs = [f"{sym[:-4]}/USDT:USDT" for sym in symbols]

    target = out_dir / "top_watchlist.json"
    temp = out_dir / "top_watchlist.json.tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": timestamp,
            "pairs": freqtrade_pairs
        }, f, indent=2, ensure_ascii=False)
    temp.replace(target)

    print(f"\n[Freqtrade 產出] Watchlist 檔案已成功更新至：{target.resolve()}")
    print(f" -> 成功寫入 {len(freqtrade_pairs)} 檔股票虛擬合約至 top_watchlist.json")


if __name__ == "__main__":
    t0 = time.time()
    print("=== Binance Tradifi Equity 前 100 大成交額清單產生器 ===")

    # 取得前 100 大 EQUITY 交易對
    top_equity_symbols = get_top_tradifi_equity_symbols(100)

    if top_equity_symbols:
        # 寫入指定的 top_watchlist.json
        write_watchlist(top_equity_symbols)
    else:
        print("未能取得任何有效清單。")

    print(f"\n執行完成，總耗時 {time.time() - t0:.2f} 秒。")