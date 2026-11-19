import logging
from datetime import datetime

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IStrategy,
    IntParameter,
)

logger = logging.getLogger(__name__)


class RSI20LongShortTSMStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = True
    startup_candle_count = 30

    leverage_value = 8.0
    base_minimal_roi = 0.03
    base_stoploss = -0.1
    minimal_roi = {"0": base_minimal_roi * leverage_value}
    stoploss = base_stoploss * leverage_value
    trailing_stop = False
    position_adjustment_enable = True
    max_entry_position_adjustment = 10

    class HyperOpt:
        @staticmethod
        def stoploss_space():
            from freqtrade.optimize.space import SKDecimal

            leverage = RSI20LongShortTSMStrategy.leverage_value
            return [
                SKDecimal(
                    -0.08 * leverage,
                    -0.02 * leverage,
                    decimals=3,
                    name="stoploss",
                )
            ]

    rsi_period = 14
    entry_rsi = IntParameter(
        low=10, high=50, default=20, space="buy", optimize=True
    )
    short_entry_rsi = IntParameter(
        low=80, high=95, default=80, space="buy", optimize=True
    )
    unprofitable_exit_hours = IntParameter(
        low=4, high=24, default=8, space="sell", optimize=True
    )
    dca_price_factor = DecimalParameter(
        low=0.975,
        high=0.995,
        default=0.976,
        decimals=3,
        space="dca",
        optimize=True,
    )
    dca_stake_amount = 100.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        has_volume = dataframe["volume"] > 0

        long_condition = (
            (dataframe["rsi"] < self.entry_rsi.value) & has_volume
        )
        short_condition = (
            (dataframe["rsi"] > self.short_entry_rsi.value) & has_volume
        )

        dataframe.loc[
            long_condition, ["enter_long", "enter_tag"]
        ] = (1, f"rsi_below_{self.entry_rsi.value}")
        dataframe.loc[
            short_condition, ["enter_short", "enter_tag"]
        ] = (1, f"rsi_above_{self.short_entry_rsi.value}")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        trade_duration_hours = (
            current_time - trade.open_date_utc
        ).total_seconds() / 3600
        if (
            trade_duration_hours > self.unprofitable_exit_hours.value
            and current_profit <= 0
        ):
            return "unprofitable_time_exit"
        return None

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        if side not in {"long", "short"} or leverage <= 0:
            return 0.0

        stake = proposed_stake
        if (min_stake is not None and stake < min_stake) or stake > max_stake:
            return 0.0

        return stake

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
        return min(self.leverage_value, max_leverage)

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None:
        if trade.has_open_orders:
            return None

        filled_entries = trade.select_filled_orders(trade.entry_side)
        additional_entries = len(filled_entries) - 1
        if (
            trade.is_short
            or additional_entries < 0
            or additional_entries >= self.max_entry_position_adjustment
        ):
            return None

        average_entry_price = trade.open_rate
        if not average_entry_price:
            return None

        trigger_rate = average_entry_price * self.dca_price_factor.value
        if current_rate > trigger_rate:
            return None
        if (
            min_stake is not None
            and self.dca_stake_amount < min_stake
        ) or self.dca_stake_amount > max_stake:
            return None

        logger.info(
            "DCA trigger: pair=%s, add=%d/%d, rate=%.8f, trigger=%.8f, "
            "average_entry=%.8f, factor=%.3f, profit=%.2f%%, stake=%.2f, time=%s",
            trade.pair,
            additional_entries + 1,
            self.max_entry_position_adjustment,
            current_rate,
            trigger_rate,
            average_entry_price,
            self.dca_price_factor.value,
            current_profit * 100,
            self.dca_stake_amount,
            current_time.isoformat(),
        )
        return self.dca_stake_amount

    def version(self) -> str:
        return "1.3"
