# Strategy version: v16

import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import (IStrategy, IntParameter, DecimalParameter,
                                stoploss_from_open)
from freqtrade.persistence import Trade


class TrendPullbackStrategy(IStrategy):
    version = "v16"

    timeframe = '1h'
    can_short = True
    startup_candle_count = 250

    trailing_stop = False
    use_custom_stoploss = True
    stoploss = -0.10

    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    minimal_roi = {
        "0": 10
    }

    leverage_level = IntParameter(1, 3, default=1, space='buy', optimize=False)
    rsi_long_max = IntParameter(40, 60, default=52, space='buy', optimize=True)
    rsi_short_min = IntParameter(40, 60, default=48, space='buy', optimize=True)
    adx_min = IntParameter(12, 30, default=18, space='buy', optimize=True)
    atr_mult = DecimalParameter(1.5, 4.0, default=2.0, decimals=1, space='sell', optimize=True)
    tp_r = DecimalParameter(1.5, 5.0, default=2.6, decimals=1, space='sell', optimize=True)

    _r_cache: dict = {}

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 48,
                "trade_limit": 4,
                "stop_duration_candles": 8,
                "only_per_pair": False,
            },
        ]

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: Optional[str], side: str, **kwargs) -> float:
        return float(min(self.leverage_level.value, max_leverage))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['ema_trend'] = ta.EMA(dataframe, timeperiod=200)

        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lowerband'] = bollinger['lowerband']
        dataframe['bb_upperband'] = bollinger['upperband']
        dataframe['bb_middleband'] = bollinger['middleband']

        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_pct'] = dataframe['atr'] / dataframe['close']

        stoch_fast = ta.STOCHF(dataframe, fastk_period=5, fastd_period=3, fastd_matype=0)
        dataframe['fastk'] = stoch_fast['fastk']
        dataframe['fastd'] = stoch_fast['fastd']

        dataframe['ema_fast_slope'] = (
            (dataframe['ema_fast'] - dataframe['ema_fast'].shift(6)) / dataframe['close']
        )
        dataframe['ema_trend_slope'] = (
            (dataframe['ema_trend'] - dataframe['ema_trend'].shift(12)) / dataframe['close']
        )
        dataframe['ema_gap'] = (dataframe['ema_fast'] - dataframe['ema_trend']) / dataframe['ema_trend']

        dataframe['vol_mean'] = dataframe['volume'].rolling(20).mean()
        dataframe['body'] = (dataframe['close'] - dataframe['open']).abs()

        dataframe['touch_lower'] = (
            (dataframe['low'] <= dataframe['bb_lowerband'] * 1.03)
            .astype(float).rolling(12).max().fillna(0)
        )
        dataframe['touch_upper'] = (
            (dataframe['high'] >= dataframe['bb_upperband'] * 0.97)
            .astype(float).rolling(12).max().fillna(0)
        )
        dataframe['rsi_min_recent'] = dataframe['rsi'].rolling(12).min()
        dataframe['rsi_max_recent'] = dataframe['rsi'].rolling(12).max()

        dataframe['touch_ema_low'] = (
            (dataframe['low'] <= dataframe['ema_fast'] * 1.01)
            .astype(float).rolling(8).max().fillna(0)
        )
        dataframe['touch_ema_high'] = (
            (dataframe['high'] >= dataframe['ema_fast'] * 0.99)
            .astype(float).rolling(8).max().fillna(0)
        )

        cross_up = (
            (dataframe['fastk'] > dataframe['fastd']) &
            (dataframe['fastk'].shift(1) <= dataframe['fastd'].shift(1))
        ).astype(float)
        cross_down = (
            (dataframe['fastk'] < dataframe['fastd']) &
            (dataframe['fastk'].shift(1) >= dataframe['fastd'].shift(1))
        ).astype(float)
        dataframe['stoch_cross_up'] = cross_up.rolling(5).max().fillna(0)
        dataframe['stoch_cross_down'] = cross_down.rolling(5).max().fillna(0)

        dataframe['high_recent'] = dataframe['high'].rolling(24).max()
        dataframe['low_recent'] = dataframe['low'].rolling(24).min()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        dataframe['enter_tag'] = ''

        vol_ok = (dataframe['volume'] > 0) & (dataframe['volume'] > dataframe['vol_mean'] * 0.5)
        vol_atr_ok = (dataframe['atr_pct'] > 0.003) & (dataframe['atr_pct'] < 0.04)
        decent_body = dataframe['body'] > dataframe['atr'] * 0.1
        not_chasing_long = dataframe['close'] < dataframe['ema_fast'] + dataframe['atr'] * 2.5
        not_chasing_short = dataframe['close'] > dataframe['ema_fast'] - dataframe['atr'] * 2.5

        pullback_long = (dataframe['high_recent'] - dataframe['close']) > dataframe['atr'] * 1.0
        pullback_short = (dataframe['close'] - dataframe['low_recent']) > dataframe['atr'] * 1.0

        trend_long = (
            (dataframe['close'] > dataframe['ema_trend']) &
            (dataframe['ema_fast'] > dataframe['ema_trend']) &
            (dataframe['ema_gap'] > 0.002) &
            (dataframe['ema_fast_slope'] > 0.0001) &
            (dataframe['ema_trend_slope'] > 0.0) &
            (dataframe['adx'] > self.adx_min.value) &
            (dataframe['plus_di'] > dataframe['minus_di']) &
            vol_atr_ok &
            vol_ok
        )

        trend_short = (
            (dataframe['close'] < dataframe['ema_trend']) &
            (dataframe['ema_fast'] < dataframe['ema_trend']) &
            (dataframe['ema_gap'] < -0.002) &
            (dataframe['ema_fast_slope'] < -0.0001) &
            (dataframe['ema_trend_slope'] < 0.0) &
            (dataframe['adx'] > self.adx_min.value) &
            (dataframe['minus_di'] > dataframe['plus_di']) &
            vol_atr_ok &
            vol_ok
        )

        cond_long_bb = (
            trend_long &
            (dataframe['touch_lower'] > 0) &
            (dataframe['rsi_min_recent'] < self.rsi_long_max.value) &
            (dataframe['rsi'] > dataframe['rsi'].shift(1)) &
            (dataframe['rsi'] > 36) &
            (dataframe['rsi'] < 60) &
            (dataframe['stoch_cross_up'] > 0) &
            (dataframe['close'] > dataframe['open']) &
            (dataframe['close'] > dataframe['close'].shift(1)) &
            decent_body &
            (dataframe['close'] < dataframe['bb_middleband'])
        )

        cond_short_bb = (
            trend_short &
            (dataframe['touch_upper'] > 0) &
            (dataframe['rsi_max_recent'] > self.rsi_short_min.value) &
            (dataframe['rsi'] < dataframe['rsi'].shift(1)) &
            (dataframe['rsi'] < 64) &
            (dataframe['rsi'] > 40) &
            (dataframe['stoch_cross_down'] > 0) &
            (dataframe['close'] < dataframe['open']) &
            (dataframe['close'] < dataframe['close'].shift(1)) &
            decent_body &
            (dataframe['close'] > dataframe['bb_middleband'])
        )

        cond_long_ema = (
            trend_long &
            (dataframe['touch_ema_low'] > 0) &
            pullback_long &
            (dataframe['close'] > dataframe['ema_fast']) &
            not_chasing_long &
            (dataframe['close'] > dataframe['open']) &
            decent_body &
            (dataframe['close'] > dataframe['close'].shift(1)) &
            (dataframe['rsi'] > 40) &
            (dataframe['rsi'] < 62) &
            (dataframe['rsi'] > dataframe['rsi'].shift(1)) &
            (dataframe['stoch_cross_up'] > 0) &
            (dataframe['close'] < dataframe['bb_upperband'])
        )

        cond_short_ema = (
            trend_short &
            (dataframe['touch_ema_high'] > 0) &
            pullback_short &
            (dataframe['close'] < dataframe['ema_fast']) &
            not_chasing_short &
            (dataframe['close'] < dataframe['open']) &
            decent_body &
            (dataframe['close'] < dataframe['close'].shift(1)) &
            (dataframe['rsi'] < 60) &
            (dataframe['rsi'] > 38) &
            (dataframe['rsi'] < dataframe['rsi'].shift(1)) &
            (dataframe['stoch_cross_down'] > 0) &
            (dataframe['close'] > dataframe['bb_lowerband'])
        )

        dataframe.loc[cond_long_ema, 'enter_long'] = 1
        dataframe.loc[cond_long_ema, 'enter_tag'] = 'ema_bounce_long'
        dataframe.loc[cond_short_ema, 'enter_short'] = 1
        dataframe.loc[cond_short_ema, 'enter_tag'] = 'ema_bounce_short'

        dataframe.loc[cond_long_bb, 'enter_long'] = 1
        dataframe.loc[cond_long_bb, 'enter_tag'] = 'bb_pullback_long'
        dataframe.loc[cond_short_bb, 'enter_short'] = 1
        dataframe.loc[cond_short_bb, 'enter_tag'] = 'bb_pullback_short'

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    def _stop_distance(self, pair: str, trade: Trade) -> float:
        key = (pair, trade.id, self.atr_mult.value)
        cached = self._r_cache.get(key)
        if cached is not None:
            return cached

        default_dist = 0.03
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            open_dt = pd.Timestamp(trade.open_date_utc)
            candles = df[df['date'] < open_dt]
            if len(candles) == 0:
                return default_dist
            row = candles.iloc[-1]
            atr = row['atr']
            price = row['close']
            if not np.isfinite(atr) or not np.isfinite(price) or price <= 0:
                return default_dist
            dist = float(np.clip(self.atr_mult.value * atr / price, 0.015, 0.05))
            if len(self._r_cache) > 5000:
                self._r_cache.clear()
            self._r_cache[key] = dist
            return dist
        except Exception:
            return default_dist

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float,
                        after_fill: bool = False, **kwargs) -> Optional[float]:
        lev = trade.leverage if trade.leverage else 1.0
        R = self._stop_distance(pair, trade) * lev

        if current_profit >= 2.0 * R:
            lock = max(0.8 * R, current_profit - 1.0 * R)
        elif current_profit >= 1.5 * R:
            lock = 0.5 * R
        elif current_profit >= 1.1 * R:
            lock = 0.1 * R
        else:
            lock = -R

        try:
            return stoploss_from_open(lock, current_profit,
                                      is_short=trade.is_short, leverage=lev)
        except Exception:
            return None

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        lev = trade.leverage if trade.leverage else 1.0
        R = self._stop_distance(pair, trade) * lev

        if current_profit >= self.tp_r.value * R:
            return 'take_profit_r'

        hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if hours > 48 and current_profit < 0.2 * R:
            return 'time_stop'
        if hours > 96:
            return 'max_hold'
        return None