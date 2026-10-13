from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

class MA7MA25Cross(IStrategy):
    timeframe = '1h'
    can_short = False

    leverage = 5

    minimal_roi = {"0": 0.1}
    stoploss = -0.1

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['ma7'] = ta.SMA(dataframe, timeperiod=7)
        dataframe['ma25'] = ta.SMA(dataframe, timeperiod=25)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['ma7'] > dataframe['ma25']) &
            (dataframe['ma7'].shift(1) <= dataframe['ma25'].shift(1)),
            'buy'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['ma7'] < dataframe['ma25']) &
            (dataframe['ma7'].shift(1) >= dataframe['ma25'].shift(1)),
            'sell'
        ] = 1
        return dataframe