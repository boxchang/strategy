# Version: v3.1 (TrendMeanReversionLX with Detailed VN Exit Logging)
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Optional
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    DecimalParameter,
    CategoricalParameter,
    stoploss_from_open,
)
from freqtrade.persistence import Trade
import logging

logger = logging.getLogger(__name__)

# 越南時間 UTC+7
VN_TZ = timezone(timedelta(hours=7))


class TrendMeanReversionLX(IStrategy):
    timeframe = '15m'
    can_short = True

    startup_candle_count = 320

    use_custom_stoploss = True
    stoploss = -0.20

    minimal_roi = {
        "0": 0.06,
        "60": 0.035,
        "180": 0.018,
        "480": 0.005
    }

    # =========================
    # 📝 Log 顯示模式開關說明：
    # 選項：
    #   "loss"   -> 只顯示賠錢交易的出場紀錄
    #   "profit" -> 只顯示賺錢交易的出場紀錄
    #   "both"   -> 顯示所有交易紀錄 (賺錢與賠錢)
    #   None     -> 完全關閉 Log 輸出 (跑 Hyperopt 時建議設為 None)
    # =========================
    log_mode = "loss"

    leverage_level = IntParameter(1, 10, default=2, space='buy', optimize=False)

    trend_ema_period = CategoricalParameter(
        list(range(100, 301, 20)), default=220, space='buy', optimize=True
    )
    trend_adx_min = IntParameter(15, 35, default=25, space='buy', optimize=True)
    trend_rsi_buy = IntParameter(50, 65, default=52, space='buy', optimize=True)
    trend_rsi_sell = IntParameter(35, 50, default=48, space='buy', optimize=True)
    trend_slope_min = DecimalParameter(0.0, 0.01, default=0.001, decimals=4, space='buy', optimize=True)

    mr_rsi_buy = IntParameter(15, 35, default=30, space='buy', optimize=True)
    mr_stoch_buy = IntParameter(10, 30, default=19, space='buy', optimize=True)
    mr_rsi_sell = IntParameter(65, 85, default=70, space='buy', optimize=True)
    mr_stoch_sell = IntParameter(70, 90, default=81, space='buy', optimize=True)

    exit_rsi_long = IntParameter(65, 85, default=80, space='sell', optimize=True)
    exit_rsi_short = IntParameter(15, 35, default=25, space='sell', optimize=True)

    sl_base = DecimalParameter(0.03, 0.15, default=0.06, decimals=2, space='sell', optimize=True)
    sl_atr_mult = DecimalParameter(1.5, 4.0, default=2.5, decimals=1, space='sell', optimize=True)
    lock_high_profit = DecimalParameter(0.08, 0.20, default=0.08, decimals=2, space='sell', optimize=True)
    lock_high_sl = DecimalParameter(0.005, 0.05, default=0.03, decimals=3, space='sell', optimize=True)
    lock_mid_profit = DecimalParameter(0.02, 0.08, default=0.04, decimals=2, space='sell', optimize=True)
    lock_mid_sl = DecimalParameter(0.0, 0.02, default=0.01, decimals=3, space='sell', optimize=True)

    mr_hold_hours = IntParameter(2, 12, default=8, space='sell', optimize=True)
    mr_min_profit = DecimalParameter(0.005, 0.04, default=0.01, decimals=3, space='sell', optimize=True)

    gen_hold_hours = IntParameter(6, 24, default=15, space='sell', optimize=True)

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: Optional[str], side: str, **kwargs) -> float:
        return float(min(self.leverage_level.value, max_leverage))

    # =========================
    # 時間轉換工具（轉換至越南時間 UTC+7）
    # =========================
    @staticmethod
    def format_vn_time(value) -> str:
        if value is None:
            return "N/A"
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        return timestamp.tz_convert(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for val in range(100, 301, 20):
            dataframe[f'ema_{val}'] = ta.EMA(dataframe, timeperiod=val)
            dataframe[f'ema_{val}_slope'] = (
                dataframe[f'ema_{val}'] / dataframe[f'ema_{val}'].shift(16) - 1.0
            )

        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)

        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lowerband'] = bollinger['lowerband']
        dataframe['bb_upperband'] = bollinger['upperband']

        stoch_fast = ta.STOCHF(dataframe, 5, 3, 0, 3, 0)
        dataframe['fastk'] = stoch_fast['fastk']

        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['volume_mean'] = dataframe['volume'].rolling(20).mean()

        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_mean'] = dataframe['atr'].rolling(20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        period = self.trend_ema_period.value
        ema_col = f'ema_{period}'
        slope_col = f'ema_{period}_slope'
        if ema_col not in dataframe.columns:
            ema_col = 'ema_200'
            slope_col = 'ema_200_slope'

        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        dataframe['enter_tag'] = None

        vol_filter = (dataframe['volume'] > dataframe['volume_mean'] * 0.3)
        not_volume_spike = dataframe['volume'] <= (dataframe['volume_mean'] * 3.0)
        not_atr_spike = dataframe['atr'] <= (dataframe['atr_mean'] * 1.5)

        uptrend = (
            (dataframe['close'] > dataframe[ema_col]) &
            (dataframe['ema_fast'] > dataframe[ema_col]) &
            (dataframe[slope_col] > self.trend_slope_min.value)
        )
        downtrend = (
            (dataframe['close'] < dataframe[ema_col]) &
            (dataframe['ema_fast'] < dataframe[ema_col]) &
            (dataframe[slope_col] < -self.trend_slope_min.value)
        )

        rsi_prev = dataframe['rsi'].shift(1)

        cond_trend_long = (
            uptrend &
            (dataframe['adx'] > self.trend_adx_min.value) &
            (dataframe['plus_di'] > dataframe['minus_di']) &
            (rsi_prev <= self.trend_rsi_buy.value) &
            (dataframe['rsi'] > self.trend_rsi_buy.value) &
            (dataframe['close'] > dataframe['ema_fast']) &
            vol_filter &
            not_volume_spike &
            not_atr_spike
        )

        cond_trend_short = (
            downtrend &
            (dataframe['adx'] > self.trend_adx_min.value) &
            (dataframe['minus_di'] > dataframe['plus_di']) &
            (rsi_prev >= self.trend_rsi_sell.value) &
            (dataframe['rsi'] < self.trend_rsi_sell.value) &
            (dataframe['close'] < dataframe['ema_fast']) &
            vol_filter &
            not_volume_spike &
            not_atr_spike
        )

        cond_mr_long = (
            (dataframe['ema_fast'] > dataframe[ema_col]) &
            (dataframe[slope_col] > 0) &
            (dataframe['close'].shift(1) < dataframe['bb_lowerband'].shift(1)) &
            (dataframe['rsi'].shift(1) < self.mr_rsi_buy.value) &
            (dataframe['fastk'].shift(1) < self.mr_stoch_buy.value) &
            (dataframe['rsi'] > rsi_prev) &
            (dataframe['close'] > dataframe['open']) &
            vol_filter &
            not_volume_spike &
            not_atr_spike
        )

        cond_mr_short = (
            (dataframe['ema_fast'] < dataframe[ema_col]) &
            (dataframe[slope_col] < 0) &
            (dataframe['close'].shift(1) > dataframe['bb_upperband'].shift(1)) &
            (dataframe['rsi'].shift(1) > self.mr_rsi_sell.value) &
            (dataframe['fastk'].shift(1) > self.mr_stoch_sell.value) &
            (dataframe['rsi'] < rsi_prev) &
            (dataframe['close'] < dataframe['open']) &
            vol_filter &
            not_volume_spike &
            not_atr_spike
        )

        dataframe.loc[cond_trend_long, ['enter_long', 'enter_tag']] = [1, 'trend_long']
        dataframe.loc[cond_mr_long, ['enter_long', 'enter_tag']] = [1, 'mr_long']
        dataframe.loc[cond_trend_short, ['enter_short', 'enter_tag']] = [1, 'trend_short']
        dataframe.loc[cond_mr_short, ['enter_short', 'enter_tag']] = [1, 'mr_short']

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        period = self.trend_ema_period.value
        ema_col = f'ema_{period}'
        if ema_col not in dataframe.columns:
            ema_col = 'ema_200'

        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0

        dataframe.loc[
            (dataframe['rsi'] > self.exit_rsi_long.value) |
            (
                (dataframe['close'] < dataframe[ema_col]) &
                (dataframe['close'].shift(1) >= dataframe[ema_col].shift(1))
            ),
            'exit_long'] = 1

        dataframe.loc[
            (dataframe['rsi'] < self.exit_rsi_short.value) |
            (
                (dataframe['close'] > dataframe[ema_col]) &
                (dataframe['close'].shift(1) <= dataframe[ema_col].shift(1))
            ),
            'exit_short'] = 1

        return dataframe

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        trade_dur_hours = (current_time - trade.open_date_utc).total_seconds() / 3600.0

        if trade_dur_hours > self.gen_hold_hours.value and current_profit < 0:
            return "time_stop_loss"

        tag = trade.enter_tag
        if tag in ('mr_long', 'mr_short'):
            if (trade_dur_hours > self.mr_hold_hours.value
                    and current_profit < self.mr_min_profit.value):
                return "mr_time_exit"

        return None

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_profit: float, current_rate: float = 0.0, **kwargs) -> float:
        lev = trade.leverage if trade.leverage else 1.0

        def _from_open(offset: float) -> Optional[float]:
            try:
                res = stoploss_from_open(
                    offset, current_profit, is_short=trade.is_short, leverage=lev
                )
            except Exception:
                return None
            if res is None or res <= 0:
                return None
            return res

        if current_profit > self.lock_high_profit.value:
            res = _from_open(self.lock_high_sl.value)
            if res is not None:
                return res

        if current_profit > self.lock_mid_profit.value:
            res = _from_open(self.lock_mid_sl.value)
            if res is not None:
                return res

        sl_pct = self.sl_base.value
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            prior = dataframe.loc[dataframe['date'] < trade.open_date_utc]
            if len(prior) > 0:
                row = prior.iloc[-1]
                atr = row['atr']
                close = row['close']
                if pd.notna(atr) and pd.notna(close) and close > 0:
                    atr_sl = self.sl_atr_mult.value * atr / close
                    sl_pct = float(min(max(atr_sl, 0.015), self.sl_base.value))
        except Exception:
            sl_pct = self.sl_base.value

        res = _from_open(-sl_pct)
        if res is not None:
            return res

        return -sl_pct

    # =========================
    # ADX 輔助函數與交易結束 Log 輸出
    # =========================
    def get_entry_adx(self, pair: str, open_date) -> float:
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or dataframe.empty or "adx" not in dataframe.columns:
                return float("nan")

            dates = pd.to_datetime(dataframe["date"], utc=True)
            entry_time = pd.Timestamp(open_date)
            if entry_time.tzinfo is None:
                entry_time = entry_time.tz_localize("UTC")
            else:
                entry_time = entry_time.tz_convert("UTC")

            matched = dataframe[dates == entry_time]
            if not matched.empty:
                adx_value = matched["adx"].iloc[0]
            else:
                mask = dates <= entry_time
                if not mask.any():
                    return float("nan")
                adx_value = dataframe.loc[mask, "adx"].iloc[-1]

            return float(adx_value) if not pd.isna(adx_value) else float("nan")
        except Exception:
            return float("nan")

    def get_exit_adx(self, pair: str, current_time) -> float:
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or dataframe.empty or "adx" not in dataframe.columns:
                return float("nan")

            dates = pd.to_datetime(dataframe["date"], utc=True)
            exit_time = pd.Timestamp(current_time)
            if exit_time.tzinfo is None:
                exit_time = exit_time.tz_localize("UTC")
            else:
                exit_time = exit_time.tz_convert("UTC")

            mask = dates <= exit_time
            if not mask.any():
                return float("nan")

            adx_value = dataframe.loc[mask, "adx"].iloc[-1]
            return float(adx_value) if not pd.isna(adx_value) else float("nan")
        except Exception:
            return float("nan")

    def confirm_trade_exit(
        self,
        pair: str,
        trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time,
        exit_reason: str,
        sell_reason=None,
        **kwargs
    ) -> bool:
        try:
            profit_ratio = trade.calc_profit_ratio(rate)
            
            # 根據 log_mode 決定是否過濾 Log 輸出
            if not self.log_mode:
                return True
            
            is_profit = profit_ratio >= 0
            if self.log_mode == "loss" and is_profit:
                return True
            if self.log_mode == "profit" and not is_profit:
                return True

            entry_adx_value = self.get_entry_adx(pair, trade.open_date_utc)
            exit_adx_value = self.get_exit_adx(pair, current_time)
            
            # 取得時間與價格（轉成越南時間格式）
            entry_time_str = self.format_vn_time(trade.open_date_utc)
            exit_time_str = self.format_vn_time(current_time)
            entry_price = trade.open_rate
            exit_price = rate
            leverage_used = getattr(trade, "leverage", 1.0)

            log_title = "賺錢交易出場紀錄" if is_profit else "賠錢交易出場紀錄"

            logger.info(
                "\n========== %s ==========\n"
                "交易對：%s (方向: %s | 標籤: %s | 槓桿: %.1fx)\n"
                "進場時間點 (VN)：%s\n"
                "出場時間點 (VN)：%s\n"
                "進場價：%.4f\n"
                "出場價：%.4f\n"
                "實際損益比例 (含槓桿)：%.4f%%\n"
                "進場當下 ADX(14)：%.2f\n"
                "出場當下 ADX(14)：%.2f\n"
                "出場原因：%s\n"
                "=====================================",
                log_title,
                pair,
                "做空" if trade.is_short else "做多",
                trade.enter_tag or "N/A",
                leverage_used,
                entry_time_str,
                exit_time_str,
                entry_price,
                exit_price,
                profit_ratio * 100,
                entry_adx_value,
                exit_adx_value,
                exit_reason or "N/A",
            )
        except Exception:
            logger.exception("[交易出場 Log 計算失敗]")

        return True