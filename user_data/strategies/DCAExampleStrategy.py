from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame

class DCAExampleStrategy(IStrategy):
    # 啟用加倉功能
    position_adjustment_enable = True

    # 每次初始下單金額
    stake_amount = 200

    timeframe = '5m'

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 簡單移動平均線示範
        dataframe['ma_fast'] = dataframe['close'].rolling(10).mean()
        dataframe['ma_slow'] = dataframe['close'].rolling(30).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['ma_fast'] > dataframe['ma_slow']),
            'enter_long'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['ma_fast'] < dataframe['ma_slow']),
            'exit_long'
        ] = 1
        return dataframe

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, **kwargs):
        """
        分批加倉邏輯：
        - 第一次：200 USDT（由 stake_amount 控制）
        - 第二次：如果虧損超過 -5%，加倉 100 USDT
        - 第三次：如果虧損超過 -10%，再加倉 50 USDT
        """
        # 已加倉次數
        num_entries = len(trade.orders)

        if num_entries == 1 and current_profit < -0.05:
            return 100  # 第二次加倉
        elif num_entries == 2 and current_profit < -0.10:
            return 50   # 第三次加倉
        else:
            return 0    # 不加倉
