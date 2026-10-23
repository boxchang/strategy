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
    stoploss_from_open,
)
from freqtrade.persistence import Trade
import logging

logger = logging.getLogger(__name__)

# 越南時間 UTC+7
VN_TZ = timezone(timedelta(hours=7))


class TEMA_ADX_CMO_Strategy(IStrategy):
    """
    TEMA + ADX + CMO 策略 (1h 時間框架)
    - 進場：TEMA 排列 + ADX > 35 + CMO 穿越 0
    - 出場與風控：採用 TrendMeanReversionLX 的階梯鎖利、ATR 動態停損與時間停損機制
    """
    INTERFACE_VERSION = 3

    timeframe = '1h'
    can_short = True
    startup_candle_count = 100

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
    #   None     -> 完全關閉 Log 輸出
    # =========================
    log_mode = None

    # =========================
    # 參數設定
    # =========================
    adx_threshold = IntParameter(25, 45, default=35, space='buy', optimize=True)
    leverage_level = IntParameter(1, 10, default=1, space='buy', optimize=False)

    # 繼承自 TrendMeanReversionLX 的風控與出場參數
    sl_base = DecimalParameter(0.03, 0.15, default=0.06, decimals=2, space='sell', optimize=True)
    sl_atr_mult = DecimalParameter(1.5, 4.0, default=2.5, decimals=1, space='sell', optimize=True)
    
    lock_high_profit = DecimalParameter(0.08, 0.20, default=0.08, decimals=2, space='sell', optimize=True)
    lock_high_sl = DecimalParameter(0.005, 0.05, default=0.03, decimals=3, space='sell', optimize=True)
    
    lock_mid_profit = DecimalParameter(0.02, 0.08, default=0.04, decimals=2, space='sell', optimize=True)
    lock_mid_sl = DecimalParameter(0.0, 0.02, default=0.01, decimals=3, space='sell', optimize=True)

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
        # 計算 8, 13, 21 TEMA
        dataframe['tema_8'] = ta.TEMA(dataframe, timeperiod=8)
        dataframe['tema_13'] = ta.TEMA(dataframe, timeperiod=13)
        dataframe['tema_21'] = ta.TEMA(dataframe, timeperiod=21)

        # 計算 ADX、CMO、ATR
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['cmo'] = ta.CMO(dataframe, timeperiod=9)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        dataframe['enter_tag'] = None

        cmo_prev = dataframe['cmo'].shift(1)

        # 1. Long 進場條件
        cond_long = (
            (dataframe['tema_8'] > dataframe['tema_13']) &
            (dataframe['tema_13'] > dataframe['tema_21']) &
            (dataframe['adx'] > self.adx_threshold.value) &
            (cmo_prev <= 0) & 
            (dataframe['cmo'] > 0)
        )

        # 2. Short 進場條件
        cond_short = (
            (dataframe['tema_8'] < dataframe['tema_13']) &
            (dataframe['tema_13'] < dataframe['tema_21']) &
            (dataframe['adx'] > self.adx_threshold.value) &
            (cmo_prev >= 0) & 
            (dataframe['cmo'] < 0)
        )

        dataframe.loc[cond_long, ['enter_long', 'enter_tag']] = [1, 'tema_adx_cmo_1h_long']
        dataframe.loc[cond_short, ['enter_short', 'enter_tag']] = [1, 'tema_adx_cmo_1h_short']

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    # =========================
    # 出場機制：時間停損 (Time Stop Loss)
    # =========================
    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> Optional[str]:
        trade_dur_hours = (current_time - trade.open_date_utc).total_seconds() / 3600.0

        if trade_dur_hours > self.gen_hold_hours.value and current_profit < 0:
            trade.stop_tag = "time_stop_loss"
            return "time_stop_loss"

        return None

    # =========================
    # 動態停損機制 (階梯鎖利 + ATR 基礎停損)
    # =========================
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
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

        # 1. 高利潤鎖定
        if current_profit > self.lock_high_profit.value:
            res = _from_open(self.lock_high_sl.value)
            if res is not None:
                trade.stop_tag = "lock_high_profit"
                return res

        # 2. 中利潤鎖定
        if current_profit > self.lock_mid_profit.value:
            res = _from_open(self.lock_mid_sl.value)
            if res is not None:
                trade.stop_tag = "lock_mid_profit"
                return res

        # 3. 基礎 ATR 停損
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
            trade.stop_tag = "atr_stop"
            return res

        trade.stop_tag = "default_stop"
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
            
            if not self.log_mode:
                return True
            
            is_profit = profit_ratio >= 0
            if self.log_mode == "loss" and is_profit:
                return True
            if self.log_mode == "profit" and not is_profit:
                return True

            entry_adx_value = self.get_entry_adx(pair, trade.open_date_utc)
            exit_adx_value = self.get_exit_adx(pair, current_time)
            
            entry_time_str = self.format_vn_time(trade.open_date_utc)
            exit_time_str = self.format_vn_time(current_time)
            entry_price = trade.open_rate
            exit_price = rate
            leverage_used = getattr(trade, "leverage", 1.0)
            
            stop_tag = getattr(trade, "stop_tag", exit_reason)

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
                "出場原因 / 觸發 Tag：%s (%s)\n"
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
                stop_tag,
            )
        except Exception:
            logger.exception("[交易出場 Log 計算失敗]")

        return True