from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import numpy as np

class IchimokuStrategy(IStrategy):
    timeframe = '5m'
    minimal_roi = { "0": 0.1 }          # 或 minimal_roi = {"0": 0.1}
    stoploss = -0.05
    trailing_stop = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Tenkan-sen (9)
        nine_high = dataframe['high'].rolling(window=9).max()
        nine_low = dataframe['low'].rolling(window=9).min()
        dataframe['tenkan_sen'] = (nine_high + nine_low) / 2

        # Kijun-sen (26)
        period26_high = dataframe['high'].rolling(window=26).max()
        period26_low = dataframe['low'].rolling(window=26).min()
        dataframe['kijun_sen'] = (period26_high + period26_low) / 2

        # Senkou Span A (shift forward 26)
        dataframe['senkou_span_a'] = ((dataframe['tenkan_sen'] + dataframe['kijun_sen']) / 2).shift(26)

        # Senkou Span B (52)
        period52_high = dataframe['high'].rolling(window=52).max()
        period52_low = dataframe['low'].rolling(window=52).min()
        dataframe['senkou_span_b'] = ((period52_high + period52_low) / 2).shift(26)

        # Chikou Span (lagging)
        dataframe['chikou_span'] = dataframe['close'].shift(-26)

        # Ensure buy/sell columns exist and are zero-filled
        dataframe['buy'] = 0
        dataframe['sell'] = 0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 條件：Tenkan > Kijun，且收盤價高於雲區 (senkou_span_a 和 senkou_span_b)
        cond = (
            (dataframe['tenkan_sen'] > dataframe['kijun_sen']) &
            (dataframe['close'] > dataframe['senkou_span_a']) &
            (dataframe['close'] > dataframe['senkou_span_b'])
        )

        dataframe.loc[cond, 'buy'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 反向條件：Tenkan < Kijun，或收盤價跌破雲區任一
        cond = (
            (dataframe['tenkan_sen'] < dataframe['kijun_sen']) |
            (dataframe['close'] < dataframe['senkou_span_a']) |
            (dataframe['close'] < dataframe['senkou_span_b'])
        )

        dataframe.loc[cond, 'sell'] = 1
        return dataframe
