import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional
import talib.abstract as ta
import logging
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

logger = logging.getLogger(__name__)

class SimpleRSIShortStrategy2(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '15m'
    can_short = True  # 啟用做空
    
    # 參數設定
    # 當 RSI 超過此值時進場做空
    short_rsi = IntParameter(70, 95, default=80, space="entry", optimize=True)
    # 當 RSI 低於此值時平倉
    exit_rsi = IntParameter(5, 30, default=20, space="exit", optimize=True)
    
    # 槓桿參數
    leverage_val = IntParameter(1, 10, default=5, space="entry", optimize=True)
    
    # 獲利目標與停損
    profit_target = DecimalParameter(0.10, 0.50, default=0.30, space="sell", optimize=True)
    stoploss_val = DecimalParameter(0.10, 0.40, default=0.25, space="sell", optimize=True)

    # 初始停損 (負數表示虧損達到 25%)
    stoploss = -0.25

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.stoploss = -self.stoploss_val.value

    use_custom_stoploss = False
    startup_candle_count = 20
    
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=6)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_short'] = 0
        # 當 RSI 超過設定的 short_rsi 時觸發做空
        rsi_high = (dataframe['rsi'] > self.short_rsi.value)
        dataframe.loc[rsi_high, 'enter_short'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_short'] = 0
        # 當 RSI 低於設定的 exit_rsi 時平倉
        rsi_low = (dataframe['rsi'] < self.exit_rsi.value)
        dataframe.loc[rsi_low, 'exit_short'] = 1
        return dataframe

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        if current_profit >= self.profit_target.value:
            return 'profit_target'
        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return float(self.leverage_val.value)