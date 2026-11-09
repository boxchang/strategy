# 專案環境設定（請依實際環境更新）

> AI 不可把本文件中的預設值當成已確認事實。標記「待填」的項目必須先確認。

## 執行環境

| 項目 | 設定 |
|---|---|
| Freqtrade 版本 | freqtrade 2026.4 |
| Python 版本 | Python 3.14.3 |
| 作業環境 |  Docker |
| Docker Image Tag | freqtradeorg/freqtrade:stable |
| Exchange | Binance |
| 市場 | USDT-M Perpetual Futures |
| Trading mode | futures |
| Margin mode | isolated |
| Position mode | One-way，須符合 Freqtrade 與交易所要求 |
| 預設 timeframe | 15m（可按策略覆寫） |
| 時區 | Asia/Ho_Chi_Minh |
| Dry-run 預設 | true |
| Live trading | 預設禁止，需明確授權 |

## 資金與交易限制（需按每個 Bot 獨立設定）

| 項目 | 基準 | 狀態 |
|---|---:|---|
| 每筆 stake | 待填 | 未確認 |
| 最大同時持倉 | 待填 | 未確認 |
| 預設槓桿 | 1x | 建議起點，非已驗證設定 |
| DCA / Position adjustment | 預設關閉 | 強制規則 |
| Long / Short | 依策略需求 | 必須明確聲明 |

## 專案隔離

- 每個 Bot 必須使用獨立 container/service、config、資料庫、log 與 Telegram token。
- 不可覆蓋其他 Bot 的資料庫或策略檔案。
- 若共用同一交易所帳戶，需考慮共享保證金、同幣種倉位、手動交易與 Bot 記錄不同步的風險。
- 不得將 API key、secret、Telegram token 放入 Git。
- API Key 權限遵循最小權限原則；除非必要，不開啟提領權限。

## AI 必須先確認

- 目前安裝的 Freqtrade 版本與 Python 版本。
- 策略使用的市場類型及交易對格式。
- 交易所 position mode 與 margin mode。
- 手續費、資金費率、滑價及資料來源。
- 回測資料的起訖時間、K 線完整性與交易對清單。

若資訊未知，明確標示未知，不得自行編造。
