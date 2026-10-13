# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# isort: skip_file
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional
from freqtrade.strategy import IStrategy, IntParameter
from freqtrade.persistence import Trade
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

class LucyStrategy(IStrategy):
    """
    LucyStrategy:
    - 移除時間限制，任何時間均可進場。
    - Long: RSI < 設定值時做多。
    - Short: RSI > 設定值時做空。
    - 出場: 只要獲利達到 5 USDT 即刻平倉 (custom_exit)。
    """
    
    INTERFACE_VERSION = 3

    # 允許做空
    can_short = True

    # 禁用 ROI 平倉
    minimal_roi = {
        "0": 100.0  
    }
    stoploss = -0.99 

    # 預設 K 線週期
    timeframe = '15m'

    # --- Hyperopt 參數設定 ---
    leverage_param = IntParameter(1, 50, default=20, space='buy', optimize=True)
    
    # RSI 參數
    rsi_entry_long = IntParameter(5, 20, default=10, space='buy', optimize=True)
    rsi_entry_short = IntParameter(80, 95, default=90, space='buy', optimize=True)

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return float(self.leverage_param.value)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        # 計算當前匯率下的絕對獲利 (USDT)
        profit_abs = trade.calc_profit(current_rate)
        
        # 增加日誌記錄，方便檢查是否曾達到 5 USDT
        logger.info(f"DEBUG: Pair: {pair}, Current Profit: {current_profit:.4f}, Absolute Profit: {profit_abs:.4f} USDT")

        if profit_abs >= 5.0:
            return "ProfitTarget_5USDT"
        return None

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # 使用週期 6 以符合您先前觀察的指標設定
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=6)
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Long: 移除日期過濾，僅根據 RSI 判斷
        long_condition = (dataframe['rsi'] < self.rsi_entry_long.value)
        dataframe.loc[long_condition, 'enter_long'] = 1

        # Short: 移除日期過濾，僅根據 RSI 判斷
        short_condition = (dataframe['rsi'] > self.rsi_entry_short.value)
        dataframe.loc[short_condition, 'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe