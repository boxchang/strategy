from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import logging
import yfinance as yf
import talib.abstract as ta
from datetime import datetime
from typing import Optional

class AnchorRSIStrategy(IStrategy):
    logger = logging.getLogger(__name__)
    position_adjustment_enable = False
    timeframe = '5m'
    leverage_value = 10.0

    can_short = True   # ★ 開啟做空功能

    def __init__(self):
        super().__init__()
        # # Binance 交易對 ↔ 美股代碼對應表
        # self.stock_mapping = {
        #     "NVDAUSDT": "NVDA",
        #     "TSMUSDT": "TSM",
        #     "MUUSDT": "MU",
        #     "AAPLUSDT": "AAPL",
        #     "MSFTUSDT": "MSFT",
        # }
        #
        # # 初始化時抓取收盤價
        # self.last_closes = {}
        # for pair, stock_symbol in self.stock_mapping.items():
        #     ticker = yf.Ticker(stock_symbol)
        #     close = ticker.history(period="1d")['Close'].iloc[-1]
        #     self.last_closes[pair] = close
        #     self.logger.info(f"{pair} 對應 {stock_symbol} 收盤價: {close}")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        self.logger.info(f"Running populate_entry_trend for {metadata['pair']}")
        pair = metadata['pair']
        self.logger.info(f"{pair} pair name")
        # if pair in self.last_closes:
        #     ref_close = self.last_closes[pair]
        #     self.logger.info(f"{pair} 最新 RSI: {dataframe['rsi'].iloc[-1]}, 參考收盤價: {ref_close}")
        #
        #     dataframe.loc[
        #         (dataframe['rsi'] < 30) & (dataframe['close'] < ref_close * 0.95),
        #         'enter_long'
        #     ] = 1
        dataframe.loc[
                (dataframe['rsi'] < 30),
                'enter_long'
        ] = 1

        return dataframe

        # dataframe.loc[
        #     (dataframe['rsi'] < 20) &
        #     (dataframe['ma7'] < dataframe['ma20']),
        #     'enter_short'
        # ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 計算獲利百分比
        dataframe['profit_pct'] = (dataframe['close'] - dataframe['open']) / dataframe['open'] * 100

        # 獲利超過 10% 就出場
        dataframe.loc[
            (dataframe['profit_pct'] > 10),
            'exit_long'
        ] = 1

        return dataframe

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, **kwargs):
        # 初始化 user_data 裡的加碼次數
        if "dca_count" not in trade.user_data:
            trade.user_data["dca_count"] = 0

        drop_pct = (current_rate - trade.open_rate) / trade.open_rate * 100

        if drop_pct <= -20 and trade.user_data["dca_count"] < 5:
            trade.user_data["dca_count"] += 1
            self.logger.info(
                f"{trade.pair} 跌超過 20%，第 {trade.user_data['dca_count']} 次加碼 50 USDT"
            )

            # 回傳加碼金額
            return 50

        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        # 回傳槓桿倍數
        return self.leverage_value