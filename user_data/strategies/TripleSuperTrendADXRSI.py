import logging
import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.strategy import IntParameter, IStrategy
from pandas import DataFrame

# 設置日誌
logger = logging.getLogger(__name__)

class TripleSuperTrendADXRSI(IStrategy):
    """
    Triple Supertrend with ADX/RSI Confirmation
    優化版：修正了參數空間錯誤與運算效能問題。
    """
    INTERFACE_VERSION: int = 3

    # 允許做空
    can_short: bool = True

    # ROI 表：定義不同時間點的最低獲利要求
    minimal_roi = {
        "0": 0.1,
        "30": 0.075,
        "60": 0.05,
        "120": 0.025
    }

    # 止損百分比
    stoploss = -0.265

    # 移動止損設定
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.1
    trailing_only_offset_is_reached = False

    # 策略週期與預留 K 線數量
    timeframe = "1h"
    startup_candle_count = 30  # 確保大於所有指標的最大週期

    # ----- 超參數優化區塊 (Hyperopt Parameters) -----
    # 透過 category 指定空間，解決 "Cannot determine parameter space" 錯誤
    
    # Buy Supertrend 參數
    buy_m1 = IntParameter(1, 7, default=4, space="buy")
    buy_m2 = IntParameter(1, 7, default=7, space="buy")
    buy_m3 = IntParameter(1, 7, default=1, space="buy")
    buy_p1 = IntParameter(7, 21, default=8, space="buy")
    buy_p2 = IntParameter(7, 21, default=9, space="buy")
    buy_p3 = IntParameter(7, 21, default=8, space="buy")

    # Sell Supertrend 參數
    sell_m1 = IntParameter(1, 7, default=1, space="sell")
    sell_m2 = IntParameter(1, 7, default=3, space="sell")
    sell_m3 = IntParameter(1, 7, default=6, space="sell")
    sell_p1 = IntParameter(7, 21, default=16, space="sell")
    sell_p2 = IntParameter(7, 21, default=18, space="sell")
    sell_p3 = IntParameter(7, 21, default=18, space="sell")

    # ADX 與 RSI 趨勢過濾參數
    adx_period = IntParameter(10, 30, default=14, space="buy")
    adx_threshold = IntParameter(20, 40, default=25, space="buy")
    
    rsi_period = IntParameter(10, 30, default=14, space="buy")
    rsi_long_threshold = IntParameter(40, 60, default=50, space="buy")
    rsi_short_threshold = IntParameter(40, 60, default=50, space="buy")
    
    rsi_long_exit = IntParameter(70, 80, default=75, space="sell")
    rsi_short_exit = IntParameter(20, 30, default=25, space="sell")

    def supertrend(self, df: DataFrame, multiplier, period) -> DataFrame:
        """
        高效優化後的 Supertrend 計算邏輯 (向量化 + Numpy 遍歷)
        """
        if len(df) < period:
            return DataFrame(index=df.index, data={"STX": ["nan"] * len(df)})

        close = df['close'].to_numpy()
        high = df['high'].to_numpy()
        low = df['low'].to_numpy()
        
        # 使用 TA-Lib 計算 ATR
        atr = ta.ATR(df, timeperiod=int(period)).to_numpy()
        
        hl2 = (high + low) / 2
        basic_ub = hl2 + (multiplier * atr)
        basic_lb = hl2 - (multiplier * atr)
        
        final_ub = np.zeros(len(df))
        final_lb = np.zeros(len(df))
        st = np.zeros(len(df))
        
        # 核心 Supertrend 遞迴邏輯 (使用 Numpy 陣列加速)
        for i in range(1, len(df)):
            # 計算最終上軌
            if basic_ub[i] < final_ub[i-1] or close[i-1] > final_ub[i-1]:
                final_ub[i] = basic_ub[i]
            else:
                final_ub[i] = final_ub[i-1]
                
            # 計算最終下軌
            if basic_lb[i] > final_lb[i-1] or close[i-1] < final_lb[i-1]:
                final_lb[i] = basic_lb[i]
            else:
                final_lb[i] = final_lb[i-1]

            # 決定 Supertrend 線的位置
            if st[i-1] == final_ub[i-1]:
                st[i] = final_ub[i] if close[i] <= final_ub[i] else final_lb[i]
            else:
                st[i] = final_lb[i] if close[i] >= final_lb[i] else final_ub[i]
        
        # 標記趨勢方向
        stx = np.where(st == 0, "nan", np.where(close < st, "down", "up"))
        
        return DataFrame(index=df.index, data={"STX": stx})

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        計算策略所需的所有指標。
        """
        # 計算 ADX (趨勢強度) 與 RSI (動量)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=self.adx_period.value)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)

        # 只計算目前參數值對應的 6 個 Supertrend 指標 (避免迴圈窮舉導致效能崩潰)
        dataframe['st_buy_1'] = self.supertrend(dataframe, self.buy_m1.value, self.buy_p1.value)['STX']
        dataframe['st_buy_2'] = self.supertrend(dataframe, self.buy_m2.value, self.buy_p2.value)['STX']
        dataframe['st_buy_3'] = self.supertrend(dataframe, self.buy_m3.value, self.buy_p3.value)['STX']
        
        dataframe['st_sell_1'] = self.supertrend(dataframe, self.sell_m1.value, self.sell_p1.value)['STX']
        dataframe['st_sell_2'] = self.supertrend(dataframe, self.sell_m2.value, self.sell_p2.value)['STX']
        dataframe['st_sell_3'] = self.supertrend(dataframe, self.sell_m3.value, self.sell_p3.value)['STX']

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        定義進場信號。
        """
        # LONG 做多條件：
        # 1. 三個 Buy Supertrend 皆為 'up'
        # 2. ADX 高於門檻 (表示趨勢強勁)
        # 3. RSI 高於多頭門檻 (確認動能)
        dataframe.loc[
            (
                (dataframe['st_buy_1'] == "up") &
                (dataframe['st_buy_2'] == "up") &
                (dataframe['st_buy_3'] == "up") &
                (dataframe['adx'] > self.adx_threshold.value) &
                (dataframe['rsi'] > self.rsi_long_threshold.value) &
                (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # SHORT 做空條件：
        # 1. 三個 Sell Supertrend 皆為 'down'
        # 2. ADX 高於門檻
        # 3. RSI 低於空頭門檻
        dataframe.loc[
            (
                (dataframe['st_sell_1'] == "down") &
                (dataframe['st_sell_2'] == "down") &
                (dataframe['st_sell_3'] == "down") &
                (dataframe['adx'] > self.adx_threshold.value) &
                (dataframe['rsi'] < self.rsi_short_threshold.value) &
                (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        定義出場信號。
        """
        # LONG 出場條件：
        # 1. 次要 Sell Supertrend 轉為 'down' (趨勢反轉)
        # 2. 或 RSI 進入超買區間
        dataframe.loc[
            (
                (dataframe['st_sell_2'] == "down") |
                (dataframe['rsi'] > self.rsi_long_exit.value)
            ),
            "exit_long",
        ] = 1

        # SHORT 出場條件：
        # 1. 次要 Buy Supertrend 轉為 'up'
        # 2. 或 RSI 進入超跌區間
        dataframe.loc[
            (
                (dataframe['st_buy_2'] == "up") |
                (dataframe['rsi'] < self.rsi_short_exit.value)
            ),
            "exit_short",
        ] = 1

        return dataframe