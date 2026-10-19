#!/bin/bash

STRATEGY_NAME="TrendMeanReversionL"
STRATEGY_VERSION="6"
TIMEFRAME="15m"
TIMERANGE="20260101-20261030"
OUTPUT_FILE="user_data/backtest_results/result.json"

# 回測控制參數（已移除無法直接帶入的 leverage）
MAX_OPEN_TRADES=1
STAKE_AMOUNT="unlimited"
DRY_RUN_WALLET=100

# 定義本次回測要測試的 Pairs
PAIRS=(
    "MU/USDT:USDT"
)

# 0. 執行 Freqtrade 下載歷史 K 線資料
echo "📥 正在下載歷史 K 線資料 (${TIMEFRAME} | ${TIMERANGE})..."
docker-compose run --rm strategy03 download-data \
    --config user_data/strategy_config/strategy03.json \
    --timeframe $TIMEFRAME \
    --timerange $TIMERANGE \
    -p "${PAIRS[@]}"

# 檢查資料下載是否成功，若失敗則中斷後續腳本
if [ $? -ne 0 ]; then
    echo "❌ 歷史資料下載失敗，停止執行回測。"
    exit 1
fi

# 1. 執行 Freqtrade 回測並輸出 JSON
echo "🚀 開始執行 Freqtrade 回測..."
docker-compose run --rm strategy03 backtesting \
    --config user_data/strategy_config/strategy03.json \
    --strategy $STRATEGY_NAME \
    --timeframe $TIMEFRAME \
    --timerange $TIMERANGE \
    --max-open-trades $MAX_OPEN_TRADES \
    --stake-amount $STAKE_AMOUNT \
    --dry-run-wallet $DRY_RUN_WALLET \
    --export trades \
    --export-filename $OUTPUT_FILE \
    -p "${PAIRS[@]}"

# 2. 調用 Python 腳本將 JSON 與相關參數上傳至資料庫
echo "💾 正在上傳回測結果至資料庫..."
python3 upload_backtest.py \
    --dir user_data/backtest_results \
    --strategy $STRATEGY_NAME \
    --version $STRATEGY_VERSION \
    --timerange $TIMERANGE \
    --max-open-trades $MAX_OPEN_TRADES \
    --stake-amount "$STAKE_AMOUNT" \
    --dry-run-wallet $DRY_RUN_WALLET \
    --pairs "${PAIRS[@]}"