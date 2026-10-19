import numpy as np
import pandas as pd
from datetime import datetime
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from freqtrade.persistence import Trade

class TrendMeanReversionUS(IStrategy):
    """
    雙向平行策略 (Trend + Mean Reversion)
    - 兩套邏輯完全平行獨立，使用 enter_tag 進行追蹤與分類
    - 結合 custom_stoploss 實現基於 enter_tag 與持倉時間的動態風控
    - 進場過濾:
        * 有量才進場 (成交量 > 20 根均量 * 倍數，且 > 0)
        * 只在美股交易時段進場 (以越南時間判斷，需依美國夏令/冬令手動切換 us_summer_time)
    """
    timeframe = '15m'
    can_short = True  # 支援合約雙向交易

    # 必須啟用動態止損
    use_custom_stoploss = True
    stoploss = -0.10  # 全局預設硬止損 (10%)

    minimal_roi = {
        "0": 0.05,
        "30": 0.03,
        "60": 0.01
    }

    # EMA 最長週期 300，需要足夠的啟動 K 線
    startup_candle_count = 320

    # 美股交易時段 (以越南時間 UTC+7 判斷，越南沒有夏令時間)
    # 美國夏令時間 (EDT, 3月第二個週日 ~ 11月第一個週日): 越南時間 20:30 ~ 次日 03:00
    # 美國冬令時間 (EST, 11月第一個週日 ~ 3月第二個週日): 越南時間 21:30 ~ 次日 04:00
    # 美國夏令/冬令切換時請手動修改此開關 (2026-11-01 切換為冬令，2027-03-14 切回夏令)
    us_summer_time = True

    # ==================== Hyperopt 可優化參數 ====================
    # 量能過濾: 成交量需大於 20 根均量的幾倍
    vol_mult = DecimalParameter(0.5, 2.0, default=1.0, decimals=1, space='buy', optimize=True)

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

        # 4. 美股交易時段標記 (UTC -> 越南時間 Asia/Ho_Chi_Minh)
        #    時段跨越午夜，因此「週一至週五」要以美股場次的起始日為準：
        #    晚上開盤段看當天星期，凌晨收盤段看前一天星期。
        if self.us_summer_time:
            start_minute, end_minute = 20 * 60 + 30, 3 * 60    # 20:30 ~ 03:00
        else:
            start_minute, end_minute = 21 * 60 + 30, 4 * 60    # 21:30 ~ 04:00

        vn = dataframe['date'].dt.tz_convert('Asia/Ho_Chi_Minh')
        minute_of_day = vn.dt.hour * 60 + vn.dt.minute
        evening = minute_of_day >= start_minute
        early_morning = minute_of_day < end_minute

        session_weekday = vn.dt.dayofweek.where(evening, (vn - pd.Timedelta(days=1)).dt.dayofweek)
        dataframe['us_session'] = (evening | early_morning) & (session_weekday < 5)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_col = f'ema_{self.trend_ema_period.value}'
        if ema_col not in dataframe.columns:
            ema_col = 'ema_200'

        # 有量才進場 + 僅限美股交易時段
        vol_filter = (
            (dataframe['volume'] > 0) &
            (dataframe['volume'] > dataframe['volume_mean'] * self.vol_mult.value) &
            (dataframe['us_session'])
        )

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