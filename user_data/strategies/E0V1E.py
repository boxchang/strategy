from datetime import datetime, timedelta
import talib.abstract as ta
import pandas_ta as pta
from freqtrade.persistence import Trade
from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
from freqtrade.strategy import DecimalParameter, IntParameter
from functools import reduce
import warnings
from typing import Optional


warnings.simplefilter(action="ignore", category=RuntimeWarning)

# 存放 5m 时间线收盘价格在 ma120 、ma240 支撑位以上的币对列表
# 这里存放的币对将在跌破 ma120 、ma240 支撑位时止损退出
TMP_HOLD = []
TMP_HOLD1 = []


class E0V1E(IStrategy):
    # 默认 roi 无限大，不使用 roi 止盈
    minimal_roi = {
        "0": 1
    }

    # 策略使用 5m 时间线
    timeframe = '5m'

    # 仅在新的蜡烛开始时才会处理开/平仓信号
    process_only_new_candles = True
    
    # 策略至少需要240根 5m 蜡烛才会启动，即 20个小时后才会开始运行
    # 主要是 ma120、ma240 支撑位的计算需要更长时间范围的窗口进行计算
    # 如果之前跑过其他的 5m 策略，这里可以减少一些，有缓存的数据可以拿
    startup_candle_count = 240

    # 订单类型，market市价、limit限价
    order_types = {
        'entry': 'market', # 开仓为市价订单
        'exit': 'market', # 平仓为市价订单
        'emergency_exit': 'market', # 紧急退出为市价订单(创建止损失败时会紧急退出， 通常是急跌时出现)
        'force_entry': 'market',  # tg机器人/web控制 forceentry命令开仓为市价订单
        'force_exit': "market",  # tg机器人/web控制 force_exit命令平仓为市价订单
        'stoploss': 'market', # 市价止损
        'stoploss_on_exchange': False, # 是否自动在交易所设置止损。（默认交易所上是不会显示机器人的止损线，开启后会将机器人止损挂到交易所上显示）
        'stoploss_on_exchange_interval': 60, # 交易所同步机器人止损的频率（秒）
        'stoploss_on_exchange_market_ratio': 0.99
    }

    stoploss = -0.25 # 硬止损，use_custom_stoploss开启后，这个属性将无效
    trailing_stop = False # 移动止损，这里建议关闭，容易回测出现误差
    trailing_stop_positive = 0.002
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    use_custom_stoploss = True # 使用自定义止损

    is_optimize_32 = True # 超优化参数是否启用

    # 快速 rsi 参数
    buy_rsi_fast_32 = IntParameter(20, 70, default=40, space='buy', optimize=is_optimize_32)
    # 慢速 rsi 参数
    buy_rsi_32 = IntParameter(15, 50, default=42, space='buy', optimize=is_optimize_32)
    # sma参数
    buy_sma15_32 = DecimalParameter(0.900, 1, default=0.973, decimals=3, space='buy', optimize=is_optimize_32)
    # cti 参数
    buy_cti_32 = DecimalParameter(-1, 1, default=0.69, decimals=2, space='buy', optimize=is_optimize_32)

    # 卖出 fastk 参数
    sell_fastx = IntParameter(50, 100, default=84, space='sell', optimize=True)

    # cci 止损参数
    cci_opt = False
    sell_loss_cci = IntParameter(low=0, high=600, default=120, space='sell', optimize=cci_opt)
    sell_loss_cci_profit = DecimalParameter(-0.15, 0, default=-0.05, decimals=2, space='sell', optimize=cci_opt)

    leverage_val = IntParameter(1, 20, default=3, space="entry", optimize=True)

    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod", # 交易后冷却时间
                "stop_duration_candles": 18 # 18个 5m 蜡烛后再启动
            }
        ]
    
    # 自定义止损
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        # 当前利润大于 5% 时，利润回吐 0.2% 时止盈
        if current_profit >= 0.05:
            return -0.002
        
        # 如果开仓信号是 buy_new 时，当前利润大于 3% 时，利润回吐 0.3% 时止盈
        if str(trade.enter_tag) == "buy_new" and current_profit >= 0.03:
            return -0.003

        return None

    # 技术指标计算
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # buy_1 indicators
        dataframe['sma_15'] = ta.SMA(dataframe, timeperiod=15) # 15个蜡烛窗口计算 sma
        dataframe['cti'] = pta.cti(dataframe["close"], length=20) # 20个蜡烛窗口计算 cti
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14) # 14个蜡烛窗口计算 rsi
        dataframe['rsi_fast'] = ta.RSI(dataframe, timeperiod=4) # 4个蜡烛窗口计算快速 rsi
        dataframe['rsi_slow'] = ta.RSI(dataframe, timeperiod=20) # 20个蜡烛窗口计算慢速 rsi

        # profit sell indicators
        stoch_fast = ta.STOCHF(dataframe, 5, 3, 0, 3, 0) # STOCHF指标
        dataframe['fastk'] = stoch_fast['fastk'] # fastk越大越容易回调，类似 rsi，大于80开始回调，小于20开始反弹

        dataframe['cci'] = ta.CCI(dataframe, timeperiod=20)

        # ma支撑位
        dataframe['ma120'] = ta.MA(dataframe, timeperiod=120)
        dataframe['ma240'] = ta.MA(dataframe, timeperiod=240)

        return dataframe

    # 开仓信号
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []
        dataframe.loc[:, 'enter_tag'] = ''

        # 同时满足以下条件时，做多
        # 当前慢速rsi小于上个蜡烛的慢速rsi时
        # 当前快速rsi值小于超参数buy_rsi_fast_32的值，默认值40
        # 当前rsi值大于超参数buy_rsi_32的值，默认值42
        # 当前收盘价小于sma_15乘以buy_sma15_32系数，buy_sma15_32默认0.973
        # cti < buy_cti_32
        buy_1 = (
                (dataframe['rsi_slow'] < dataframe['rsi_slow'].shift(1)) &
                (dataframe['rsi_fast'] < self.buy_rsi_fast_32.value) &
                (dataframe['rsi'] > self.buy_rsi_32.value) &
                (dataframe['close'] < dataframe['sma_15'] * self.buy_sma15_32.value) &
                (dataframe['cti'] < self.buy_cti_32.value)
        )

        # buy_new和buy_1一致，只是修改了超参数
        buy_new = (
                (dataframe['rsi_slow'] < dataframe['rsi_slow'].shift(1)) &
                (dataframe['rsi_fast'] < 34) &
                (dataframe['rsi'] > 28) &
                (dataframe['close'] < dataframe['sma_15'] * 0.96) &
                (dataframe['cti'] < self.buy_cti_32.value)
        )


        conditions.append(buy_1)
        # 满足buy_1时，设置开仓信号名称 buy_1
        dataframe.loc[buy_1, 'enter_tag'] += 'buy_1'

        conditions.append(buy_new)
        # 满足buy_new时，设置开仓信号名称 buy_new
        dataframe.loc[buy_new, 'enter_tag'] += 'buy_new'

        # buy_1和buy_new至少满足其中之一时，做多信号
        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x | y, conditions),
                'enter_long'] = 1

        return dataframe

    # 自定义平仓
    def custom_exit(self, pair: str, trade: 'Trade', current_time: 'datetime', current_rate: float,
                    current_profit: float, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)

        # 取当前最后一根蜡烛
        # 这里为了防止前瞻，选择上一根蜡烛
        current_candle = dataframe.iloc[-1].squeeze()
        # 计算蜡烛内的最小利润
        min_profit = trade.calc_profit_ratio(trade.min_rate)
        
        # 如果收盘价在 ma120 和 ma240 支撑位以上，则记录到 TMP_HOLD 中
        if current_candle['close'] > current_candle["ma120"] and current_candle['close'] > current_candle["ma240"]:
            if trade.id not in TMP_HOLD:
                TMP_HOLD.append(trade.id)
        
        # 如果 (开盘价 - ma120) / 开盘价 > 10%，则记录到 TMP_HOLD1 中
        if (trade.open_rate - current_candle["ma120"]) / trade.open_rate >= 0.1:
            if trade.id not in TMP_HOLD1:
                TMP_HOLD1.append(trade.id)
        
        # 如果利润大于0
        if current_profit > 0:
            # fastk大于超参数 sell_fastx 时，平仓
            if current_candle["fastk"] > self.sell_fastx.value:
                return "fastk_profit_sell"
        
        # 如果最小利润小于-10%
        if min_profit <= -0.1:
            # cci > sell_loss_cci 平仓
            if current_profit > self.sell_loss_cci_profit.value:
                if current_candle["cci"] > self.sell_loss_cci.value:
                    return "cci_loss_sell"

        # 收盘价跌破 ma120，平仓
        if trade.id in TMP_HOLD1 and current_candle["close"] < current_candle["ma120"]:
            TMP_HOLD1.remove(trade.id)
            return "ma120_sell_fast"

        # 收盘价跌破 ma120 & ma240，平仓
        if trade.id in TMP_HOLD and current_candle["close"] < current_candle["ma120"] and current_candle["close"] < \
                current_candle["ma240"]:
            if min_profit <= -0.1:
                TMP_HOLD.remove(trade.id)
                return "ma120_sell"

        return None

    # 平仓技术指标
    # 这里不使用计算的平仓信号，使用上文的自定义止损和自定义平仓
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ['exit_long', 'exit_tag']] = (0, 'long_out')
        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                         proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                         side: str, **kwargs) -> float:
                """
                設定交易槓桿倍數
                """
                return float(self.leverage_val.value)