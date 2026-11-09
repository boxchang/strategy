# 回測流程與策略評分規範

## 1. 回測目的

回測用來檢查策略假設、程式行為與歷史風險，不代表未來績效保證。不得以單一最佳回測區間或最佳參數宣稱策略有效。

## 2. 固定測試條件

每次比較必須記錄：
- Freqtrade 版本、策略版本與 Git commit
- 交易對、timeframe、回測起訖時間
- 資料來源及資料下載日期
- stake、最大持倉、槓桿與 margin mode
- maker/taker 手續費、滑價假設、funding fee 是否納入
- order type、exit 設定、protections
- 回測命令及完整設定檔版本（移除 secrets）

不同策略比較時，除非研究目的就是測試該因素，否則應固定上述條件。

## 3. 測試順序

1. 語法與策略載入檢查。
2. 資料品質檢查。
3. 基準回測（不先做大規模參數優化）。
4. Lookahead / recursive 檢查（適用時）。
5. Long、Short 分開分析。
6. 分市場狀態、分年度或分區間分析。
7. Out-of-sample / walk-forward 驗證。
8. 參數敏感度測試。
9. Dry Run 前檢查。

## 4. 評分框架（100 分）

| 項目 | 權重 | 評估重點 |
|---|---:|---|
| 淨報酬與報酬穩定性 | 15 | 扣除成本後的報酬；不同區間是否一致 |
| 最大回撤與回撤時間 | 20 | 回撤幅度、恢復時間、尾部風險 |
| Profit Factor | 10 | 總獲利 / 總虧損；避免由少數交易主導 |
| 交易樣本與統計可信度 | 10 | 交易數量、分布、信賴程度 |
| Out-of-sample / Walk-forward | 15 | 未參與調參資料的表現 |
| Long / Short 平衡 | 5 | 兩方向是否各自有合理表現 |
| 市場狀態穩健性 | 10 | 趨勢、盤整、高波動等環境 |
| 交易成本與執行敏感度 | 10 | 手續費、滑價、funding fee 壓力測試 |
| 程式正確性與可維護性 | 5 | 無已知 lookahead、錯誤與不透明邏輯 |
| **總分** | **100** | |

### 評分解釋

- 85–100：可進入更嚴格的 Dry Run 驗證；不是實盤保證。
- 70–84：需針對弱項改善，再做獨立驗證。
- 55–69：研究原型，不建議部署。
- 低於 55：淘汰或重新提出交易假設。

## 5. 硬性淘汰 / 暫停條件

以下任一情況成立，不得因總分高而通過：
- 有未解決的 lookahead bias 或資料洩漏。
- Out-of-sample 明顯失效或績效主要來自單一交易對/單一時段。
- 最大回撤超出使用者預先核准的風險上限。
- 扣除合理交易成本後期望值轉負。
- 交易數太少，無法支持統計結論。
- 存在無停損、無限加倉、倉位計算錯誤或重大程式錯誤。
- 回測設定與預期實盤設定不一致且未解釋。

## 6. 參數優化

- 先定義合理參數範圍及經濟/市場理由。
- 不可只報告最佳參數；需提供鄰近參數表現。
- 優先尋找穩定平台區，不追逐尖峰最佳值。
- 限制優化自由度，避免同時調整過多參數。
- 保留未參與優化的資料區間。
- 不得反覆使用同一 OOS 區間調參後仍稱其為 OOS。

## 7. 每次回測報告必列

- Net profit、absolute profit、最大回撤與回撤時間
- Profit factor、勝率、平均盈虧、交易數
- 平均持倉時間、最大連敗、Long/Short 分項
- 交易對分項及月份/年度分項
- 費用、funding、滑價假設
- OOS / walk-forward 結果
- 失敗案例與代表性交易
- 版本、commit、命令及資料區間
- 評分與硬性淘汰檢查結果

若 Freqtrade 版本或輸出格式沒有提供某指標，標示未取得，不可自行捏造。

## 8. Docker Compose 回測命令範例

在遠端主機的 Docker Compose 專案目錄執行。先設定服務、config 檔名、策略 class、週期、資料路徑與回測區間；之後下載資料及回測都會使用相同的 config、週期和資料路徑：

```bash
SERVICE="strategy04"
CONFIG_NAME="config_person.json"
STRATEGY_NAME="RSI20LongStrategy"
TIMEFRAME="15m"
TIMERANGE="20260101-"
DATADIR="/freqtrade/user_data/data/binance"
CONFIG_PATH="/freqtrade/user_data/${CONFIG_NAME}"

# 如需下載或更新歷史資料，再執行此步驟。
docker-compose run --rm "$SERVICE" download-data \
  --trading-mode futures \
  --timeframes "$TIMEFRAME" \
  --config "$CONFIG_PATH" \
  --datadir "$DATADIR" \
  --timerange "$TIMERANGE"

# 使用以上設定執行基準回測；不會啟動交易服務或 Hyperopt。
docker-compose run --rm "$SERVICE" backtesting \
  --strategy "$STRATEGY_NAME" \
  --config "$CONFIG_PATH" \
  --timeframe "$TIMEFRAME" \
  --datadir "$DATADIR" \
  --timerange "$TIMERANGE"
```

- `CONFIG_NAME` 是 `/freqtrade/user_data/` 底下的 config 檔名；若檔案在其他位置，請相應修改 `CONFIG_PATH`。
- `STRATEGY_NAME` 必須與策略 Python 檔內的 class 名稱一致，不是檔名。
- `SERVICE` 必須是 Compose 檔中提供的服務名稱，不一定等同 container name。
- 僅在需要下載或更新資料時執行 `download-data`；回測前確認該市場/交易對的資料已存在。
- `--timerange` 只指定希望測試的區間；實際回測起點仍受本機資料最早可用日期限制。執行後核對輸出中的 `Backtested ... -> ...`，不可只依命令列區間宣稱完整覆蓋。
- 若出現 orphan containers 警告，先確認回測本身是否完成；不要為消除警告而加上 `--remove-orphans`，以免影響同一 Compose 專案中其他 Bot 的容器。
- 使用前檢查 config 的交易模式、交易對、費用及風險設定。回測命令不會將 config 的 dry-run 設定變成實盤授權；不可用此流程啟動 live trading。

### 已驗證的命令範例

以下命令已在遠端 Docker Compose 環境成功執行：

```bash
docker-compose run --rm strategy04 backtesting \
  --strategy RSI20LongStrategy \
  --config /freqtrade/user_data/config_person.json \
  --timeframe 15m \
  --datadir /freqtrade/user_data/data/binance \
  --timerange 20260101-
```

這次輸出實際回測區間為 `2026-05-01 15:45` 至 `2026-11-09 09:15`，早於可用資料的部分並未被測試。報告顯示 15 筆已結束交易、淨利 `-0.589 USDT`（`-0.06%`）、Profit Factor `0.97`、勝率 `60%`、最大回撤 `1.18%`；另有 1 筆未結束交易列於 Left Open Trades Report。這只是歷史回測結果，不代表未來績效。
