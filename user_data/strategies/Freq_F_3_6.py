import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    DecimalParameter,
    CategoricalParameter,
    stoploss_from_open,
)
from freqtrade.persistence import Trade
import logging

logger = logging.getLogger(__name__)


class Freq_F_3_6(IStrategy):
    """
    Freq_F_3_6 Strategy (Futures / 期貨版本)
    - 支持 Long & Short
    - 支持槓桿設定 (Leverage)
    - 55 EMA 趨勢跟隨與大盤過濾
    """
    # 策略介面版本
    INTERFACE_VERSION = 3

    # K線時間框架
    timeframe = '1h'

    # 開啟做空 (期貨模式必需)
    can_short = True

    # 啟動所需的 K 線數量
    startup_candle_count = 200

    # 預設固定停損點 (可被 custom_stoploss 覆蓋)
    stoploss = -0.15

    # 預設 ROI 階梯獲利出場
    minimal_roi = {
        "0": 0.08,
        "40": 0.04,
        "120": 0.02,
        "240": 0.005
    }

    # ==========================================
    # 可優化參數 (Hyperoptable Parameters)
    # ==========================================
    # 槓桿設定 (預設 3 倍)
    leverage_level = IntParameter(1, 10, default=3, space='buy', optimize=False)

    # 趨勢與指標參數
    ema_period = IntParameter(30, 100, default=55, space='buy', optimize=True)
    rsi_period = IntParameter(10, 20, default=14, space='buy', optimize=True)
    rsi_buy_min = IntParameter(50, 70, default=55, space='buy', optimize=True)
    rsi_sell_max = IntParameter(30, 50, default=45, space='buy', optimize=True)

    # 突破幅度門檻 (例如突破 EMA 1%)
    breakout_threshold = DecimalParameter(0.005, 0.03, default=0.01, decimals=3, space='buy', optimize=True)

    # ==========================================
    # 槓桿設置 Callback
    # ==========================================
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: Optional[str], side: str, **kwargs) -> float:
        """
        針對期貨交易動態計算槓桿倍數，不超過交易所允許的最大槓桿
        """
        return float(min(self.leverage_level.value, max_leverage))

    # ==========================================
    # 指標計算 (Populate Indicators)
    # ==========================================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # EMA 主趨勢線
        dataframe['ema_55'] = ta.EMA(dataframe, timeperiod=self.ema_period.value)
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)

        # RSI 相對強弱指標
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)

        # ATR 平均真實波幅 (用於計算動態風控)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # 成交量均線過濾
        dataframe['volume_mean'] = dataframe['volume'].rolling(20).mean()

        return dataframe

    # ==========================================
    # 進場邏輯 (Populate Entry Trend)
    # ==========================================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        dataframe['enter_tag'] = None

        # 成交量過濾：避免無量假突破
        vol_filter = dataframe['volume'] > (dataframe['volume_mean'] * 0.5)

        # 做多條件：價格突破 55 EMA 一定比例 + RSI 高於門檻 + 位於長線 EMA200 之上
        cond_long = (
            (dataframe['close'] > dataframe['ema_55'] * (1 + self.breakout_threshold.value)) &
            (dataframe['close'] > dataframe['ema_200']) &
            (dataframe['rsi'] > self.rsi_buy_min.value) &
            vol_filter
        )

        # 做空條件：價格跌破 55 EMA 一定比例 + RSI 低於門檻 + 位於長線 EMA200 之下
        cond_short = (
            (dataframe['close'] < dataframe['ema_55'] * (1 - self.breakout_threshold.value)) &
            (dataframe['close'] < dataframe['ema_200']) &
            (dataframe['rsi'] < self.rsi_sell_max.value) &
            vol_filter
        )

        dataframe.loc[cond_long, ['enter_long', 'enter_tag']] = [1, 'f_long_entry']
        dataframe.loc[cond_short, ['enter_short', 'enter_tag']] = [1, 'f_short_entry']

        return dataframe

    # ==========================================
    # 出場邏輯 (Populate Exit Trend)
    # ==========================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0

        # 做多平倉條件：價格重新跌破 55 EMA 或 RSI 過熱 (>80)
        dataframe.loc[
            (dataframe['close'] < dataframe['ema_55']) |
            (dataframe['rsi'] > 80),
            'exit_long'
        ] = 1

        # 做空平倉條件：價格重新突破 55 EMA 或 RSI 超賣 (<20)
        dataframe.loc[
            (dataframe['close'] > dataframe['ema_55']) |
            (dataframe['rsi'] < 20),
            'exit_short'
        ] = 1

        return dataframe

    # ==========================================
    # 自訂動態停損 (Custom Stoploss)
    # ==========================================
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_profit: float, current_rate: float = 0.0, **kwargs) -> float:
        """
        考慮槓桿倍數的利潤保護與停損
        """
        # 如果獲利超過 5%，鎖定 1.5% 的利潤 (含槓桿)
        if current_profit > 0.05:
            res = stoploss_from_open(0.015, current_profit, is_short=trade.is_short, leverage=trade.leverage)
            if res is not None:
                return res

        # 保持預設停損
        return self.stoploss