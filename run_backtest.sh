#!/bin/bash

# Step 1: 確認交易所支援的時間週期
echo "=== Step 1: List available timeframes ==="
docker-compose run --rm strategy2 \
    list-timeframes \
    --config /freqtrade/user_data/config_person.json

# Step 2: 下載資料 (15m + 1h)
echo "=== Step 2: Download data (15m + 1h) ==="
docker-compose run --rm strategy2 \
    download-data \
    --timeframes 15m 1h \
    --config /freqtrade/user_data/config_person.json \
    --datadir /freqtrade/user_data/data \
    --timerange 20260501- 

# Step 3: 執行回測
echo "=== Step 3: Run backtesting ==="
docker-compose run --rm strategy2 \
    backtesting \
    --strategy SimpleRSIStrategy \
    --config /freqtrade/user_data/config_person.json \
    --datadir /freqtrade/user_data/data \
    --timerange 20260501- 


# Step 4: 執行策略優化 (Hyperopt)
echo "=== Step 4: Run hyperopt optimization ==="
docker-compose run --rm strategy2 \
    hyperopt \
    --strategy SimpleRSIStrategy \
    --config /freqtrade/user_data/config_person.json \
    --datadir /freqtrade/user_data/data \
    --timerange 20260501- \
    --hyperopt-loss SharpeHyperOptLoss \
    --epochs 200
