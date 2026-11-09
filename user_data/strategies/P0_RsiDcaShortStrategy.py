# -*- coding: utf-8 -*-
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class P0_RsiDcaShortStrategy(IStrategy):
    """
    RSI 極端值反轉 (支援 Long 與 Short) + 分階段 ROI 停利 + 高槓桿 DCA 加倉策略

    需搭配 config:
        "trading_mode": "futures",
        "margin_mode": "isolated",
        交易對格式: "BTC/USDT:USDT"
    """

    # 基礎設定
    timeframe = '15m'
    startup_candle_count: int = 50

    # 必須宣告，否則 enter_short 訊號會被忽略
    can_short = True

    # 停損設定 (含槓桿的損益比例)
    stoploss = -0.60

    # RSI 門檻（可自行調整；原註解為 10 / 90，原程式為 40 / 60）
    rsi_long_threshold = 20
    rsi_short_threshold = 80

    # 槓桿設定 (20x，並限制不超過交易所該交易對的最大槓桿)
    leverage_value = 20.0

    # 分階段 ROI 停利（key 為開倉後經過的分鐘數，value 為含槓桿的獲利比例）
    minimal_roi = {
        "0": 0.99,
        "3": 0.20,
        "5": 0.15,
        "10": 0.10,
        "60": 0.10,
    }

    # 加倉 (DCA) 設定：最多加倉 3 次，每虧損 -50% 加倉一次
    position_adjustment_enable = True
    max_entry_position_adjustment = 3

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str,
                 side: str, **kwargs) -> float:
        return min(self.leverage_value, max_leverage)

    def custom_stake_amount(self, pair: str, current_time, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            leverage: float, entry_tag: str, side: str,
                            **kwargs) -> float:
        """
        將資金分為 4 等分：1 份初始 + 3 份加倉
        """
        stake = proposed_stake / 4.0
        if min_stake is not None and stake < min_stake:
            return min_stake
        return stake

    def adjust_trade_position(self, trade, current_time, current_rate: float,
                              current_profit: float, min_stake: float, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs):
        """
        加倉邏輯：浮動虧損達 -50% 時加倉一次，最多加 3 次
        """
        # 有未成交訂單時不重複下單
        if trade.has_open_orders:
            return None

        filled_entries = trade.select_filled_orders(trade.entry_side)
        count_of_entries = len(filled_entries)

        # 1 次初始 + 3 次加倉 = 最多 4 筆進場單
        if count_of_entries == 0 or count_of_entries > self.max_entry_position_adjustment:
            return None

        if current_profit <= -0.50:
            # 以第一筆進場的保證金金額加倉
            stake = filled_entries[0].stake_amount
            if max_stake is not None:
                stake = min(stake, max_stake)
            if min_stake is not None and stake < min_stake:
                return None
            return stake

        return None

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(
        self,
        dataframe: DataFrame,
        metadata: dict
    ) -> DataFrame:

        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None

        # Long：RSI 超賣
        long_condition = (
            (dataframe["rsi"] < self.rsi_long_threshold)
            & (dataframe["volume"] > 0)
        )

        # Short：RSI 超買
        short_condition = (
            (dataframe["rsi"] > self.rsi_short_threshold)
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[
            long_condition,
            ["enter_long", "enter_tag"]
        ] = (1, "rsi_oversold_long")

        dataframe.loc[
            short_condition,
            ["enter_short", "enter_tag"]
        ] = (1, "rsi_overbought_short")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 出場交由 ROI 與 Stoploss 處理
        dataframe.loc[:, 'exit_long'] = 0
        dataframe.loc[:, 'exit_short'] = 0
        return dataframe