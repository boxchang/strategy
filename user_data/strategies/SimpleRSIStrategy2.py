import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional
import talib.abstract as ta
import logging
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

logger = logging.getLogger(__name__)

class SimpleRSIStrategy2(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '15m'
    can_short = False

    # 資金與加倉 (DCA) 控制
    position_adjustment_enable = False
    max_entry_position_adjustment = 10
    
    # 【主導出場機制】ROI 設定
    # 策略會根據此表優先進行獲利了結
    minimal_roi = {
        "0": 0.28,    # 進場後若瞬間獲利 28% 直接出場
        "120": 0.15,  # 2 小時後獲利 15% 出場
        "300": 0.05,  # 5 小時後獲利 5% 出場
        "600": 0.01   # 10 小時後保本微利出場
    }

    # 參數化設定
    buy_rsi = IntParameter(5, 30, default=20, space="buy", optimize=True)
    exit_rsi = IntParameter(70, 95, default=90, space="sell", optimize=True)
    leverage_val = IntParameter(1, 20, default=4, space="entry", optimize=True)
    
    # 參數化 Trailing Stop 設定
    trailing_stop = True
    trailing_stop_positive = DecimalParameter(0.01, 0.10, default=0.05, space="sell", optimize=True)
    trailing_stop_positive_offset = DecimalParameter(0.02, 0.15, default=0.08, space="sell", optimize=True)
    trailing_only_offset_is_reached = True

    # DCA 參數
    dca_stage1_profit = DecimalParameter(-0.55, -0.35, default=-0.45, decimals=2, space="buy", optimize=True)
    dca_stage2_profit = DecimalParameter(-0.68, -0.50, default=-0.60, decimals=2, space="buy", optimize=True)
    dca_stage3_profit = DecimalParameter(-0.85, -0.65, default=-0.70, decimals=2, space="buy", optimize=True)

    # 資金管理
    tradable_balance_ratio = 0.99
    stoploss_val = DecimalParameter(-0.80, -0.10, default=-0.8, space="sell", optimize=True)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.stoploss = self.stoploss_val.value

    use_custom_stoploss = False
    startup_candle_count = 20
    
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=6)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0
        rsi_low = (dataframe['rsi'] < self.buy_rsi.value)
        dataframe.loc[rsi_low, 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        # 如果 RSI 超過出場門檻，也可作為輔助出場條件
        rsi_high = (dataframe['rsi'] > self.exit_rsi.value)
        dataframe.loc[rsi_high, 'exit_long'] = 1
        return dataframe

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        # 移除了原有的 profit_target，讓 ROI 機制主導獲利了結
        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return float(self.leverage_val.value)

    def adjust_trade_position(self, trade, current_time: datetime, current_rate: float,
                              current_profit: float, min_stake: float | None,
                              max_stake: float, **kwargs):
        if trade.nr_of_successful_entries >= self.max_entry_position_adjustment:
            return None
        
        # 檢查冷卻時間
        if trade.date_last_filled_utc:
            seconds = (current_time - trade.date_last_filled_utc).total_seconds()
            if seconds < 300: return None
    
        if min_stake is not None and max_stake < min_stake: return None
        if max_stake <= 0: return None
    
        base_stake = self.config.get('stake_amount', 30)
        if isinstance(base_stake, str) or base_stake is None: base_stake = 30
    
        min_s = min_stake if min_stake is not None else 0.0
        
        if current_profit <= self.dca_stage1_profit.value and trade.nr_of_successful_entries < 4:
            return max(min_s, min(base_stake * 0.1, max_stake)), "test_dca_stage1"
        elif current_profit <= self.dca_stage2_profit.value and trade.nr_of_successful_entries < 7:
            return max(min_s, min(base_stake * 0.15, max_stake)), "test_dca_stage2"
        elif current_profit <= self.dca_stage3_profit.value and trade.nr_of_successful_entries < 11:
            return max(min_s, min(base_stake * 0.2, max_stake)), "test_dca_stage3"
    
        return None