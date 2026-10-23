from datetime import datetime
from typing import Union

import pandas as pd
from pandas import DataFrame
import talib.abstract as ta
import logging

from freqtrade.strategy import (
    IStrategy,
    IntParameter,
)
from freqtrade.persistence import Trade


logger = logging.getLogger(__name__)


class TrendAndMeanReversionFutures(IStrategy):
    """
    EMA Trend + RSI/Bollinger Bands Mean Reversion
    Futures strategy supporting long and short positions.

    Entry tags:
        ema_trend_long
        ema_trend_short
        rsi_mean_long
        rsi_mean_short

    Exit rules:
        EMA:
            - Exit on EMA trend reversal.
            - Exit losing trades after 8 hours.
            - Maximum holding time: 24 hours.

        RSI:
            - Exit when price returns to Bollinger middle band.
            - Exit when RSI returns to 50.
            - Maximum holding time: 48 hours.

    Notes:
        - Built-in ROI exit is disabled.
        - Hard stoploss remains enabled.
        - EMA signals have priority over RSI signals.
        - Conflicting long/short signals are rejected.
    """

    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = True

    # Disable built-in ROI exits.
    # All strategy-specific exits are handled by custom_exit().
    minimal_roi = {}

    # Disable built-in trailing stop.
    trailing_stop = False

    # Retain the original hard stoploss as a safety net.
    stoploss = -0.50
    use_custom_stoploss = False

    startup_candle_count = 100

    # Allow custom_exit to manage exits.
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ==================== Hyperopt Parameters ====================

    ema_fast_p = IntParameter(
        5, 10,
        default=7,
        space="buy",
        optimize=True,
    )

    ema_slow_p = IntParameter(
        20, 30,
        default=25,
        space="buy",
        optimize=True,
    )

    rsi_buy_thresh = IntParameter(
        10, 25,
        default=20,
        space="buy",
        optimize=True,
    )

    rsi_sell_thresh = IntParameter(
        75, 90,
        default=80,
        space="buy",
        optimize=True,
    )

    leverage_level = IntParameter(
        1, 10,
        default=3,
        space="buy",
        optimize=False,
    )

    # ==================== Exit Parameters ====================

    # EMA strategy
    ema_loss_exit_hours = 8
    ema_max_hold_hours = 24

    # RSI mean-reversion strategy
    rsi_max_hold_hours = 48
    rsi_recovery_level = 50

    # ==================== Leverage ====================

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """
        Limit leverage to the configured value and exchange maximum.
        """

        return float(
            min(self.leverage_level.value, max_leverage)
        )

    # ==================== Indicators ====================

    def populate_indicators(
        self,
        dataframe: DataFrame,
        metadata: dict,
    ) -> DataFrame:

        # --------------------------------------------------
        # EMA indicators
        #
        # Calculate all parameter values available to Hyperopt.
        # This allows EMA periods to change during optimization.
        # --------------------------------------------------

        for period in self.ema_fast_p.range:
            dataframe[f"ema_fast_{period}"] = ta.EMA(
                dataframe,
                timeperiod=period,
            )

        for period in self.ema_slow_p.range:
            dataframe[f"ema_slow_{period}"] = ta.EMA(
                dataframe,
                timeperiod=period,
            )

        # RSI
        dataframe["rsi"] = ta.RSI(
            dataframe,
            timeperiod=14,
        )

        # Bollinger Bands
        bollinger = ta.BBANDS(
            dataframe,
            timeperiod=20,
            nbdevup=2.0,
            nbdevdn=2.0,
        )

        dataframe["bb_lowerband"] = bollinger["lowerband"]
        dataframe["bb_middleband"] = bollinger["middleband"]
        dataframe["bb_upperband"] = bollinger["upperband"]

        # Volume filter
        dataframe["volume_mean"] = (
            dataframe["volume"].rolling(20).mean()
        )

        # Weekday filter: Monday to Friday
        dataframe["is_weekday"] = (
            dataframe["date"].dt.dayofweek < 5
        )

        return dataframe

    # ==================== Entry Signals ====================

    def populate_entry_trend(
        self,
        dataframe: DataFrame,
        metadata: dict,
    ) -> DataFrame:

        fast_period = self.ema_fast_p.value
        slow_period = self.ema_slow_p.value

        ema_fast = dataframe[f"ema_fast_{fast_period}"]
        ema_slow = dataframe[f"ema_slow_{slow_period}"]

        # Volume filter
        volume_filter = (
            dataframe["volume"]
            > dataframe["volume_mean"] * 0.2
        )

        weekday_filter = dataframe["is_weekday"]

        # --------------------------------------------------
        # Strategy 1: EMA trend-following entries
        # --------------------------------------------------

        crossed_above_ema = (
            (ema_fast > ema_slow)
            & (
                ema_fast.shift(1)
                <= ema_slow.shift(1)
            )
        )

        crossed_below_ema = (
            (ema_fast < ema_slow)
            & (
                ema_fast.shift(1)
                >= ema_slow.shift(1)
            )
        )

        cond_ema_long = (
            weekday_filter
            & crossed_above_ema
            & volume_filter
        )

        cond_ema_short = (
            weekday_filter
            & crossed_below_ema
            & volume_filter
        )

        # --------------------------------------------------
        # Strategy 2: RSI + Bollinger Bands mean reversion
        # --------------------------------------------------

        cond_rsi_long_raw = (
            weekday_filter
            & (
                dataframe["rsi"]
                < self.rsi_buy_thresh.value
            )
            & (
                dataframe["close"]
                < dataframe["bb_lowerband"]
            )
            & volume_filter
        )

        cond_rsi_short_raw = (
            weekday_filter
            & (
                dataframe["rsi"]
                > self.rsi_sell_thresh.value
            )
            & (
                dataframe["close"]
                > dataframe["bb_upperband"]
            )
            & volume_filter
        )

        # EMA has priority over RSI when both signals
        # point in the same direction.
        cond_rsi_long = (
            cond_rsi_long_raw
            & ~cond_ema_long
        )

        cond_rsi_short = (
            cond_rsi_short_raw
            & ~cond_ema_short
        )

        # --------------------------------------------------
        # Reject conflicting long/short signals
        # --------------------------------------------------

        long_signal = (
            cond_ema_long | cond_rsi_long
        )

        short_signal = (
            cond_ema_short | cond_rsi_short
        )

        conflict = long_signal & short_signal

        cond_ema_long = (
            cond_ema_long & ~conflict
        )

        cond_ema_short = (
            cond_ema_short & ~conflict
        )

        cond_rsi_long = (
            cond_rsi_long & ~conflict
        )

        cond_rsi_short = (
            cond_rsi_short & ~conflict
        )

        # --------------------------------------------------
        # Initialize entry columns
        # --------------------------------------------------

        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None

        # EMA long
        dataframe.loc[
            cond_ema_long,
            "enter_long",
        ] = 1

        dataframe.loc[
            cond_ema_long,
            "enter_tag",
        ] = "ema_trend_long"

        # EMA short
        dataframe.loc[
            cond_ema_short,
            "enter_short",
        ] = 1

        dataframe.loc[
            cond_ema_short,
            "enter_tag",
        ] = "ema_trend_short"

        # RSI long
        dataframe.loc[
            cond_rsi_long,
            "enter_long",
        ] = 1

        dataframe.loc[
            cond_rsi_long,
            "enter_tag",
        ] = "rsi_mean_long"

        # RSI short
        dataframe.loc[
            cond_rsi_short,
            "enter_short",
        ] = 1

        dataframe.loc[
            cond_rsi_short,
            "enter_tag",
        ] = "rsi_mean_short"

        return dataframe

    # ==================== Exit Signals ====================

    def populate_exit_trend(
        self,
        dataframe: DataFrame,
        metadata: dict,
    ) -> DataFrame:
        """
        Leave exit signals unset.
        Strategy-specific exits are handled in custom_exit().
        """

        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0

        return dataframe

    # ==================== Custom Exit ====================

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Union[str, bool]:
        """
        Strategy-specific exit rules.

        EMA:
            1. Exit on trend reversal.
            2. Exit losing trades after 8 hours.
            3. Exit all remaining trades after 24 hours.

        RSI:
            1. Exit when price returns to Bollinger middle band.
            2. Exit when RSI returns to 50.
            3. Exit all remaining trades after 48 hours.
        """

        tag = trade.enter_tag or ""

        # Calculate trade duration
        trade_duration_hours = (
            current_time - trade.open_date_utc
        ).total_seconds() / 3600.0

        # Get analyzed dataframe
        dataframe, _ = self.dp.get_analyzed_dataframe(
            pair,
            self.timeframe,
        )

        if dataframe.empty:
            return False

        last_candle = dataframe.iloc[-1]

        # Avoid exits when indicators are unavailable.
        required_columns = [
            "rsi",
            "bb_middleband",
            "close",
        ]

        if any(
            pd.isna(last_candle[column])
            for column in required_columns
        ):
            return False

        close = last_candle["close"]
        rsi = last_candle["rsi"]
        bb_middle = last_candle["bb_middleband"]

        # Read the EMA periods selected by the strategy.
        fast_period = self.ema_fast_p.value
        slow_period = self.ema_slow_p.value

        fast_column = f"ema_fast_{fast_period}"
        slow_column = f"ema_slow_{slow_period}"

        if (
            fast_column not in dataframe.columns
            or slow_column not in dataframe.columns
        ):
            return False

        ema_fast = last_candle[fast_column]
        ema_slow = last_candle[slow_column]

        if pd.isna(ema_fast) or pd.isna(ema_slow):
            return False

        # ==================================================
        # Strategy 1: EMA trend-following exits
        # ==================================================

        if tag == "ema_trend_long":

            # 1. Trend reversal
            if ema_fast < ema_slow:
                return "ema_reversal_exit_long"

            # 2. Losing trade held longer than 8 hours
            if (
                trade_duration_hours
                >= self.ema_loss_exit_hours
                and current_profit < 0
            ):
                return "ema_time_stop_loss_8h"

            # 3. Maximum holding time: 24 hours
            if (
                trade_duration_hours
                >= self.ema_max_hold_hours
            ):
                return "ema_max_hold_24h"

            return False

        if tag == "ema_trend_short":

            # 1. Trend reversal
            if ema_fast > ema_slow:
                return "ema_reversal_exit_short"

            # 2. Losing trade held longer than 8 hours
            if (
                trade_duration_hours
                >= self.ema_loss_exit_hours
                and current_profit < 0
            ):
                return "ema_time_stop_loss_8h"

            # 3. Maximum holding time: 24 hours
            if (
                trade_duration_hours
                >= self.ema_max_hold_hours
            ):
                return "ema_max_hold_24h"

            return False

        # ==================================================
        # Strategy 2: RSI mean-reversion exits
        # ==================================================

        if tag == "rsi_mean_long":

            # 1. Price returns to or above the middle band
            if close >= bb_middle:
                return "rsi_mean_target_long"

            # 2. RSI has recovered from oversold conditions
            if rsi >= self.rsi_recovery_level:
                return "rsi_recovery_exit_long"

            # 3. Maximum holding time: 48 hours
            if (
                trade_duration_hours
                >= self.rsi_max_hold_hours
            ):
                return "rsi_max_hold_48h"

            return False

        if tag == "rsi_mean_short":

            # 1. Price returns to or below the middle band
            if close <= bb_middle:
                return "rsi_mean_target_short"

            # 2. RSI has recovered from overbought conditions
            if rsi <= self.rsi_recovery_level:
                return "rsi_recovery_exit_short"

            # 3. Maximum holding time: 48 hours
            if (
                trade_duration_hours
                >= self.rsi_max_hold_hours
            ):
                return "rsi_max_hold_48h"

            return False

        # Unknown or missing entry tag
        return False
