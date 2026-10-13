from freqtrade.strategy.interface import IStrategy
from freqtrade.strategy import IntParameter, DecimalParameter, CategoricalParameter
from pandas import DataFrame
import talib.abstract as ta
from datetime import timedelta

class QianLongStrategy(IStrategy):
    # 預設 timeframe
    timeframe = '15m'
    can_short = True
    stoploss = -0.05
    leverage_value = 5.0

    # -------------------------------
    # Hyperopt 參數設定
    # -------------------------------
    ma_short_length = IntParameter(3, 15, default=5, space='buy', optimize=True)
    ma_mid_length = IntParameter(10, 50, default=20, space='buy', optimize=True)
    volume_ma_length = IntParameter(5, 30, default=10, space='buy', optimize=True)

    rsi_length = IntParameter(7, 30, default=14, space='buy', optimize=True)
    rsi_buy_threshold = IntParameter(10, 40, default=30, space='buy', optimize=True)
    rsi_sell_threshold = IntParameter(60, 90, default=70, space='sell', optimize=True)

    custom_stoploss_opt = DecimalParameter(-0.15, -0.02, default=-0.05, decimals=3, space='sell', optimize=True)

    # -------------------------------
    # 指標計算
    # -------------------------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['ma_short'] = ta.SMA(dataframe, timeperiod=int(self.ma_short_length.value))
        dataframe['ma_mid'] = ta.SMA(dataframe, timeperiod=int(self.ma_mid_length.value))
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=int(self.volume_ma_length.value))
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=int(self.rsi_length.value))
        dataframe['force'] = dataframe['ma_short'] - dataframe['ma_mid']
        return dataframe

    # -------------------------------
    # 進場邏輯
    # -------------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['ma_short'] > dataframe['ma_mid']) &
            (dataframe['volume'] > dataframe['volume_ma']) &
            (dataframe['force'] > 0) &
            (dataframe['rsi'] < self.rsi_buy_threshold.value),
            'enter_long'
        ] = 1

        dataframe.loc[
            (dataframe['ma_short'] < dataframe['ma_mid']) &
            (dataframe['volume'] > dataframe['volume_ma']) &
            (dataframe['force'] < 0) &
            (dataframe['rsi'] > self.rsi_sell_threshold.value),
            'enter_short'
        ] = 1
        return dataframe

    # -------------------------------
    # 出場邏輯
    # -------------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe['ma_short'] < dataframe['ma_mid']), 'exit_long'] = 1
        dataframe.loc[(dataframe['ma_short'] > dataframe['ma_mid']), 'exit_short'] = 1
        return dataframe

    # -------------------------------
    # 自訂止損
    # -------------------------------
    def custom_stoploss(self, pair: str, trade, current_time, current_rate, current_profit, **kwargs):
        return float(self.custom_stoploss_opt.value)

    # -------------------------------
    # 兩小時後仍虧損 → 強制平倉
    # -------------------------------
    def custom_exit(self, pair: str, trade, current_time, current_rate, current_profit, **kwargs):
        holding_time = current_time - trade.open_date_utc
        if holding_time > timedelta(hours=2) and current_profit < 0:
            return "timeout_exit"
        return None
