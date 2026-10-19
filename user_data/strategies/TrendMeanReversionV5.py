import numpy as np
import pandas as pd
from datetime import datetime
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import IStrategy, IntParameter, merge_informative_pair
from freqtrade.persistence import Trade

class TrendMeanReversionV5(IStrategy):
    """
    雙向平行策略 (Trend + Mean Reversion)
    - 兩套邏輯完全平行獨立，使用 enter_tag 進行追蹤與分類
    - 結合 custom_stoploss 實現基於 enter_tag 與持倉時間的動態風控
    - 新增全局過濾:
        * 1h KD: K > D 才允許做多；K < D 才允許做空
        * MACD:  MACD > 0 才允許做多；MACD < 0 才允許做空
    """
    timeframe = '15m'
    informative_timeframe = '1h'
    can_short = True  # 支援合約雙向交易

    # EMA 最長週期 300，需要足夠的啟動 K 線
    startup_candle_count = 320

    # 必須啟用動態止損
    use_custom_stoploss = True
    stoploss = -0.10  # 全局預設硬止損 (10%)

    minimal_roi = {
        "0": 0.05,
        "30": 0.03,
        "60": 0.01
    }

    # ==================== Hyperopt 可優化參數 ====================
    # 策略 1: 趨勢突破 (Trend Breakout) 參數
    trend_ema_period = IntParameter(100, 300, default=200, space='buy', optimize=True)
    trend_adx_min = IntParameter(15, 35, default=20, space='buy', optimize=True)
    trend_rsi_buy = IntParameter(50, 65, default=55, space='buy', optimize=True)
    trend_rsi_sell = IntParameter(35, 50, default=45, space='buy', optimize=True)

    # 策略 2: 均值回歸 (Mean Reversion) 參數
    mr_rsi_buy = IntParameter(15, 35, default=25, space='buy', optimize=True)
    mr_stoch_buy = IntParameter(10, 30, default=20, space='buy', optimize=True)
    mr_rsi_sell = IntParameter(65, 85, default=75, space='buy', optimize=True)
    mr_stoch_sell = IntParameter(70, 90, default=80, space='buy', optimize=True)

    # 出場參數
    exit_rsi_long = IntParameter(65, 85, default=70, space='sell', optimize=True)
    exit_rsi_short = IntParameter(15, 35, default=30, space='sell', optimize=True)
    # ============================================================

    def informative_pairs(self):
        """宣告需要額外下載的 1h 資料"""
        pairs = self.dp.current_whitelist()
        return [(pair, self.informative_timeframe) for pair in pairs]

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

        # 4. MACD (使用策略主週期 15m)
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe['macd'] = macd['macd']

        # 5. 1h KD (Slow Stochastic: 9, 3, 3)，合併回 15m 資料
        informative = self.dp.get_pair_dataframe(
            pair=metadata['pair'], timeframe=self.informative_timeframe
        )
        stoch = ta.STOCH(
            informative,
            fastk_period=9,
            slowk_period=3, slowk_matype=0,
            slowd_period=3, slowd_matype=0
        )
        informative['slowk'] = stoch['slowk']
        informative['slowd'] = stoch['slowd']

        # merge_informative_pair 會自動避免未來函數 (lookahead)，欄位會加上 _1h 後綴
        dataframe = merge_informative_pair(
            dataframe, informative[['date', 'slowk', 'slowd']],
            self.timeframe, self.informative_timeframe, ffill=True
        )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_col = f'ema_{self.trend_ema_period.value}'
        if ema_col not in dataframe.columns:
            ema_col = 'ema_200'

        vol_filter = dataframe['volume'] > dataframe['volume_mean'] * 0.2

        # ==================== 全局方向過濾 (KD 1h + MACD) ====================
        long_filter = (
            (dataframe['slowk_1h'] > dataframe['slowd_1h']) &
            (dataframe['macd'] > 0)
        )
        short_filter = (
            (dataframe['slowk_1h'] < dataframe['slowd_1h']) &
            (dataframe['macd'] < 0)
        )

        # ==================== 1. 做多平行邏輯 (Long) ====================
        cond_trend_long = (
            (dataframe['close'] > dataframe[ema_col]) &
            (dataframe['adx'] > self.trend_adx_min.value) &
            (dataframe['rsi'] > self.trend_rsi_buy.value) &
            vol_filter &
            long_filter
        )

        cond_mr_long = (
            (dataframe['close'] < dataframe['bb_lowerband']) &
            (dataframe['rsi'] < self.mr_rsi_buy.value) &
            (dataframe['fastk'] < self.mr_stoch_buy.value) &
            vol_filter &
            long_filter
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
            vol_filter &
            short_filter
        )

        cond_mr_short = (
            (dataframe['close'] > dataframe['bb_upperband']) &
            (dataframe['rsi'] > self.mr_rsi_sell.value) &
            (dataframe['fastk'] > self.mr_stoch_sell.value) &
            vol_filter &
            short_filter
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

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        基於 enter_tag、當前未實現利潤與持倉時間的動態止損邏輯
        注意：Freqtrade 要求的返回值是相對開倉價的比例 (例如 -0.02 代表相對於開倉價 -2% 止損)
        """
        tag = trade.enter_tag
        trade_dur_seconds = (current_time - trade.open_date_utc).total_seconds()

        # ---------------- 1. 利潤鎖定 / 保本機制 (Profit Protection) ----------------
        # 收益超過 10%：拉高止損到成本價以上 (+1% 利潤處鎖利保本)
        if current_profit > 0.10:
            return 0.01

        # 收益超過 5%：拉高止損到成本價 (0% 移動保本)
        if current_profit > 0.05:
            return 0.00

        # ---------------- 2. 均值回歸策略的特定時間止損 (Mean Reversion Time-based Exit) ----------------
        # 抄底/摸頂策略若持倉超過 6 小時仍未產生 2% 以上利潤，代表行情停滯，收緊止損至 -2%
        if tag in ['mr_long', 'mr_short']:
            if trade_dur_seconds > 6 * 3600 and current_profit < 0.02:
                return -0.02

        # ---------------- 3. 通用時間止損 (General Time-based Stop) ----------------
        # 持倉超過 12 小時且仍處於虧損狀態，迅速離場 (-2% 止損)
        if trade_dur_seconds > 12 * 3600 and current_profit < 0:
            return -0.02

        # ---------------- 4. 預設止損 ----------------
        return -0.10