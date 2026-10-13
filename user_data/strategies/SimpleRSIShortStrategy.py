import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional
import talib.abstract as ta
import logging
from freqtrade.strategy import IStrategy, IntParameter

logger = logging.getLogger(__name__)

class SimpleRSIShortStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '15m'
    can_short = True
    stoploss = -0.10
    
    # 強制預讀 K 線以確保 RSI 計算不為 NaN
    startup_candle_count = 20
    
    tradable_balance_ratio = 0.99
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.08
    trailing_only_offset_is_reached = True

    buy_rsi = IntParameter(5, 30, default=10, space="buy", optimize=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 使用週期 6 以符合設定
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=6)
        
        # 記錄所有 K 線的 RSI 數值，方便核對完整數據
        try:
            vn_time = dataframe['date'].dt.tz_convert('Asia/Ho_Chi_Minh')
            for i in range(len(dataframe)):
                logger.info(f"DEBUG: 時間 (VN): {vn_time.iloc[i]} | RSI: {dataframe['rsi'].iloc[i]:.2f}")
        except Exception as e:
            logger.error(f"DEBUG: 指標顯示錯誤: {e}")
        
        dataframe['debug_rsi'] = dataframe['rsi']
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0

        # Short 條件: RSI < 設定值
        short_cond = (dataframe['rsi'].notna()) & (dataframe['rsi'] < self.buy_rsi.value)
        
        # 將所有觸發進場的 RSI 資訊記錄下來
        trigger_df = dataframe.loc[short_cond]
        if not trigger_df.empty:
            for _, row in trigger_df.iterrows():
                logger.info(f"DEBUG: 進場觸發 | 時間: {row['date']} | RSI: {row['rsi']:.2f}")

        dataframe.loc[short_cond, 'enter_short'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return 10.0