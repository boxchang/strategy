import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional, Union

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
)
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib


class BigWaveTrendStrategy(IStrategy):
    """
    大波段趨勢追蹤策略 (Big Wave Trend Following Strategy)
    
    此策略專為捕捉類似 PLTR, MSFT, ORCL, HOOD 的強勢多頭大波段而設計。
    核心邏輯：
    - 買入：均線多頭排列 (EMA 7 > 25 > 99) + MACD 黃金交叉/多頭柱 + RSI 動能確認
    - 賣出：價格跌破 EMA(25) 或是 啟用移動止損 (Trailing Stop) 鎖定利潤，確保能抱完整個大波段
    """

    # 策略介面版本
    INTERFACE_VERSION = 3

    # 買賣與資金管理設定
    timeframe = '1h'          # 配合圖表中的 1小時 (1h) K線
    stoploss = -0.10         # 初始硬止損 10% (主要依賴移動止損或指標出場)
    leverage_value = 15.0
    # 啟用移動止損 (Trailing Stop) - 這對於抱住大波段並在回撤時鎖定利潤至關重要
    trailing_stop = True
    trailing_stop_positive = 0.05      # 獲利達到 5% 後啟動移動止損
    trailing_stop_positive_offset = 0.08  # 觸發移動止損的利潤偏移量 (8%)
    trailing_only_offset_is_reached = True # 只有在達到 offset 後才開始移動

    # 運作控制
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # 最小投資回報率 (ROI) 表：我們不設置過低的自動止盈，讓利潤奔跑
    # 這裡的設定非常寬鬆，目的是讓移動止損和指標出場主導
    minimal_roi = {
        "0": 0.50,            # 50% 收益時直接止盈
        "120": 0.30,          # 120 小時後，若有 30% 收益則止盈
        "240": 0.15,          # 240 小時後，若有 15% 收益則止盈
        "720": 0.05           # 30天後，若有 5% 收益則止盈
    }

    # 策略參數調整 (可使用 freqtrade hyperopt 進行優化)
    buy_rsi_min = IntParameter(50, 65, default=55, space="buy")
    buy_ema_long_len = IntParameter(80, 120, default=99, space="buy")
    
    # 標記運行的指標
    plot_config = {
        'main_plot': {
            'ema7': {'color': '#FFD700'},    # 黃色
            'ema25': {'color': '#FF69B4'},   # 粉色
            'ema99': {'color': '#8A2BE2'},   # 紫色
        },
        'subplots': {
            "RSI": {
                'rsi6': {'color': '#FFA500'},
                'rsi12': {'color': '#DDA0DD'},
                'rsi24': {'color': '#800080'},
            },
            "MACD": {
                'macd': {'color': '#0000FF'},
                'macdsignal': {'color': '#FF0000'},
                'macdhist': {'color': '#00FF00', 'type': 'bar'},
            }
        }
    }

    def informative_pairs(self):
        """
        定義需要額外下載的貨幣對數據（此策略僅需當前 timeframe）
        """
        return []

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        計算技術指標
        """
        # 1. 均線系統 (EMA)
        dataframe['ema7'] = ta.EMA(dataframe, timeperiod=7)
        dataframe['ema25'] = ta.EMA(dataframe, timeperiod=25)
        dataframe['ema99'] = ta.EMA(dataframe, timeperiod=self.buy_ema_long_len.value)

        # 2. 相對強弱指標 (RSI)
        dataframe['rsi6'] = ta.RSI(dataframe, timeperiod=6)
        dataframe['rsi12'] = ta.RSI(dataframe, timeperiod=12)
        dataframe['rsi24'] = ta.RSI(dataframe, timeperiod=24)

        # 3. MACD
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']

        # 4. 成交量均線 (避免在 populate_entry_trend 進行複雜計算，且指定 price='volume')
        dataframe['volume_sma10'] = ta.SMA(dataframe, timeperiod=10, price='volume')

        # 5. 前一根 K 棒的收盤價，用於確認是否突破
        dataframe['prev_close'] = dataframe['close'].shift(1)
        dataframe['prev_ema25'] = dataframe['ema25'].shift(1)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        進場訊號：
        滿足以下多頭條件時買入：
        1. 均線多頭排列：EMA(7) > EMA(25) > EMA(99) 且收盤價高於 EMA(7)
        2. 價格站穩在 EMA(99)（牛熊分界線）之上
        3. MACD 動能支持：DIF > DEA (快線在慢線上) 且 MACD 柱狀圖為正數
        4. RSI 短期動能強勁：RSI(6) > 55 且 RSI(6) > RSI(12) 
        5. 當前 K 線為陽線（綠棒）
        """
        conditions = []

        conditions.append(
            # 均線多頭排列
            (dataframe['ema7'] > dataframe['ema25']) &
            (dataframe['ema25'] > dataframe['ema99']) &
            (dataframe['close'] > dataframe['ema7'])
        )

        conditions.append(
            # MACD 處於上升通道
            (dataframe['macd'] > dataframe['macdsignal']) &
            (dataframe['macdhist'] > 0)
        )

        conditions.append(
            # RSI 強勢區間與多頭排列
            (dataframe['rsi6'] > self.buy_rsi_min.value) &
            (dataframe['rsi6'] > dataframe['rsi12']) &
            (dataframe['rsi12'] > dataframe['rsi24'])
        )

        conditions.append(
            # 確保有成交量支撐 (使用在 populate_indicators 中預先算好的 volume_sma10)
            (dataframe['volume'] > dataframe['volume_sma10'] * 0.5)
        )

        if conditions:
            dataframe.loc[
                np.logical_and.reduce(conditions),
                'enter_long'
            ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        出場訊號：
        當趨勢結束時賣出以鎖定大波段利潤。
        符合以下條件之一時賣出：
        1. K 線收盤價跌破 EMA(25)（粉色線，代表跌破中期支撐）
        2. EMA(7) 死亡交叉 EMA(25)（短期趨勢轉折）
        3. RSI(6) 嚴重走弱並跌破 45
        """
        conditions = []

        conditions.append(
            # 條件 1：收盤價跌破 EMA(25)
            (dataframe['close'] < dataframe['ema25'])
        )

        conditions.append(
            # 條件 2：EMA(7) 跌破 EMA(25)（死亡交叉）
            (qtpylib.crossed_below(dataframe['ema7'], dataframe['ema25']))
        )

        conditions.append(
            # 條件 3：RSI(6) 急速跌破中線，表示多頭動能耗盡
            (dataframe['rsi6'] < 45)
        )

        if conditions:
            dataframe.loc[
                np.logical_or.reduce(conditions),
                'exit_long'
            ] = 1

        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return self.leverage_value