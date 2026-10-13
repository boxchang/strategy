import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from technical import qtpylib

class KDStrategy(IStrategy):
    # 策略介面版本
    INTERFACE_VERSION = 3
    
    # 時間週期
    timeframe = '15m'

    # --- 參數化設定 (可透過 Hyperopt 優化) ---
    # FastK 的週期 (通常 9 或 14)
    fastk_period = IntParameter(5, 20, default=9, space="buy", optimize=True)
    # SlowK 的週期
    slowk_period = IntParameter(2, 6, default=3, space="buy", optimize=True)
    # SlowD 的週期
    slowd_period = IntParameter(2, 6, default=3, space="buy", optimize=True)
    
    # 進場門檻：K值低於此值時視為超賣 (買入區)
    buy_threshold = IntParameter(10, 30, default=20, space="buy", optimize=True)
    # 出場門檻：K值高於此值時視為超買 (賣出區)
    sell_threshold = IntParameter(70, 90, default=80, space="sell", optimize=True)

    stoploss_val = DecimalParameter(-0.80, -0.10, default=-0.8, space="sell", optimize=True)

    def __init__(self, config: dict) -> None:
            super().__init__(config)
            self.stoploss = self.stoploss_val.value

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        計算 KD 指標
        talib.STOCH 回傳兩個陣列: [slowk, slowd]
        """
        stoch = ta.STOCH(
            dataframe,
            fastk_period=self.fastk_period.value,
            slowk_period=self.slowk_period.value,
            slowd_period=self.slowd_period.value
        )
        dataframe['slowk'] = stoch['slowk']
        dataframe['slowd'] = stoch['slowd']
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        買入邏輯：
        當 K 值 < D 值 (黃金交叉) 且 K 值 < buy_threshold (超賣)
        """
        dataframe.loc[
            (
                (qtpylib.crossed_above(dataframe['slowk'], dataframe['slowd'])) &
                (dataframe['slowk'] < self.buy_threshold.value)
            ),
            'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        賣出邏輯：
        當 K 值 > D 值 (死亡交叉) 且 K 值 > sell_threshold (超買)
        """
        dataframe.loc[
            (
                (qtpylib.crossed_below(dataframe['slowk'], dataframe['slowd'])) &
                (dataframe['slowk'] > self.sell_threshold.value)
            ),
            'exit_long'] = 1
        return dataframe
