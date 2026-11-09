# Python / Freqtrade 程式碼規範

## 1. 基本要求

- 遵守專案實際 Python 版本，不使用環境不支援的語法或套件。
- 使用明確、可讀的變數名稱；避免過度縮寫。
- 保持單一職責，將複雜條件拆成可測試函式。
- 移除未使用 import、死碼與重複計算。
- 不以大量 try/except 隱藏錯誤。
- 日誌不得輸出 API key、secret、token 或敏感帳戶資料。

## 2. Freqtrade 策略

- 明確定義 `INTERFACE_VERSION`（依目前版本要求）。
- 明確設定 `timeframe`、`startup_candle_count`、`can_short`、`minimal_roi`、`stoploss`。
- 若 ROI 不使用，明確說明設定意圖，不可誤以為空字典就代表所有退出邏輯停用。
- `populate_indicators`、`populate_entry_trend`、`populate_exit_trend` 應避免不必要的逐列 Python loop。
- 正確使用 `enter_long`、`enter_short`、`exit_long`、`exit_short` 與 tag。
- 檢查 signal collision、同時進出場與 `ignore_*_if_entry_signal` 等設定。
- callback 必須核對當前版本簽名、呼叫時機及回傳值語義。
- 不可在未驗證情況下宣稱 PairLocks、order_filled 或交易所停損訂單的行為。

## 3. Lookahead 與資料品質

- 禁止使用 `shift(-n)` 等方式將未來值帶入當前訊號。
- 禁止使用全資料集統計值回填歷史訊號，除非採用只看過去資料的 rolling/expanding 計算。
- informative timeframe 合併須檢查時間對齊。
- 使用 `lookahead-analysis`（若版本支援）及必要的手動檢查。
- 回測前檢查缺 K、重複 K、時區、交易對格式與資料區間。

## 4. 測試要求

依環境能力執行：
- Python compile / syntax check
- Freqtrade strategy list / strategy loading
- `freqtrade test-pairlist`（若相關）
- backtesting
- lookahead-analysis
- recursive-analysis（若適用）
- 單元測試或自訂訊號測試

未執行的測試必須標示「未執行」，不得推測通過。
