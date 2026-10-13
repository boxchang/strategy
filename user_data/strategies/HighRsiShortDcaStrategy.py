import logging
import numpy as np
import pandas as pd
from pandas import DataFrame
import pandas_ta as pta
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

# 建立 Logger 物件以在控制台印出 Log
logger = logging.getLogger(__name__)

class HighRsiShortDcaStrategy(IStrategy):
    """
    4h Short-Only Strategy with Hyperoptable RSI Parameters
    - RSI Entry > rsi_buy_level (可優化範圍 70-95)
    - RSI Exit < rsi_sell_level (可優化範圍 10-40)
    - RSI Period = rsi_period (可優化範圍 7-21)
    """

    # 策略基本設定
    timeframe = '4h'
    can_short = True
    
    stoploss = -1.0

    minimal_roi = {
        "0": 0.10
    }
    
    position_adjustment_enable = True
    max_entry_position_adjustment = 5

    # ==================== 可優化參數定義 ====================
    # RSI 週期，預設 14，範圍 7 到 21
    rsi_period = IntParameter(7, 21, default=14, space='buy', optimize=True)
    
    # 做空進場觸發點（RSI 高於此值進場），預設 90，範圍 70 到 95
    rsi_short_entry = IntParameter(70, 95, default=90, space='buy', optimize=True)
    
    # 做空平倉出場點（RSI 低於此值出場），預設 25，範圍 10 到 40
    rsi_short_exit = IntParameter(10, 40, default=25, space='sell', optimize=True)
    # ========================================================

    max_safety_orders = IntParameter(1, 5, default=10, space="buy", optimize=True)

    # 首張安全單觸發下跌百分比 (例如 -0.02 代表跌 2%)
    initial_safety_order_trigger = DecimalParameter(-0.08, -0.01, default=-0.4, decimals=3, space="buy", optimize=True)

    # 安全單間距擴大倍數 (Step Scale, 例如 2.0 代表第2張加碼跌幅為 2% * 2 = 4%)
    safety_order_step_scale = DecimalParameter(1.0, 3.0, default=1.0, decimals=2, space="buy", optimize=True)

    # 安全單注碼放大倍數 (Volume Scale, 例如 2.0 代表 100 -> 200 -> 400 USDT)
    safety_order_volume_scale = DecimalParameter(1.0, 3.0, default=2.0, decimals=2, space="buy", optimize=True)

    def leverage(self, pair: str, current_time: str, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str,
                 side: str, **kwargs) -> float:
        return 5.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """計算不同週期的 RSI 指標"""
        # 如果開啟 Hyperopt 尋找最佳週期，會依據當前的 rsi_period 動態計算
        dataframe['rsi'] = pta.rsi(dataframe['close'], length=self.rsi_period.value)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """進場條件：價格 > 100 且 RSI > rsi_short_entry 時做空"""
        dataframe.loc[
            (
                (dataframe['close'] > 95) &
                (dataframe['rsi'] > self.rsi_short_entry.value) &
                (dataframe['volume'] > 0)
            ),
            'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """出場條件：RSI < rsi_short_exit 時平倉出局"""
        dataframe.loc[
            (
                (dataframe['rsi'] < self.rsi_short_exit.value) &
                (dataframe['volume'] > 0)
            ),
            'exit_short'] = 1

        return dataframe

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                              current_profit: float, min_stake: Optional[float],
                              max_stake: float, **kwargs) -> Optional[tuple[float, str]]:
        """
        動態倉位調整：實作 DCA 安全單 (Safety Orders) 加碼機制（做空 Short + 槓桿專用）
        """
        # 計算目前已成功的進場次數 (首單為 1)
        filled_entries = trade.nr_of_successful_entries
        current_so_level = filled_entries  # 當前準備執行的安全單層數 (1 代表 SO1, 2 代表 SO2...)

        # 1. 檢查是否已達最大安全單層數上限
        if current_so_level > self.max_safety_orders.value:
            return None

        if filled_entries >= self.max_entry_position_adjustment:
            return None

        # 2. 設定加碼冷卻時間 (防止同一個 Candle 內重複觸發)
        if trade.date_last_filled_utc:
            seconds_since_last_fill = (current_time - trade.date_last_filled_utc).total_seconds()
            if seconds_since_last_fill < 300:  # 5分鐘冷卻
                return None

        # 3. 檢查剩餘可用資金
        if min_stake is not None and max_stake < min_stake:
            return None
        if max_stake <= 0:
            return None

        # 4. 取得當前交易槓桿 (預設 1.0 以防非合約模式)
        leverage = trade.leverage if trade.leverage else 1.0

        # 5. 取得首單保證金基礎 (Base Margin Stake)
        base_stake = self.config.get('stake_amount', 100)
        if isinstance(base_stake, str) or base_stake is None:
            base_stake = 100.0

        # --- 計算當前層數所需累積【標的價格漲幅】門檻 ---
        # 如果策略設定的 trigger 是 ROE 虧損比（如 1.0 代表 ROE -100%），
        # 則標的價格實際漲幅 = ROE 門檻 / 槓桿 (1.0 / 10 = 0.10 即價格上漲 10%)
        raw_trigger = abs(self.initial_safety_order_trigger.value)
        
        # 若 trigger 設定值已是標的價格變動百分比，請調整此處算式；
        # 若是 ROE 虧損比例（例如 -100%），則除以槓桿換算為現貨價格漲幅：
        price_trigger_per_level = raw_trigger / leverage if leverage > 0 else raw_trigger

        step_scale = self.safety_order_step_scale.value
        cumulative_rise_required = 0.0
        for i in range(current_so_level):
            cumulative_rise_required += price_trigger_per_level * (step_scale ** i)

        initial_entry_price = trade.open_rate

        # 7. 做空：計算當前價格相較於初始進場價的【實際價格漲幅】
        actual_rise = (current_rate - initial_entry_price) / initial_entry_price

        # 8. 觸發安全單條件：實際漲幅達到或超過換算後的累積價格漲幅
        if actual_rise >= price_trigger_per_level:
            volume_scale = self.safety_order_volume_scale.value

            # 當前安全單需要投入的保證金 (Margin Stake)
            desired_margin_stake = base_stake * (volume_scale ** (current_so_level - 1))

            # 確保保證金符合交易所最小/最大下單限制
            min_s = min_stake if min_stake is not None else 0.0
            final_stake = max(min_s, min(desired_margin_stake, max_stake))

            # 計算當前加碼對應的名義倉位價值 (Notional Position Value)
            notional_value = final_stake * leverage

            logger.info(
                f"[{trade.pair}] 觸發 Short DCA 安全單 #{current_so_level} (槓桿 {leverage:.0f}x):\n"
                f"▸ 初始進場價: {initial_entry_price:.4f} | 當前價: {current_rate:.4f}\n"
                f"▸ 實際標的漲幅: {actual_rise*100:.2f}% (相當於 ROE 虧損 {(actual_rise * leverage)*100:.2f}%)\n"
                f"▸ 要求標的漲幅: {cumulative_rise_required*100:.2f}%\n"
                f"▸ 補倉保證金: {final_stake:.2f} USDT (開倉名義價值: {notional_value:.2f} USDT)"
            )

            # 回傳 (加碼保證金金額, 標籤名稱)
            return final_stake, f"safety_order_level_{current_so_level}"

        return None