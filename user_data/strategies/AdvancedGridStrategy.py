import numpy as np
import talib.abstract as ta
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from pandas import DataFrame
from datetime import datetime
from typing import Optional
from freqtrade.persistence import Trade


class AdvancedGridStrategy(IStrategy):
    # 策略介面版本
    INTERFACE_VERSION = 3

    # --- 參數調整區 ---
    # 開啟位置調整功能（網格核心）
    position_adjustment_enable = True
    max_entry_nodes = 5  # 最大格數（包含起始單）

    # 參數優化範圍 (可透過 hyperopt 優化)
    grid_atr_mult = DecimalParameter(1.0, 3.0, default=2.0, space='buy')
    adx_threshold = IntParameter(15, 35, default=25, space='buy')

    # 基礎設定
    timeframe = '15m'
    leverage_value = 5.0
    stoploss = -0.15  # 總體停損
    minimal_roi = {"0": 0.02}  # 總體獲利 2% 出場

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 趨勢指標
        dataframe['adx'] = ta.ADX(dataframe)
        dataframe['ema200'] = ta.EMA(dataframe, timeperiod=200)

        # 波動率指標 (用於計算動態網格間距)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # 震盪指標 (找進場點)
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lower'] = bollinger['lowerband']
        dataframe['bb_middle'] = bollinger['middleband']

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        定義開啟「第一格」的條件
        """
        dataframe.loc[
            (
                # 條件 1: 價格觸碰布林下軌 (超賣)
                    (dataframe['close'] < dataframe['bb_lower']) &
                    # 條件 2: ADX 較低，代表目前是震盪行情，適合跑網格
                    (dataframe['adx'] < self.adx_threshold.value) &
                    # 條件 3: 價格在長期均線附近，避免大趨勢反轉
                    (dataframe['close'] > dataframe['ema200'] * 0.95)
            ),
            'enter_long'] = 1

        return dataframe

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float, max_stake: float,
                              **kwargs) -> Optional[float]:
        """
        自定義網格補倉邏輯
        """
        # 獲取最新資料
        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        last_candle = dataframe.iloc[-1]

        # 1. 檢查是否達到最大格數
        count_of_entries = trade.nr_of_successful_entries
        if count_of_entries >= self.max_entry_nodes:
            return None

        # 2. 計算動態網格間距 (基於 ATR)
        # 這裡設定：下跌超過 1.5 倍 ATR 且目前虧損中才補下一格
        dynamic_gap = (last_candle['atr'] * self.grid_atr_mult.value) / current_rate

        if current_profit > -dynamic_gap:
            return None

        # 3. 趨勢過濾：如果 ADX 突然飆升且價格大跌，暫時不補倉（避開瀑布）
        if last_candle['adx'] > 40 and last_candle['close'] < last_candle['ema200']:
            return None

        # 4. 決定補倉金額 (這裡使用等額，也可以改成 trade.stake_amount * 1.5 做加倍)
        return trade.stake_amount

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        一般網格靠 ROI 或 Trailing Stop 出場，
        但這裡可以加入「趨勢反轉」強制出場條件。
        """
        dataframe.loc[
            (
                # 例如：當 ADX 極高且價格跌破 EMA200，可能發生系統性風險
                    (dataframe['adx'] > 50) &
                    (dataframe['close'] < dataframe['ema200'])
            ),
            'exit_long'] = 1
        return dataframe


    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        # 回傳槓桿倍數
        return self.leverage_value