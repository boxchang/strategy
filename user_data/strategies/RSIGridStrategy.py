from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.persistence import Trade
from typing import Optional
from datetime import datetime

class RSIGridStrategy(IStrategy):
    # 策略介面版本
    INTERFACE_VERSION = 1

    # --- 參數調整區 ---
    # 開啟位置調整功能（網格核心）
    position_adjustment_enable = True
    max_entry_nodes = 10  # 最大格數（包含起始單）

    # 參數優化範圍 (可透過 hyperopt 優化)
    grid_atr_mult = DecimalParameter(1.0, 3.0, default=3, space='buy')
    adx_threshold = IntParameter(15, 35, default=15, space='buy')

    timeframe = '15m'
    leverage_value = 5.0

    minimal_roi = {
        "0": 0.03,
        "30": 0.02,
        "60": 0.01
    }

    stoploss = -0.10

    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    def custom_stoploss(self, pair: str, trade, current_time, current_rate, current_profit, **kwargs) -> float:
        """
        動態止損：
        - 每次補單後，止損距離會放寬一些
        - 基於 ATR 計算止損距離
        """

        # 取得最近一根 K 線的 ATR
        dataframe = self.dp.get_pair_dataframe(pair=pair, timeframe=self.timeframe)
        last_atr = dataframe['atr'].iloc[-1]

        # 假設我們把補單次數存在 trade.user_data
        # 沒有的話就預設為 0
        add_count = trade.user_data.get("add_count", 0)

        # 動態計算止損距離：基礎 1.5 倍 ATR + 每次補單再加 0.5 倍 ATR
        atr_multiplier = 1.5 + (0.5 * add_count)
        stoploss_distance = last_atr * atr_multiplier

        # 換算成百分比（相對於當前價格）
        stoploss_pct = stoploss_distance / current_rate

        # 回傳負數（Freqtrade 規定止損要用負百分比）
        return -stoploss_pct

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI 指標
        dataframe['rsi'] = ta.RSI(dataframe['close'], timeperiod=14)

        # 成交量均線 (用來判斷大戶進場)
        dataframe['vol_ma20'] = ta.SMA(dataframe['volume'], timeperiod=20)

        # 均線 (MA5 與 MA20)
        dataframe['ma5'] = ta.SMA(dataframe['close'], timeperiod=5)
        dataframe['ma20'] = ta.SMA(dataframe['close'], timeperiod=20)

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
        # 進場條件：RSI < 20 + 當前成交量 > 20期均量 + MA5 > MA20
        dataframe.loc[
            (dataframe['ma5'] > dataframe['ma20']) &
            (dataframe['rsi'] < 40) &
            (dataframe['volume'] > dataframe['vol_ma20']),
            'enter_long'
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 出場條件：MA5 < MA20
        dataframe.loc[
            (dataframe['rsi'] > 80),
            'exit_long'
        ] = 1

        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        # 回傳槓桿倍數
        return self.leverage_value

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