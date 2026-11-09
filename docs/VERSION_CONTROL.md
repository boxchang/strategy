# Git 與策略版本管理規範

## 1. 版本原則

- 每個策略使用獨立且穩定的 Python class name。
- 每次有交易邏輯變更，更新策略內部版本字串或版本常數，並在實驗紀錄中記錄。
- Git commit 是程式碼快照；策略版本是策略邏輯識別，兩者都要記錄。
- 回測結果必須能追溯到 commit、策略檔案、設定與資料區間。
- 不得以檔名或口頭描述代替版本識別。

## 2. 分支建議

- `main`：已審查、可重現的穩定版本。
- `dev`：整合開發。
- `feature/<strategy>-<change>`：單一功能或策略實驗。
- `experiment/<id>`：參數或假設實驗。

小型個人專案可簡化分支，但仍須保留可回復的 commit。

## 3. Commit 規範

建議格式：

`<type>(<strategy>): <簡短描述>`

type：
- `feat`：新增策略或功能
- `fix`：修正錯誤
- `refactor`：不改變策略行為的重構
- `test`：新增測試
- `docs`：文件變更
- `experiment`：研究實驗設定或結果

範例：
- `feat(rsi-reversion): add short entry condition`
- `fix(ema-trend): prevent duplicate entry signals`
- `experiment(rsi-reversion): test exit threshold 50`

## 4. 每次策略版本更新需記錄

- Strategy class
- Strategy version
- Git commit hash
- 變更假設與原因
- 進出場規則差異
- 風控差異
- 回測設定與資料期間
- 回測結果
- 是否通過 OOS / Dry Run
- 已知限制

## 5. 回測產物管理

- 回測報告、圖表與實驗紀錄應有唯一 ID。
- 不要把 API secrets、帳戶資料、私人交易紀錄提交到公開 repo。
- 大型資料檔、資料庫與原始 K 線通常不應直接提交 Git；依專案需求使用忽略規則或獨立儲存。
- 不得覆寫唯一一份回測結果；每次實驗另存新檔。
- 變更策略後，舊回測結果不可視為新版本的驗證結果。

## 6. 發布流程

研究分支 → 程式檢查 → 回測 → OOS / 穩健性測試 → Dry Run → 人工審查 → 明確授權後部署。
AI 不得自行跳過階段或自行 push / deploy / 啟動實盤。
