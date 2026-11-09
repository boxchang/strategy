# Freqtrade AI Agent 專案規範

本套文件用來約束 AI 在此專案中的策略設計、程式修改、風險檢查、回測評估與版本管理流程。

## 快速開始

1. 將本資料夾內容解壓到 Freqtrade 專案根目錄。
2. 保留 `CLAUDE.md`、`AGENTS.md` 與 `docs/` 的相對路徑。
3. 依照實際環境修改 `docs/PROJECT_CONFIG.md`。
4. 每次開發策略前，要求 AI 先閱讀入口規範；Claude Code 會讀取 `CLAUDE.md`，支援 Agent 指示檔的工具可讀取 `AGENTS.md`。
5. 新策略先 Dry Run，再做回測、穩健性測試，最後才考慮小額實盤。

## 文件地圖

| 文件 | 用途 |
|---|---|
| `CLAUDE.md` | Claude Code 專案入口規則 |
| `AGENTS.md` | 通用 Agent 專案入口規則 |
| `docs/PROJECT_CONFIG.md` | 專案環境與不可假設的設定 |
| `docs/STRATEGY_DEVELOPMENT.md` | 策略研究、設計與實作 SOP |
| `docs/RISK_MANAGEMENT.md` | 資金、槓桿、倉位與交易風險 |
| `docs/BACKTEST_AND_SCORING.md` | 回測流程、評分與淘汰門檻 |
| `docs/CODING_STANDARDS.md` | Python / Freqtrade 程式碼規範 |
| `docs/VERSION_CONTROL.md` | Git、策略版本與實驗紀錄 |
| `docs/AI_WORKFLOW.md` | AI 任務流程、交付格式與提示詞 |
| `templates/STRATEGY_SPEC.md` | 新策略需求規格模板 |
| `templates/EXPERIMENT_LOG.md` | 回測實驗紀錄模板 |
| `templates/RELEASE_CHECKLIST.md` | 發布前檢查清單 |

## 重要原則

- 本文件是開發治理規範，不代表任何策略已經盈利或通過測試。
- AI 不得虛構回測結果、測試結果、指標數值或 Git commit。
- 未取得使用者明確授權，不得修改交易所實盤設定、API 金鑰、資金、部署服務或既有策略。
- 任何策略都必須先證明程式正確，再檢查回測可信度，最後才討論績效。
