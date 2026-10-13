from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame

class SimpleMovingAverageStrategy(IStrategy):
    timeframe = '15m'
    minimal_roi = {"0": 0.1}
    stoploss = -0.1

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['sma_short'] = dataframe['close'].rolling(10).mean()
        dataframe['sma_long'] = dataframe['close'].rolling(30).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['sma_short'] > dataframe['sma_long']),
            'enter_long'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['sma_short'] < dataframe['sma_long']),
            'exit_long'
        ] = 1
        return dataframe