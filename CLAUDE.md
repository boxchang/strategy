# Claude Code 專案指示

你正在協助開發 Freqtrade 策略與研究工具。請把本文件視為本專案的強制入口規範。

## 開始任何工作前

必須先讀取：
- `AGENTS.md`
- `docs/PROJECT_CONFIG.md`
- `docs/STRATEGY_DEVELOPMENT.md`
- `docs/RISK_MANAGEMENT.md`
- `docs/CODING_STANDARDS.md`
- `docs/BACKTEST_AND_SCORING.md`
- `docs/VERSION_CONTROL.md`
- `docs/AI_WORKFLOW.md`

若任務只涉及其中一個領域，仍須讀取該領域文件；若要寫入或修改策略，以上文件全部必讀。

## 不可違反的規則

1. 不可擅自修改或覆蓋既有策略；先檢查 Git 狀態、檔案內容與使用者指定範圍。
2. 不可把回測最佳參數直接視為未來最佳參數。
3. 不可只以總報酬率作為策略好壞判斷。
4. 必須檢查 Lookahead Bias、資料洩漏、未完成 K 線、重複訊號、倉位與槓桿風險。
5. 未經明確授權，不得啟動 Live Trading、下單、取消訂單、修改交易所帳戶或部署服務。
6. 不可輸出、提交或記錄 API Key、Secret、Telegram Token、密碼及個人識別資料。
7. 不可宣稱已執行未實際執行的測試或回測。
8. Freqtrade callback、設定欄位與交易所支援性必須以專案安裝版本為準；不確定時先查版本文件或原始碼。

## 修改前

- 檢查 `git status`、目前 branch、最近相關 commit。
- 找出目標策略及其被引用的設定、PairList、資料與部署服務。
- 說明預計修改的檔案及風險；使用者未要求大範圍重構時，採最小修改。
- 保留原策略行為，除非使用者明確要求變更。

## 修改後

- 執行可用的語法檢查、策略載入檢查與必要測試。
- 提供實際執行過的命令及結果；未執行者標示「未執行」。
- 列出變更摘要、假設、已知限制、回測命令與下一步。
- 不得自行 commit、push、部署或啟動實盤，除非使用者明確授權。

## 通用 Agent 規則

`AGENTS.md` 與 `docs/` 是跨工具共用規範。若不同文件互相矛盾，採用更保守的風險要求，並先向使用者指出衝突，不可自行挑選較寬鬆的規則。
