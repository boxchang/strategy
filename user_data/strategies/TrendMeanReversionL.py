import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, CategoricalParameter
from freqtrade.persistence import Trade

class TrendMeanReversionL(IStrategy):
    """
    雙向平行策略 (Trend + Mean Reversion) - 槓桿 + Hyperopt 版
    - 兩套邏輯完全平行獨立，使用 enter_tag 進行追蹤與分類
    - 結合 custom_stoploss 實現基於 enter_tag 與持倉時間的動態風控
    - 新增 leverage() 回調，槓桿倍數可由 Hyperopt 優化 (leverage_level)

    注意 (合約/槓桿交易):
    - config 需設定 "trading_mode": "futures", "margin_mode": "isolated"
    - 啟用槓桿後，profit / stoploss / minimal_roi 皆以「含槓桿的收益率」計算。
      例如 5 倍槓桿下，stoploss -0.10 約等於標的價格反向波動 2% 就會觸發。
    """
    timeframe = '15m'
    can_short = True  # 支援合約雙向交易

    # EMA 最長週期 300，需要足夠的啟動 K 線
    startup_candle_count = 320

    # 必須啟用動態止損
    use_custom_stoploss = True
    # 全局硬止損上限 (含槓桿)。實際的預設止損由 custom_stoploss 的 sl_base 決定，
    # 這裡需設成不小於 sl_base 搜尋範圍的最大值 (0.15)，否則優化出的止損會被截斷
    stoploss = -0.20

    minimal_roi = {
        "0": 0.05,
        "30": 0.03,
        "60": 0.01
    }

    # ==================== Hyperopt 可優化參數 ====================
    # 槓桿倍數 (實際使用時會被交易所的 max_leverage 限制)
    leverage_level = IntParameter(1, 10, default=1, space='buy', optimize=False)

    # 策略 1: 趨勢突破 (Trend Breakout) 參數
    # EMA 週期需與 populate_indicators 預先計算的週期一致 (100~300，間隔 20)
    trend_ema_period = CategoricalParameter(
        list(range(100, 301, 20)), default=220, space='buy', optimize=True
    )
    trend_adx_min = IntParameter(15, 35, default=32, space='buy', optimize=True)
    trend_rsi_buy = IntParameter(50, 65, default=51, space='buy', optimize=True)
    trend_rsi_sell = IntParameter(35, 50, default=40, space='buy', optimize=True)

    # 策略 2: 均值回歸 (Mean Reversion) 參數
    mr_rsi_buy = IntParameter(15, 35, default=30, space='buy', optimize=True)
    mr_stoch_buy = IntParameter(10, 30, default=19, space='buy', optimize=True)
    mr_rsi_sell = IntParameter(65, 85, default=85, space='buy', optimize=True)
    mr_stoch_sell = IntParameter(70, 90, default=88, space='buy', optimize=True)

    # 出場參數
    exit_rsi_long = IntParameter(65, 85, default=80, space='sell', optimize=True)
    exit_rsi_short = IntParameter(15, 35, default=31, space='sell', optimize=True)

    # ---------- 止損 / 鎖利門檻 (以下皆為含槓桿的收益率) ----------
    # 預設止損 (回傳 -sl_base)
    sl_base = DecimalParameter(0.05, 0.15, default=0.07, decimals=2, space='sell', optimize=True)

    # 高檔鎖利: 收益超過 lock_high_profit 時，止損拉到 +lock_high_sl
    lock_high_profit = DecimalParameter(0.08, 0.20, default=0.08, decimals=2, space='sell', optimize=True)
    lock_high_sl = DecimalParameter(0.005, 0.05, default=0.011, decimals=3, space='sell', optimize=True)

    # 中檔保本: 收益超過 lock_mid_profit 時，止損拉到 +lock_mid_sl (預設 0 = 成本價)
    lock_mid_profit = DecimalParameter(0.02, 0.08, default=0.04, decimals=2, space='sell', optimize=True)
    lock_mid_sl = DecimalParameter(0.0, 0.02, default=0.007, decimals=3, space='sell', optimize=True)

    # 均值回歸時間止損: 持倉超過 mr_hold_hours 小時且收益低於 mr_min_profit，止損收緊到 -mr_tight_sl
    mr_hold_hours = IntParameter(2, 12, default=10, space='sell', optimize=True)
    mr_min_profit = DecimalParameter(0.005, 0.04, default=0.038, decimals=3, space='sell', optimize=True)
    mr_tight_sl = DecimalParameter(0.01, 0.05, default=0.033, decimals=3, space='sell', optimize=True)

    # 通用時間止損: 持倉超過 gen_hold_hours 小時且仍虧損，止損收緊到 -gen_tight_sl
    gen_hold_hours = IntParameter(6, 24, default=15, space='sell', optimize=True)
    gen_tight_sl = DecimalParameter(0.01, 0.05, default=0.035, decimals=3, space='sell', optimize=True)
    # ============================================================

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: Optional[str], side: str, **kwargs) -> float:
        """回傳槓桿倍數，並限制不超過交易所該交易對的最大槓桿"""
        return float(min(self.leverage_level.value, max_leverage))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1. 趨勢指標
        for val in range(100, 301, 20):
            dataframe[f'ema_{val}'] = ta.EMA(dataframe, timeperiod=val)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # 2. 均值回歸指標 (布林帶 + STOCHF)
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lowerband'] = bollinger['lowerband']
        dataframe['bb_upperband'] = bollinger['upperband']

        stoch_fast = ta.STOCHF(dataframe, 5, 3, 0, 3, 0)
        dataframe['fastk'] = stoch_fast['fastk']

        # 3. 通用指標 (RSI & Volume)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['volume_mean'] = dataframe['volume'].rolling(20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_col = f'ema_{self.trend_ema_period.value}'
        if ema_col not in dataframe.columns:
            ema_col = 'ema_200'

        vol_filter = dataframe['volume'] > dataframe['volume_mean'] * 0.2

        # ==================== 1. 做多平行邏輯 (Long) ====================
        cond_trend_long = (
            (dataframe['close'] > dataframe[ema_col]) &
            (dataframe['adx'] > self.trend_adx_min.value) &
            (dataframe['rsi'] > self.trend_rsi_buy.value) &
            vol_filter
        )

        cond_mr_long = (
            (dataframe['close'] < dataframe['bb_lowerband']) &
            (dataframe['rsi'] < self.mr_rsi_buy.value) &
            (dataframe['fastk'] < self.mr_stoch_buy.value) &
            vol_filter
        )

        dataframe.loc[cond_trend_long, 'enter_long'] = 1
        dataframe.loc[cond_trend_long, 'enter_tag'] = 'trend_long'

        dataframe.loc[cond_mr_long, 'enter_long'] = 1
        dataframe.loc[cond_mr_long, 'enter_tag'] = 'mr_long'

        # ==================== 2. 做空平行邏輯 (Short) ====================
        cond_trend_short = (
            (dataframe['close'] < dataframe[ema_col]) &
            (dataframe['adx'] > self.trend_adx_min.value) &
            (dataframe['rsi'] < self.trend_rsi_sell.value) &
            vol_filter
        )

        cond_mr_short = (
            (dataframe['close'] > dataframe['bb_upperband']) &
            (dataframe['rsi'] > self.mr_rsi_sell.value) &
            (dataframe['fastk'] > self.mr_stoch_sell.value) &
            vol_filter
        )

        dataframe.loc[cond_trend_short, 'enter_short'] = 1
        dataframe.loc[cond_trend_short, 'enter_tag'] = 'trend_short'

        dataframe.loc[cond_mr_short, 'enter_short'] = 1
        dataframe.loc[cond_mr_short, 'enter_tag'] = 'mr_short'

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['rsi'] > self.exit_rsi_long.value),
            'exit_long'] = 1

        dataframe.loc[
            (dataframe['rsi'] < self.exit_rsi_short.value),
            'exit_short'] = 1

        return dataframe

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime,
                current_rate: float, current_profit: float, **kwargs) -> str | bool:
        """
        基於持倉時間與利潤狀況的主動出場函數（Custom Exit）
        只要觸發條件，就會回傳一個 Exit Tag 字串，直接將訂單強制平倉。
        """
        # 計算當前持倉時間（以秒為單位，再轉為小時）
        trade_dur_seconds = (current_time - trade.open_date_utc).total_seconds()
        trade_dur_hours = trade_dur_seconds / 3600.0

        # ---------------- 1. 通用時間強制平倉 (Time-based Force Exit) ----------------
        # 如果持倉時間超過設定的小時數（例如 self.gen_hold_hours），且處於虧損狀態
        if trade_dur_hours > self.gen_hold_hours.value and current_profit < 0:
            return f"time_stop_loss_{self.gen_hold_hours.value}h"

        # ---------------- 2. 針對特定標籤（例如網格/抄底單）的額外時間止損 ----------------
        tag = trade.enter_tag
        if tag in ['mr_long', 'mr_short']:
            if (trade_dur_seconds > self.mr_hold_hours.value * 3600
                    and current_profit < self.mr_min_profit.value):
                return -self.mr_tight_sl.value

        # 如果沒有達到任何強制出場條件，回傳 False（繼續持有，交由其他出場機制判定）
        return False

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        基於 enter_tag、當前未實現利潤與持倉時間的動態止損邏輯
        注意：current_profit 與回傳值皆為「含槓桿」的比例
        """

        # ---------------- 1. 利潤鎖定 / 保本機制 (Profit Protection) ----------------
        # 高檔鎖利：收益超過 lock_high_profit，止損拉到成本價以上 (+lock_high_sl)
        if current_profit > self.lock_high_profit.value:
            return self.lock_high_sl.value

        # 中檔保本：收益超過 lock_mid_profit，止損拉到 +lock_mid_sl (預設成本價)
        if current_profit > self.lock_mid_profit.value:
            return self.lock_mid_sl.value

        # ---------------- 4. 預設止損 ----------------
        return -self.sl_base.value