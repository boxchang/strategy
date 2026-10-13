import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional
import talib.abstract as ta
import logging
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)

class SimpleKDStrategy(IStrategy):
    """
    SimpleKDStrategy - 基於 KD (Stochastic) 低位黃金交叉與雙模式趨勢過濾的量化策略
    
    核心機制：
    1. 指標：KD (9, 3, 3) 隨機指標 + EMA 50/200 趨勢線 + ADX 趨勢強度 + BB 布林通道。
    2. 進場：KD 低位金叉 (K線上穿D線) + 雙模式過濾 (強趨勢回檔模式 / 盤整抄底模式)。
    3. 出場：KD 高位死叉獲利離場 / 觸碰布林上軌爆發平倉 / 3小時無效單快速停損。
    4. 風控：徹底關閉 DCA (無加倉)，硬停損設為 -4.0% (槓桿2x下最大虧損僅 -8%)。
    """
    INTERFACE_VERSION = 3
    timeframe = '15m'
    can_short = False

    # 資金與加倉 (DCA) 控制：徹底關閉 DCA，避免單筆虧損無限放大
    position_adjustment_enable = False
    max_entry_position_adjustment = 0

    # 【動態獲利讓飛】放寬 ROI 限制，讓金叉強彈趨勢充分發揮利潤
    minimal_roi = {
        "0": 0.12,     # 瞬時衝高 12% 出場
        "30": 0.06,    # 30 分鐘獲利 6% 出場
        "90": 0.03,    # 1.5 小時獲利 3% 出場
        "240": 0.015   # 4 小時獲利 1.5% 出場
    }

    # 參數化設定 (KD 指標進出場門檻)
    buy_kd_range = IntParameter(10, 30, default=20, space="buy", optimize=True)   # 盤整區間 KD 金叉門檻
    buy_kd_trend = IntParameter(25, 45, default=35, space="buy", optimize=True)   # 多頭趨勢 KD 金叉門檻
    exit_kd = IntParameter(70, 90, default=80, space="sell", optimize=True)       # KD 高位平倉門檻
    leverage_val = IntParameter(1, 3, default=2, space="entry", optimize=True)

    # 移動止盈設定 (改為標準 float 數值，避免 Freqtrade 驗證失敗)
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.04
    trailing_only_offset_is_reached = True

    # 嚴格風控停損：固定單筆最大虧損為 4.0%
    stoploss = -0.040
    use_custom_stoploss = False
    startup_candle_count = 200

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1. KD (Stochastic) 指標：使用標準參數 fastk_period=9, slowk_period=3, slowd_period=3
        stoch = ta.STOCH(
            dataframe,
            fastk_period=9,
            slowk_period=3,
            slowk_matype=0,
            slowd_period=3,
            slowd_matype=0
        )
        dataframe['slowk'] = stoch['slowk']  # K 線
        dataframe['slowd'] = stoch['slowd']  # D 線

        # 2. 長期與短期趨勢均線
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)

        # 3. 趨勢強度指標 ADX
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # 4. 布林通道 (用於動態波段高點出場)
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_upperband'] = bollinger['upperband']
        dataframe['bb_middleband'] = bollinger['middleband']

        # 5. 暴跌過濾：計算過去 1 小時 (4 根 15m K線) 的跌幅
        dataframe['roc_1h'] = ta.ROC(dataframe, timeperiod=4)

        # 6. 成交量均線 (過濾無量假金叉)
        dataframe['volume_mean'] = ta.SMA(dataframe['volume'], timeperiod=20)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0

        # 通用防瀑布濾網：1小時內跌幅不大於 3.0%
        no_crash = (dataframe['roc_1h'] > -3.0)

        # 量能濾網：當前成交量高於近 20 根 K 線平均，確保有資金進場帶動反彈
        has_volume = (dataframe['volume'] > dataframe['volume_mean'] * 0.9)

        # 【關鍵止跌訊號】：KD 黃金交叉 (前一根 K <= D，當前 K > D)
        kd_golden_cross = (
            (dataframe['slowk'] > dataframe['slowd']) & 
            (dataframe['slowk'].shift(1) <= dataframe['slowd'].shift(1))
        )

        # 模式 1：強多頭趨勢回檔 (Trend Pullback Engine)
        is_strong_uptrend = (dataframe['close'] > dataframe['ema_50']) & (dataframe['ema_50'] > dataframe['ema_200'])
        is_trending_market = (dataframe['adx'] >= 20)
        kd_trend_dip = (dataframe['slowk'] < self.buy_kd_trend.value)
        entry_mode_trend = is_strong_uptrend & is_trending_market & kd_trend_dip & kd_golden_cross & no_crash & has_volume

        # 模式 2：盤整區間深跌抄底 (Range Scalp Engine)
        is_weak_uptrend = (dataframe['close'] > dataframe['ema_200'])
        is_ranging_market = (dataframe['adx'] < 20)
        kd_range_dip = (dataframe['slowk'] < self.buy_kd_range.value)
        entry_mode_range = is_weak_uptrend & is_ranging_market & kd_range_dip & kd_golden_cross & no_crash & has_volume

        # 觸發任意一種進場模式
        dataframe.loc[
            entry_mode_trend | entry_mode_range,
            'enter_long'
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if not dataframe.empty:
            last_candle = dataframe.iloc[-1]
            prev_candle = dataframe.iloc[-2]

            # 判斷 KD 死亡交叉 (K線下穿D線)
            kd_death_cross = (last_candle['slowk'] < last_candle['slowd']) and (prev_candle['slowk'] >= prev_candle['slowd'])

            # 1. 布林通道上軌衝高爆發離場 (當價格觸碰布林上軌且獲利 > 2.0% 時平倉)
            if current_rate >= last_candle['bb_upperband'] and current_profit > 0.02:
                return "bb_upper_profit_exit"

            # 2. KD 高位死亡交叉獲利離場 (獲利 > 1.2% 且 K值高於 exit_kd，並出現死叉)
            if last_candle['slowk'] > self.exit_kd.value and kd_death_cross and current_profit > 0.012:
                return "kd_death_cross_profit_exit"

            # 3. 超長滯留平倉：放寬持倉時間至 8 小時 (480分) 且虧損 > 3.0%，確認反彈動能衰竭才認賠離場
            trade_duration = (current_time - trade.open_date_utc).total_seconds() / 60
            if trade_duration > 480 and current_profit < -0.030:
                return "stagnant_trend_exit"

        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return float(self.leverage_val.value)

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                              current_profit: float, min_stake: float | None,
                              max_stake: float, **kwargs):
        # 徹底停用 DCA 加倉
        return None