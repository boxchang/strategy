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
    informative,
    stoploss_from_absolute,
    stoploss_from_open,
)
from freqtrade.persistence import Trade


class TrendMeanReversionV2(IStrategy):
    """
    雙向平行策略 V3 (Trend Breakout + Mean Reversion)

    V3 針對 V2 回測結果的修正：
    - 趨勢單 (勝率約 19%、持倉僅 2~3 小時被洗出)：
        * 止損改用 4h ATR (與 4h 趨勢論點同一時間尺度)，不再用 15m ATR
        * 「BB 寬度大於下限」改為「近期曾壓縮 + 現在擴張」，避免在行情已走完時追突破
    - 回歸單 (停損單約 1 小時就被掃，但中軌出場 100% 獲利)：
        * 止損放寬 (預設 3 倍 15m ATR)，給均值回歸時間
        * 進場加入反轉 K 線確認，不接下墜的刀
    - 加入 StoplossGuard / CooldownPeriod，避免連環止損

    V2 相較 V1 的主要改動：
    1. 多週期：4h 負責「方向與強度」(EMA200 + ADX)，15m 負責「進場發動點」
    2. 趨勢進場改為 Donchian 通道突破，並加上 BB Width / 量能 / ATR 波動度過濾
    3. 均值回歸只在 4h 無強趨勢 (ADX 低) 時啟用，避免逆勢接刀
    4. custom_stoploss 改為 ATR 動態追蹤止損 (趨勢單寬、回歸單窄)，
       並使用 stoploss_from_open / stoploss_from_absolute 修正 V1 的止損比例基準
    5. 取消固定 ROI；趨勢單靠追蹤止損 + 趨勢反轉出場，回歸單靠 RSI / 中軌回歸出場 (custom_exit)
    6. 反向訊號出現時平倉並翻轉 (Flip)
    """
    timeframe = '15m'
    can_short = True

    # ---------------- 風控 / 出場基本設定 ----------------
    use_custom_stoploss = True
    stoploss = -0.10                 # 全局硬止損 (最後防線)
    trailing_stop = False
    minimal_roi = {"0": 100}         # 等同停用固定 ROI，出場交給追蹤止損與 custom_exit
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    process_only_new_candles = True
    startup_candle_count: int = 300  # 4h EMA200 由 informative 自動放大 startup

    # ==================== Hyperopt 可優化參數 ====================
    # --- 趨勢突破 ---
    trend_ema_period = IntParameter(100, 300, default=200, space='buy', optimize=True)   # 15m 趨勢均線
    trend_adx_min = IntParameter(15, 35, default=22, space='buy', optimize=True)         # 4h ADX 下限
    trend_rsi_buy = IntParameter(50, 65, default=55, space='buy', optimize=True)
    trend_rsi_sell = IntParameter(35, 50, default=45, space='buy', optimize=True)
    trend_dc_period = IntParameter(20, 80, default=40, space='buy', optimize=True)       # Donchian 突破週期
    trend_vol_mult = DecimalParameter(1.0, 2.0, default=1.2, decimals=1, space='buy', optimize=True)
    trend_squeeze_q = DecimalParameter(0.10, 0.40, default=0.25, decimals=2, space='buy', optimize=True)  # 壓縮分位數

    # --- 均值回歸 ---
    mr_rsi_buy = IntParameter(15, 35, default=25, space='buy', optimize=True)
    mr_stoch_buy = IntParameter(10, 30, default=20, space='buy', optimize=True)
    mr_rsi_sell = IntParameter(65, 85, default=75, space='buy', optimize=True)
    mr_stoch_sell = IntParameter(70, 90, default=80, space='buy', optimize=True)
    mr_adx_max = IntParameter(18, 35, default=28, space='buy', optimize=True)            # 4h 強趨勢時不做回歸

    # --- 通用過濾 ---
    atr_pct_min = DecimalParameter(0.001, 0.005, default=0.002, decimals=3, space='buy', optimize=True)

    # --- 出場 ---
    exit_rsi_long = IntParameter(60, 85, default=70, space='sell', optimize=True)        # 回歸單多單出場
    exit_rsi_short = IntParameter(15, 40, default=30, space='sell', optimize=True)       # 回歸單空單出場
    atr_mult_trend = DecimalParameter(1.5, 4.0, default=2.5, decimals=1, space='sell', optimize=True)  # 乘 4h ATR
    atr_mult_mr = DecimalParameter(2.0, 5.0, default=3.0, decimals=1, space='sell', optimize=True)    # 乘 15m ATR
    # ============================================================

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 4},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 48,
                "trade_limit": 3,
                "stop_duration_candles": 24,
                "only_per_pair": True,
            },
        ]

    # ------------------------------------------------------------
    # 4h 大週期：方向 + 強度  (自動合併為 *_4h 欄位，無未來函數)
    # ------------------------------------------------------------
    @informative('4h')
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)
        dataframe['ema_slope_up'] = (dataframe['ema_200'] > dataframe['ema_200'].shift(3)).astype(int)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    # ------------------------------------------------------------
    # 15m 指標
    # ------------------------------------------------------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 波動度
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_pct'] = dataframe['atr'] / dataframe['close']

        # 布林帶 + 寬度
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lowerband'] = bollinger['lowerband']
        dataframe['bb_middleband'] = bollinger['middleband']
        dataframe['bb_upperband'] = bollinger['upperband']
        dataframe['bb_width'] = (
            (dataframe['bb_upperband'] - dataframe['bb_lowerband']) / dataframe['bb_middleband']
        )

        # 壓縮 → 擴張：近 24 根曾低於過去 200 根寬度的某分位，且目前寬度正在擴張
        dataframe['bb_width_prev_min'] = dataframe['bb_width'].shift(1).rolling(24).min()
        dataframe['bb_expanding'] = (dataframe['bb_width'] > dataframe['bb_width'].shift(1)).astype(int)

        # 快速隨機
        stoch_fast = ta.STOCHF(dataframe, fastk_period=5, fastd_period=3, fastd_matype=0)
        dataframe['fastk'] = stoch_fast['fastk']

        # 通用
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['volume_mean'] = dataframe['volume'].rolling(20).mean()

        return dataframe

    # ------------------------------------------------------------
    # 進場
    # ------------------------------------------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 可優化週期直接於此計算 (不受 20 的倍數限制，避免 V1 的欄位找不到問題)
        ema_trend = ta.EMA(dataframe, timeperiod=self.trend_ema_period.value)

        # Donchian 通道 (shift(1) 避免用到當根 K 線自己)
        dc_n = self.trend_dc_period.value
        dc_high = dataframe['high'].rolling(dc_n).max().shift(1)
        dc_low = dataframe['low'].rolling(dc_n).min().shift(1)

        # ---------- 過濾器 ----------
        vol_ok_trend = dataframe['volume'] > dataframe['volume_mean'] * self.trend_vol_mult.value
        vol_ok_mr = dataframe['volume'] > dataframe['volume_mean'] * 0.5
        vol_alive = dataframe['volume'] > 0
        atr_ok = dataframe['atr_pct'] > self.atr_pct_min.value
        # 先壓縮、後擴張才算有效突破 (而不是行情已經擴張完才追)
        bb_q = dataframe['bb_width'].shift(1).rolling(200).quantile(self.trend_squeeze_q.value)
        bbw_ok = (dataframe['bb_width_prev_min'] < bb_q) & (dataframe['bb_expanding'] == 1)

        # 回歸單的反轉 K 線確認
        bullish_candle = dataframe['close'] > dataframe['open']
        bearish_candle = dataframe['close'] < dataframe['open']

        # 4h 方向 / 強度
        up_4h = (dataframe['close_4h'] > dataframe['ema_200_4h']) & (dataframe['ema_slope_up_4h'] == 1)
        down_4h = (dataframe['close_4h'] < dataframe['ema_200_4h']) & (dataframe['ema_slope_up_4h'] == 0)
        strong_4h = dataframe['adx_4h'] > self.trend_adx_min.value
        range_4h = dataframe['adx_4h'] < self.mr_adx_max.value

        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0

        # ==================== 1. 趨勢突破 ====================
        cond_trend_long = (
            up_4h & strong_4h &
            (dataframe['close'] > ema_trend) &
            (dataframe['close'] > dc_high) &                 # 突破前高
            (dataframe['rsi'] > self.trend_rsi_buy.value) &
            bbw_ok & vol_ok_trend & atr_ok & vol_alive
        )
        cond_trend_short = (
            down_4h & strong_4h &
            (dataframe['close'] < ema_trend) &
            (dataframe['close'] < dc_low) &                  # 跌破前低
            (dataframe['rsi'] < self.trend_rsi_sell.value) &
            bbw_ok & vol_ok_trend & atr_ok & vol_alive
        )

        # ==================== 2. 均值回歸 (僅在 4h 非強趨勢時) ====================
        cond_mr_long = (
            range_4h &
            (dataframe['close'] < dataframe['bb_lowerband']) &
            (dataframe['rsi'] < self.mr_rsi_buy.value) &
            (dataframe['fastk'] < self.mr_stoch_buy.value) &
            bullish_candle &
            vol_ok_mr & atr_ok & vol_alive
        )
        cond_mr_short = (
            range_4h &
            (dataframe['close'] > dataframe['bb_upperband']) &
            (dataframe['rsi'] > self.mr_rsi_sell.value) &
            (dataframe['fastk'] > self.mr_stoch_sell.value) &
            bearish_candle &
            vol_ok_mr & atr_ok & vol_alive
        )

        # 回歸先寫、趨勢後寫 (兩者條件本質互斥，若重疊以趨勢優先)
        dataframe.loc[cond_mr_long, ['enter_long', 'enter_tag']] = (1, 'mr_long')
        dataframe.loc[cond_mr_short, ['enter_short', 'enter_tag']] = (1, 'mr_short')
        dataframe.loc[cond_trend_long, ['enter_long', 'enter_tag']] = (1, 'trend_long')
        dataframe.loc[cond_trend_short, ['enter_short', 'enter_tag']] = (1, 'trend_short')

        # 同根 K 線多空同時成立 → 視為衝突，全部取消
        conflict = (dataframe['enter_long'] == 1) & (dataframe['enter_short'] == 1)
        dataframe.loc[conflict, ['enter_long', 'enter_short']] = 0

        return dataframe

    # ------------------------------------------------------------
    # 出場訊號：反向訊號 = 翻轉 (Flip)
    # 其餘依 tag 的出場放在 custom_exit
    # ------------------------------------------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0

        dataframe.loc[dataframe['enter_short'] == 1, ['exit_long', 'exit_tag']] = (1, 'flip_to_short')
        dataframe.loc[dataframe['enter_long'] == 1, ['exit_short', 'exit_tag']] = (1, 'flip_to_long')
        return dataframe

    # ------------------------------------------------------------
    # 依 enter_tag 的專屬出場
    # ------------------------------------------------------------
    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> Optional[str]:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return None
        candle = dataframe.iloc[-1]
        tag = trade.enter_tag or ''

        # ----- 趨勢單：4h 趨勢失效才主動離場，其餘交給 ATR 追蹤止損 -----
        if tag == 'trend_long':
            if candle['close_4h'] < candle['ema_200_4h']:
                return 'trend_long_4h_break'
        elif tag == 'trend_short':
            if candle['close_4h'] > candle['ema_200_4h']:
                return 'trend_short_4h_break'

        # ----- 回歸單：RSI 過熱 / 回到布林中軌即獲利了結 -----
        elif tag == 'mr_long':
            if candle['rsi'] > self.exit_rsi_long.value:
                return 'mr_long_rsi'
            if current_profit > 0 and candle['close'] >= candle['bb_middleband']:
                return 'mr_long_mid'
        elif tag == 'mr_short':
            if candle['rsi'] < self.exit_rsi_short.value:
                return 'mr_short_rsi'
            if current_profit > 0 and candle['close'] <= candle['bb_middleband']:
                return 'mr_short_mid'

        return None

    # ------------------------------------------------------------
    # ATR 動態追蹤止損 + 利潤鎖定 + 時間止損
    # 回傳值為「相對當前價格的止損距離」，一律透過官方 helper 轉換，
    # 並取最緊者；Freqtrade 本身只會讓止損往有利方向移動 (不會放寬)。
    # ------------------------------------------------------------
    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> Optional[float]:
        tag = trade.enter_tag or ''
        is_short = trade.is_short
        lev = trade.leverage
        dur = (current_time - trade.open_date_utc).total_seconds()

        candidates = []

        # ---------- 1. ATR 追蹤止損 ----------
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is not None and len(dataframe) > 0:
            last = dataframe.iloc[-1]
            if tag.startswith('trend'):
                atr, mult = last.get('atr_4h'), self.atr_mult_trend.value   # 趨勢單：4h ATR
            else:
                atr, mult = last.get('atr'), self.atr_mult_mr.value         # 回歸單：15m ATR
            if pd.notna(atr) and atr > 0:
                stop_price = current_rate + mult * atr if is_short else current_rate - mult * atr
                candidates.append(
                    stoploss_from_absolute(stop_price, current_rate, is_short=is_short, leverage=lev)
                )

        # ---------- 2. 利潤鎖定 / 保本 (以開倉價為基準) ----------
        if current_profit > 0.10:
            candidates.append(stoploss_from_open(0.01, current_profit, is_short=is_short, leverage=lev))
        elif current_profit > 0.05:
            candidates.append(stoploss_from_open(0.0, current_profit, is_short=is_short, leverage=lev))

        # ---------- 3. 時間止損 ----------
        if tag in ('mr_long', 'mr_short'):
            # 回歸單 6 小時仍無 2% 利潤 → 行情停滯，收緊至 -2% (相對開倉價)
            if dur > 6 * 3600 and current_profit < 0.02:
                candidates.append(stoploss_from_open(-0.02, current_profit, is_short=is_short, leverage=lev))
        else:
            # 趨勢單給較多空間：24 小時仍虧損 → 收緊至 -3%
            if dur > 24 * 3600 and current_profit < 0:
                candidates.append(stoploss_from_open(-0.03, current_profit, is_short=is_short, leverage=lev))

        if not candidates:
            return None  # 沿用全局 stoploss

        return min(candidates)