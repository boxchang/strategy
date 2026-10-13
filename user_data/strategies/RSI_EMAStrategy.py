from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta

class RSI_EMAStrategy(IStrategy):
    # 使用 5 分鐘 K 線
    timeframe = '5m'

    # ROI 設定：分段獲利目標
    minimal_roi = {
        "0": 0.04,   # 立即獲利目標 4%
        "30": 0.02,  # 30 分鐘後降到 2%
        "60": 0.01   # 60 分鐘後降到 1%
    }

    # 止損設定
    stoploss = -0.02   # 最大虧損 2%

    # 移動停損
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI 指標
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        # EMA 指標
        dataframe['ema200'] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI < 30 且 價格高於 EMA200 → 只在多頭趨勢中逢低買入
        dataframe.loc[
            (dataframe['rsi'] < 30) &
            (dataframe['close'] > dataframe['ema200']),
            'enter_long'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI > 70 或 價格跌破 EMA200 → 出場
        dataframe.loc[
            (dataframe['rsi'] > 70) |
            (dataframe['close'] < dataframe['ema200']),
            'exit_long'
        ] = 1
        return dataframe