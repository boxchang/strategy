import logging
from datetime import datetime

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IStrategy,
    IntParameter,
    merge_informative_pair,
)

logger = logging.getLogger(__name__)


class RSI20LongStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False
    startup_candle_count = 30

    tsm_pair = "TSM/USDT:USDT"
    tsm_long_max_price = DecimalParameter(
        low=0.0,
        high=1000.0,
        default=370.0,
        decimals=2,
        space="buy",
        optimize=False,
    )

    leverage_value = 7.0
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

            leverage = RSI20LongStrategy.leverage_value
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
    dca_price_factor = DecimalParameter(
        low=0.975,
        high=0.995,
        default=0.976,
        decimals=3,
        space="dca",
        optimize=True,
    )
    dca_stake_amount = 100.0

    def informative_pairs(self) -> list[tuple[str, str]]:
        return [(self.tsm_pair, self.timeframe)]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)

        tsm_dataframe = self.dp.get_pair_dataframe(
            pair=self.tsm_pair, timeframe=self.timeframe
        )
        return merge_informative_pair(
            dataframe,
            tsm_dataframe[["date", "close"]],
            self.timeframe,
            self.timeframe,
            ffill=True,
        )

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["rsi"] < self.entry_rsi.value)
            & (dataframe["volume"] > 0)
            & (dataframe["close_15m"] < self.tsm_long_max_price.value),
            ["enter_long", "enter_tag"],
        ] = (1, f"rsi_below_{self.entry_rsi.value}")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

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
        if side != "long" or leverage <= 0:
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
        return self.leverage_value

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
        return "2.8"
