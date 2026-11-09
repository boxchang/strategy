# AGENTS.md — Freqtrade AI Agent 規範

## 任務開始時必做

1. 閱讀 `docs/PROJECT_CONFIG.md`，確認實際 Freqtrade 版本、Python 版本、交易模式、交易所與資料路徑。
2. 策略任務閱讀 `docs/STRATEGY_DEVELOPMENT.md`。
3. 任何可能影響交易風險的修改閱讀 `docs/RISK_MANAGEMENT.md`。
4. Python / Freqtrade 程式修改閱讀 `docs/CODING_STANDARDS.md`。
5. 回測或參數優化閱讀 `docs/BACKTEST_AND_SCORING.md`。
6. 版本或發布任務閱讀 `docs/VERSION_CONTROL.md` 與 `templates/RELEASE_CHECKLIST.md`。

## 工作安全邊界

- 預設只讀取、分析與修改使用者指定的檔案。
- 預設不連接實盤、不下單、不改交易所設定、不啟動 Live Bot。
- 不讀取或輸出 secrets；如需檢查設定，先遮蔽敏感欄位。
- 不修改其他 Bot、容器、資料庫或設定檔，除非使用者明確指定。
- 不刪除回測資料、交易紀錄、資料庫或 Git 歷史。
- 不將使用者手動交易與 Bot 管理的 Trade 混為一談。

## 工程要求

- 先理解既有架構，再提出最小必要修改。
- 不可憑印象假設 Freqtrade API；以安裝版本與官方文件/原始碼核對。
- 策略需明確處理 Long / Short、進場、出場、停損、槓桿、stake、最大持倉數及訊號衝突。
- 防止 lookahead bias、資料洩漏、未完成 K 線誤用與過度擬合。
- 任何新增指標必須說明用途、參數、資料來源及計算成本。
- 所有績效比較須使用相同資料區間、交易對、費率、滑價假設與回測設定。

## 交付內容

每次完成任務需提供：
- 變更檔案清單
- 原始問題與修改目的
- 策略規則及交易風險變化
- 測試命令與真實結果
- 回測區間、設定、績效指標（若有）
- 未驗證事項與風險
- 建議的 Git commit message（但不自行 commit）

## 衝突處理

若使用者需求與風控文件衝突，先指出衝突及可能後果，等待使用者確認。不可默默放寬風控。
