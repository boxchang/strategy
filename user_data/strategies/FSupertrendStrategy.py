import logging
import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.strategy import IntParameter, IStrategy
from pandas import DataFrame

class FSupertrendStrategy(IStrategy):
    INTERFACE_VERSION: int = 3
    can_short: bool = True

    # ROI table:
    minimal_roi = {"0": 0.1, "30": 0.075, "60": 0.05, "120": 0.025}
    stoploss = -0.265

    # Trailing stop:
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.1
    trailing_only_offset_is_reached = False

    timeframe = "1h"
    startup_candle_count = 18

    # Hyperopt parameters
    buy_m1 = IntParameter(1, 7, default=4)
    buy_m2 = IntParameter(1, 7, default=7)
    buy_m3 = IntParameter(1, 7, default=1)
    buy_p1 = IntParameter(7, 21, default=8)
    buy_p2 = IntParameter(7, 21, default=9)
    buy_p3 = IntParameter(7, 21, default=8)

    sell_m1 = IntParameter(1, 7, default=1)
    sell_m2 = IntParameter(1, 7, default=3)
    sell_m3 = IntParameter(1, 7, default=6)
    sell_p1 = IntParameter(7, 21, default=16)
    sell_p2 = IntParameter(7, 21, default=18)
    sell_p3 = IntParameter(7, 21, default=18)

    def supertrend(self, df: DataFrame, multiplier, period) -> DataFrame:
        """
        優化後的 Supertrend 計算 (向量化處理提高效能)
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # 計算 ATR
        atr = ta.ATR(df, timeperiod=period)
        
        # 基本上下軌
        hl2 = (high + low) / 2
        basic_ub = hl2 + (multiplier * atr)
        basic_lb = hl2 - (multiplier * atr)
        
        # 初始化最終上下軌
        final_ub = np.array([0.0] * len(df))
        final_lb = np.array([0.0] * len(df))
        st = np.array([0.0] * len(df))
        
        # 由於 Supertrend 具有遞歸性質 (依賴前一根 K 線)，這裡使用 numba 或優化循環
        # 為了保持兼容性，使用優化後的 numpy 遍歷
        for i in range(1, len(df)):
            # Final Upperband
            if basic_ub[i] < final_ub[i-1] or close[i-1] > final_ub[i-1]:
                final_ub[i] = basic_ub[i]
            else:
                final_ub[i] = final_ub[i-1]
                
            # Final Lowerband
            if basic_lb[i] > final_lb[i-1] or close[i-1] < final_lb[i-1]:
                final_lb[i] = basic_lb[i]
            else:
                final_lb[i] = final_lb[i-1]
        
        # 計算 Trend
        current_trend = np.array([0] * len(df)) # 1 for up, -1 for down
        for i in range(1, len(df)):
            if st[i-1] == final_ub[i-1]:
                st[i] = final_ub[i] if close[i] <= final_ub[i] else final_lb[i]
            else:
                st[i] = final_lb[i] if close[i] >= final_lb[i] else final_ub[i]
        
        # 標記方向
        stx = np.where(st > 0, np.where(close < st, "down", "up"), "nan")
        
        return DataFrame(index=df.index, data={"STX": stx})

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 關鍵修正：只計算目前參數需要的指標，而不是遍歷整個 range
        # 這樣在實盤和單次策略運行時會快非常多
        
        # 如果是在 Hyperopt 模式，Freqtrade 會自動處理參數變化
        dataframe['st_buy_1'] = self.supertrend(dataframe, self.buy_m1.value, self.buy_p1.value)['STX']
        dataframe['st_buy_2'] = self.supertrend(dataframe, self.buy_m2.value, self.buy_p2.value)['STX']
        dataframe['st_buy_3'] = self.supertrend(dataframe, self.buy_m3.value, self.buy_p3.value)['STX']
        
        dataframe['st_sell_1'] = self.supertrend(dataframe, self.sell_m1.value, self.sell_p1.value)['STX']
        dataframe['st_sell_2'] = self.supertrend(dataframe, self.sell_m2.value, self.sell_p2.value)['STX']
        dataframe['st_sell_3'] = self.supertrend(dataframe, self.sell_m3.value, self.sell_p3.value)['STX']

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe['st_buy_1'] == "up") &
                (dataframe['st_buy_2'] == "up") &
                (dataframe['st_buy_3'] == "up") &
                (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        dataframe.loc[
            (
                (dataframe['st_sell_1'] == "down") &
                (dataframe['st_sell_2'] == "down") &
                (dataframe['st_sell_3'] == "down") &
                (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 以第二個 Supertrend 作為出場訊號反轉的基準
        dataframe.loc[
            (dataframe['st_sell_2'] == "down"),
            "exit_long",
        ] = 1

        dataframe.loc[
            (dataframe['st_buy_2'] == "up"),
            "exit_short",
        ] = 1

        return dataframe