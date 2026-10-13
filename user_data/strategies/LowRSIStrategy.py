import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime, timezone
from typing import Optional, Union
import logging

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
)
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib


class LowRSIStrategy(IStrategy):
    """
    RSI 低位建倉與高位止盈策略 (15x 槓桿 + 盈利加倉版)
    
    策略核心邏輯：
    1. 【進場】：當 1 小時 K 線的 RSI(14) 跌破 30（超賣）時，開多單（Long）。
    2. 【出場】：當 RSI(14) 突破 80（超買）時，平多單止盈。
    3. 【槓桿】：固定開 15x 槓桿。
    4. 【加倉 (DCA/Pyramiding)】：
       - 當目前交易「已經獲利超過 10%」時，
       - 且距離上一次成交時間超過 5 分鐘，
       - 且總進場次數小於 4 次，
       - 則自動追加名義資金（最大 100 USDT 或 max_stake）進行「順勢加倉」。
    5. 【移動止損 (Trailing Stop)】：
       - 達到 8% 獲利後啟用，回撤 5% 時鎖定利潤出場。
    """

    # 啟用日誌記錄器
    logger = logging.getLogger(__name__)

    # Freqtrade 策略介面版本
    INTERFACE_VERSION = 3

    # 1. 核心交易設定
    timeframe = '1h'       # 運行 K 線週期：1 小時
    can_short = True       # 允許做空（但目前 populate 只有寫做多訊號）
    
    # 2. 資金與加倉（DCA）控制
    position_adjustment_enable = True       # 必須設為 True 才能啟用下面的 adjust_trade_position 函數
    max_entry_position_adjustment = 10      # 單筆交易最大允許的追加進場次數

    # 3. 止盈止損設定
    stoploss = -0.2  # 修正：將止損設回 -0.2 (20%)

    # 啟用移動止損 (Trailing Stop) - 這對於抱住大波段並在回撤時鎖定利潤至關重要
    trailing_stop = True
    trailing_stop_positive = 0.05      # 獲利達到 5% 後啟動移動止損
    trailing_stop_positive_offset = 0.08  # 觸發移動止損的利潤偏移量 (8%)
    trailing_only_offset_is_reached = True # 只有在達到 offset 後才開始移動

    # 修改：自動止盈條件改為 50% 獲利 (0.50)
    minimal_roi = {
        "0": 0.50  # 只要利潤達到 50%，立刻觸發自動 ROI 止盈離場
    }

    # 4. 槓桿倍數設定
    leverage_value = 15.0

    # 繪圖設定
    plot_config = {
        'main_plot': {},
        'subplots': {
            "RSI": {
                'rsi': {'color': '#FFA500'},
            }
        }
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        計算技術指標
        """
        # 在 Freqtrade 中使用 talib.abstract 時，直接傳入 dataframe
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        多頭（Long）進場訊號：當 RSI < 30 時進場
        """
        dataframe.loc[
            (dataframe['rsi'] < 30),
            ['enter_long', 'enter_tag']
        ] = (1, 'rsi_long')

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        多頭（Long）出場訊號：當 RSI 突破 80 時離場
        """
        dataframe.loc[
            (dataframe['rsi'] > 80),
            ['exit_long', 'exit_tag']
        ] = (1, 'rsi_exit_long')

        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        """
        自訂槓桿乘數：強制全域使用 15 倍槓桿
        """
        return self.leverage_value

    def adjust_trade_position(
            self,
            trade,
            current_time: datetime,
            current_rate: float,
            current_profit: float,
            min_stake: float | None,
            max_stake: float,
            current_entry_rate: float,
            current_exit_rate: float,
            current_entry_profit: float,
            current_exit_profit: float,
            **kwargs
    ):
        """
        動態倉位調整（加倉 / DCA 邏輯）
        
        此函數會在有持倉的情況下，於每個 Candle 結束時被調用，用以決定是否要增加持倉。
        """
        # 1. 檢查冷卻時間：避免在極短時間內連續加倉
        if trade.date_last_filled_utc:
            seconds = (current_time - trade.date_last_filled_utc).total_seconds()

            # 距離上一次買入成交不滿 5 分鐘，拒絕加倉
            if seconds < 5 * 60:
                return None

        # 2. 輸出當前狀態到 Freqtrade 日誌
        self.logger.info(
            f"DCA 檢測呼叫: {trade.pair}, 當前利潤={current_profit:.4f}, "
            f"已成交次數={trade.nr_of_successful_entries}"
        )

        # 3. 觸發加倉條件：
        # 當前總利潤 > 10% 且 目前這筆交易成功的進場次數小於 4 次
        if current_profit > 0.10 and trade.nr_of_successful_entries < 4:
            # 計算本次加倉本金：取 100 USDT 或 錢包允許的最大單筆開倉金額（max_stake）的最小值
            stake = min(100, max_stake)
            
            # 返回加倉金額與標記 tag
            return stake, "test_dca"

        # 如果不滿足條件，返回 None 代表不進行任何倉位調整
        return None