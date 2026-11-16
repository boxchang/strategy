import logging
from datetime import datetime

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IStrategy,
    IntParameter,
    stoploss_from_absolute,
)

logger = logging.getLogger(__name__)


class RSI20LongStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False
    startup_candle_count = 30

    minimal_roi = {}
    # Hard cap: a 2% price stop at 20x corresponds to 40% stake loss.
    stoploss = -0.40
    use_custom_stoploss = True
    trailing_stop = False
    position_adjustment_enable = True
    max_entry_position_adjustment = 3

    rsi_period = 14
    entry_rsi = IntParameter(
        low=10, high=50, default=20, space="buy", optimize=True
    )
    dca_loss_threshold = DecimalParameter(
        low=0.005,
        high=0.025,
        default=0.02,
        decimals=3,
        space="dca",
        optimize=True,
    )
    leverage_value = IntParameter(
        low=1, high=20, default=1, space="leverage", optimize=True
    )
    stoploss_price_pct = DecimalParameter(
        low=0.005,
        high=0.02,
        default=0.02,
        decimals=3,
        space="price_sl",
        optimize=True,
    )
    target_notional = 100.0
    dca_notional = 100.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["rsi"] < self.entry_rsi.value) & (dataframe["volume"] > 0),
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

        stake = self.target_notional / leverage
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
        return min(self.leverage_value.value, max_leverage)

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        # Do not expose after_fill: DCA must not widen an already-set stop.
        stop_rate = trade.open_rate * (1 - self.stoploss_price_pct.value)
        return stoploss_from_absolute(
            stop_rate,
            current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

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
            or trade.leverage <= 0
        ):
            return None

        average_entry_price = trade.open_rate
        if not average_entry_price:
            return None

        loss_threshold = self.dca_loss_threshold.value
        trigger_rate = average_entry_price * (
            1 - loss_threshold / trade.leverage
        )
        if current_profit > -loss_threshold:
            return None

        dca_stake = self.dca_notional / trade.leverage
        if (
            min_stake is not None
            and dca_stake < min_stake
        ) or dca_stake > max_stake:
            return None

        logger.info(
            "DCA trigger: pair=%s, add=%d/%d, rate=%.8f, "
            "approx_trigger=%.8f, average_entry=%.8f, "
            "loss_threshold=%.2f%%, leverage=%.1fx, profit=%.2f%%, "
            "notional=%.2f, stake=%.2f, time=%s",
            trade.pair,
            additional_entries + 1,
            self.max_entry_position_adjustment,
            current_rate,
            trigger_rate,
            average_entry_price,
            loss_threshold * 100,
            trade.leverage,
            current_profit * 100,
            self.dca_notional,
            dca_stake,
            current_time.isoformat(),
        )
        return dca_stake

    def version(self) -> str:
        return "2.5"
