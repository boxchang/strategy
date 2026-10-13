import logging
from datetime import datetime
from typing import Optional
import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    DecimalParameter,
    CategoricalParameter,
)
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)

class DCARSIBot(IStrategy):
    """
    Freqtrade DCA + RSI 攤平機器人策略
    參考 Quant Tactics 及 欣利克的投資學習筆記 策略拆解實作。
    
    運作邏輯：
    1. 首單 (Base Order)：當 RSI 跌穿超賣門檻時觸發開倉，帶有 `rsi_entry` 標籤。
    2. 安全單 (Safety Orders)：價格持續下跌時，依據 Step Scale（間距倍數）與 
       Volume Scale（注碼倍數）進行多層次攤平加倉，帶有 `safety_order_level_X` 標籤。
    3. 出場機制：以持倉加權平均成本 (Average Entry Price) 上浮固定百分比進行全倉止賺。
    """

    # 策略版本與基礎設定
    INTERFACE_VERSION = 3
    timeframe = '15m'
    can_short = False

    # 啟用動態加倉 (DCA)
    position_adjustment_enable = True
    max_entry_position_adjustment = 6  # 包含首單在內的最大交易次數 (首單 + 5 張安全單)

    # 停利與停損機制
    # 將 ROI 設為極高，由 custom_exit 實作自訂動態均價止賺 (tp_from_average_price)
    minimal_roi = {
        "0": 100.0
    }
    
    # 原影片/文章預設 Stoploss -99%（等同不設硬止蝕，由 DCA 攤平等待反彈）
    stoploss = -0.99
    use_custom_stoploss = False

    # --- 超參數優化空間 (Hyperopt Parameters) ---
    
    # 1. RSI 進場濾網參數
    rsi_period = IntParameter(5, 30, default=14, space="buy", optimize=True)
    buy_rsi = IntParameter(10, 45, default=30, space="buy", optimize=True)

    # 2. DCA 安全單 (Safety Order) 設定
    # 最大安全單層數 (除首單外的加碼次數)
    max_safety_orders = IntParameter(1, 5, default=3, space="buy", optimize=True)
    
    # 首張安全單觸發下跌百分比 (例如 -0.02 代表跌 2%)
    initial_safety_order_trigger = DecimalParameter(-0.08, -0.01, default=-0.02, decimals=3, space="buy", optimize=True)
    
    # 安全單間距擴大倍數 (Step Scale, 例如 2.0 代表第2張加碼跌幅為 2% * 2 = 4%)
    safety_order_step_scale = DecimalParameter(1.0, 3.0, default=2.0, decimals=2, space="buy", optimize=True)
    
    # 安全單注碼放大倍數 (Volume Scale, 例如 2.0 代表 100 -> 200 -> 400 USDT)
    safety_order_volume_scale = DecimalParameter(1.0, 3.0, default=2.0, decimals=2, space="buy", optimize=True)

    # 3. 止賺參數 (基於持倉均價之獲利百分比)
    take_profit = DecimalParameter(0.01, 0.15, default=0.05, decimals=3, space="sell", optimize=True)

    # 4. 資金管理
    tradable_balance_ratio = 0.99
    startup_candle_count = 50

    leverage_val = IntParameter(1, 20, default=1, space="entry", optimize=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        計算技術指標：包含可優化週期的 RSI
        """
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        定義首單進場條件：當 RSI 跌穿超賣線時觸發 enter_long
        同時加入 enter_tag 讓回測能產生完整 Entry Tag 統計表
        """
        dataframe['enter_long'] = 0
        dataframe['enter_tag'] = ''

        # 當前 Candle 的 RSI 低於設定的 buy_rsi 門檻
        rsi_oversold = (dataframe['rsi'] < self.buy_rsi.value)
        
        # 確保有足夠成交量與指標數據
        valid_conditions = (dataframe['volume'] > 0) & rsi_oversold

        dataframe.loc[valid_conditions, 'enter_long'] = 1
        dataframe.loc[valid_conditions, 'enter_tag'] = 'rsi_entry'
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        標準 exit_long 置空，獲利了結完全由 custom_exit 接管
        """
        dataframe['exit_long'] = 0
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        """
        自訂出場邏輯：基於持倉加權平均成本 (Average Open Rate) 進行固定百分比止賺
        """
        # trade.open_rate 為 Freqtrade 自動維護的持倉加權平均成本
        if trade.open_rate > 0:
            # 計算相對於均價的真實獲利比例
            profit_from_avg = (current_rate - trade.open_rate) / trade.open_rate
            
            # 當當前獲利達到指定的 take_profit 目標時觸發出場
            if profit_from_avg >= self.take_profit.value:
                return "tp_from_average_price"

        return None

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                              current_profit: float, min_stake: Optional[float],
                              max_stake: float, **kwargs) -> Optional[tuple[float, str]]:
        """
        動態倉位調整：實作 DCA 安全單 (Safety Orders) 加碼機制
        """
        # 計算目前已成功的進場次數 (首單為 1)
        filled_entries = trade.nr_of_successful_entries
        current_so_level = filled_entries  # 當前準備執行的安全單層數 (1-indexed: 1 代表 SO1, 2 代表 SO2...)

        # 檢查是否已達最大安全單層數上限
        if current_so_level > self.max_safety_orders.value:
            return None

        if filled_entries >= self.max_entry_position_adjustment:
            return None

        # 設定加碼冷卻時間 (防止同一個 Candle 內重複觸發)
        if trade.date_last_filled_utc:
            seconds_since_last_fill = (current_time - trade.date_last_filled_utc).total_seconds()
            if seconds_since_last_fill < 300:  # 5分鐘冷卻
                return None

        # 檢查剩餘可用資金
        if min_stake is not None and max_stake < min_stake:
            return None
        if max_stake <= 0:
            return None

        # 取得首單金額基礎 (預設從 config 讀取 stake_amount，若未設定則為 100)
        base_stake = self.config.get('stake_amount', 100)
        if isinstance(base_stake, str) or base_stake is None:
            base_stake = 100.0

        # --- 計算當前層數所需累積跌幅門檻 ---
        initial_trigger = abs(self.initial_safety_order_trigger.value)
        step_scale = self.safety_order_step_scale.value
        
        cumulative_drop_required = 0.0
        for i in range(current_so_level):
            cumulative_drop_required += initial_trigger * (step_scale ** i)

        # 取得初次進場價格 (Initial Entry Price) - 使用 select_filled_orders 更安全
        filled_buys = trade.select_filled_orders('buy')
        if filled_buys:
            initial_entry_price = filled_buys[0].price or filled_buys[0].average or trade.open_rate
        else:
            initial_entry_price = trade.open_rate
        
        # 計算目前價格相較於初始進場價的下跌百分比
        actual_drop = (initial_entry_price - current_rate) / initial_entry_price

        # 觸發安全單條件：實際跌幅達到或超過該層數要求的累積跌幅
        if actual_drop >= cumulative_drop_required:
            volume_scale = self.safety_order_volume_scale.value
            
            # 當前安全單的注碼 = base_stake * (volume_scale ^ (current_so_level - 1))
            desired_stake = base_stake * (volume_scale ** (current_so_level - 1))

            # 確保注碼符合交易所最小/最大下單限制
            min_s = min_stake if min_stake is not None else 0.0
            final_stake = max(min_s, min(desired_stake, max_stake))

            logger.info(
                f"[{trade.pair}] 觸發 DCA 安全單 #{current_so_level}: "
                f"初始進場價={initial_entry_price:.4f}, 當前價={current_rate:.4f}, "
                f"實際跌幅={actual_drop*100:.2f}%, 要求跌幅={cumulative_drop_required*100:.2f}%, "
                f"下單金額={final_stake:.2f} USDT"
            )

            # 回傳 (金額, 標籤名稱) 以產生 Entry Tag 統計表
            return final_stake, f"safety_order_level_{current_so_level}"

        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                     proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                     side: str, **kwargs) -> float:
            """
            設定交易槓桿倍數
            """
            return float(self.leverage_val.value)