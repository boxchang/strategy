
# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file

import logging
from datetime import datetime, timedelta
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade


logger = logging.getLogger(__name__)


class P0_KdDcaShortStrategy(IStrategy):

    INTERFACE_VERSION = 3

    # =========================================================
    # 基本設定
    # =========================================================

    timeframe = "15m"
    can_short = True
    startup_candle_count = 50

    process_only_new_candles = True

    # 不使用 ROI，改由 custom_exit 控制出場
    minimal_roi = {}

    # 最大虧損保護
    stoploss = -0.60

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    use_custom_exit = True

    # 關閉加倉
    position_adjustment_enable = False

    # =========================================================
    # KD / RSI 設定
    # =========================================================

    kd_buy_level = 20
    kd_sell_level = 80

    rsi_period = 14
    rsi_short_threshold = 30
    rsi_long_threshold = 70

    # =========================================================
    # 交易設定
    # =========================================================

    leverage_value = 20.0

    # 止盈 3%
    profit_target = 0.03

    # 持倉最多 3 分鐘
    max_hold_minutes = 15

    # 虧損 Pair 冷卻 60 分鐘
    cooldown_minutes = 60

    # =========================================================
    # 槓桿設定
    # =========================================================

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs
    ) -> float:

        return min(self.leverage_value, max_leverage)

    # =========================================================
    # 指標計算
    # =========================================================

    def populate_indicators(
        self,
        dataframe: DataFrame,
        metadata: dict
    ) -> DataFrame:

        # KD
        stoch = ta.STOCH(
            dataframe,
            fastk_period=14,
            slowk_period=3,
            slowk_matype=0,
            slowd_period=3,
            slowd_matype=0
        )

        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # RSI
        dataframe["rsi"] = ta.RSI(
            dataframe,
            timeperiod=self.rsi_period
        )

        return dataframe

    # =========================================================
    # 進場訊號
    # =========================================================

    def populate_entry_trend(
        self,
        dataframe: DataFrame,
        metadata: dict
    ) -> DataFrame:

        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0

        dataframe["enter_tag"] = None


        long_condition = (
            (dataframe["slowk"] < self.kd_buy_level)
            &
            (dataframe["slowk"] > dataframe["slowd"])
            &
            (
                dataframe["slowk"].shift(1)
                <= dataframe["slowd"].shift(1)
            )
            &
            (dataframe["rsi"] < self.rsi_short_threshold)
        )

        short_condition = (
            (dataframe["slowk"] > self.kd_sell_level)
            &
            (dataframe["slowk"] < dataframe["slowd"])
            &
            (
                dataframe["slowk"].shift(1)
                >= dataframe["slowd"].shift(1)
            )
            &
            (dataframe["rsi"] > self.rsi_long_threshold)
        )

        dataframe.loc[
            short_condition,
            ["enter_short", "enter_tag"]
        ] = (1, "kd_reverse_short_rsi")

        dataframe.loc[
            long_condition,
            ["enter_long", "enter_tag"]
        ] = (1, "kd_reverse_long_rsi")

        return dataframe

    # =========================================================
    # 出場訊號
    # =========================================================

    def populate_exit_trend(
        self,
        dataframe: DataFrame,
        metadata: dict
    ) -> DataFrame:

        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0

        return dataframe

    # =========================================================
    # 自訂出場
    # =========================================================

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs
    ) -> Optional[str]:

        # 優先判斷是否達到 3% 獲利
        if current_profit >= self.profit_target:
            return "profit_3_percent"

        # 持倉滿 3 分鐘，無論盈虧都平倉
        hold_time = current_time - trade.open_date_utc

        if hold_time >= timedelta(
            minutes=self.max_hold_minutes
        ):
            return "time_exit"

        return None

    # =========================================================
    # 虧損 Pair 冷卻機制
    # =========================================================

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs
    ) -> bool:

        # 取得該 Pair 已平倉交易紀錄
        closed_trades = Trade.get_trades_proxy(
            pair=pair,
            is_open=False
        )

        if not closed_trades:
            return True

        # 找出最近一次平倉的交易
        latest_trade = max(
            closed_trades,
            key=lambda trade: trade.close_date
            or datetime.min.replace(tzinfo=current_time.tzinfo)
        )

        # 最近一筆交易虧損才啟動冷卻
        if (
            latest_trade.close_profit is not None
            and latest_trade.close_profit < 0
            and latest_trade.close_date is not None
        ):

            cooldown_end = (
                latest_trade.close_date
                + timedelta(minutes=self.cooldown_minutes)
            )

            if current_time < cooldown_end:

                remaining = cooldown_end - current_time

                logger.info(
                    f"{pair} 虧損冷卻中，"
                    f"剩餘時間：{remaining}，"
                    f"禁止重新進場。"
                )

                return False

        return True
