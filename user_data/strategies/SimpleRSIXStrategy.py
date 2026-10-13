import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime, timezone
from typing import Optional, Union
import logging

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
)
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib


class SimpleRSIXStrategy(IStrategy):
    """
    RSI 雙向極速交易策略 (15x 槓桿 + 三階層虧損加倉防禦版 - 24H全天候放量突破版)
    
    此策略已升級為：
    1. 順勢過濾：價格必須高於 EMA 200 (Long) / 低於 EMA 200 (Short)
    2. 訊號觸發：RSI 重新站回/跌破臨界點 (黃金交叉/死亡交叉)
    3. 動能確認：成交量必須高於成交量均線的 X 倍
    4. 運行模式：24小時全天候運行 (已去除美股開盤時間限制)
    """

    # 啟用日誌記錄器
    logger = logging.getLogger(__name__)

    # Freqtrade 策略介面版本
    INTERFACE_VERSION = 3

    # 1. 核心交易設定
    timeframe = '15m'      # 運行 K 線週期：15 分鐘 (適合敏捷雙向交易)
    can_short = True       # 啟用做空功能 (支持 Long / Short 雙向套利)
    
    # 2. 資金與加倉 (DCA) 控制
    position_adjustment_enable = True       # 必須設為 True 才能啟用自訂的加倉機制
    max_entry_position_adjustment = 10      # 單筆交易最大允許的追加進場次數 (配合第3階最大11次交易)

    # 3. 止盈止損設定
    # 【重要邏輯修正】：
    # Freqtrade 的 stoploss 是以「未槓桿的標的價格變化」為基準。
    # 如果設定 -0.12 (標下跌 12%)，在 10 倍槓桿下等於實際帳戶虧損 -120% 時止損。
    # 這樣才能確保第三階 DCA（槓桿利潤 -70% ~ -85%，即標的下跌 7% ~ 8.5%）有空間可以執行補倉拉低均價！
    stoploss = -0.12       

    # 4. 移動止損 (Trailing Stop) - 用於在大趨勢噴發時抱緊利潤
    trailing_stop = True
    trailing_stop_positive = 0.05      # 獲利達到 5% 後啟動移動止損
    trailing_stop_positive_offset = 0.08  # 觸發移動止損的利潤偏移量 (8%)
    trailing_only_offset_is_reached = True # 只有在達到 offset 後才開始移動

    # 5. 階層止盈 (ROI) 表 - 隨著持有時間增加，逐步降低止盈期望值
    minimal_roi = {
        "0": 0.20,     # 開倉即刻止盈目標：+20%
        "30": 0.15,    # 持倉 30 分鐘後止盈目標：+15%
        "60": 0.10     # 持倉 60 分鐘後止盈目標：+10%
    }

    # 6. 槓桿倍數設定
    leverage_value = 10.0

    # =====================================================================
    # 🎯 【HYPEROPT 超參數優化定義空間】
    # =====================================================================
    # 買入與賣出 RSI 臨界點
    buy_rsi = IntParameter(20, 40, default=30, space="buy", optimize=True)
    sell_rsi = IntParameter(60, 90, default=80, space="sell", optimize=True)

    # 新增：成交量放大倍數參數 (支持回測與優化)
    volume_multiplier = DecimalParameter(1.0, 3.0, default=1.5, decimals=1, space="buy", optimize=True)

    # 三階段 DCA 的虧損加倉觸發比例 (此處為合約帳戶「已槓桿」的浮動利潤虧損比例)
    # ⚠️ 注意：這三個優化上限（如 -0.85）在 10 倍槓桿下，對應標的價格下跌 8.5%。
    # 因此全域 stoploss 絕對不能設定得比 -0.095 還要淺，否則 Hyperopt 會優化出「看得到卻吃不到」的 DCA 階層。
    dca_stage1_profit = DecimalParameter(-0.55, -0.35, default=-0.45, decimals=2, space="buy", optimize=True)
    dca_stage2_profit = DecimalParameter(-0.68, -0.50, default=-0.60, decimals=2, space="buy", optimize=True)
    dca_stage3_profit = DecimalParameter(-0.85, -0.65, default=-0.70, decimals=2, space="buy", optimize=True)
    # =====================================================================

    # 繪圖設定
    plot_config = {
        'main_plot': {
            'ema200': {'color': '#00FFFF'}
        },
        'subplots': {
            "RSI": {
                'rsi': {'color': '#FFA500'},
            }
        }
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        計算技術指標
        """
        # 1. 計算 RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        
        # 2. 計算 EMA 200 趨勢線
        dataframe['ema200'] = ta.EMA(dataframe, timeperiod=200)

        # 3. 計算成交量均線 (20週期 EMA)，用來判斷是否「放量」
        dataframe['volume_ema'] = ta.EMA(dataframe['volume'], timeperiod=20)
        
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        進場信號：
        - 多頭 (Long)：
            1. 價格高於 EMA 200 (close > ema200)
            2. RSI 金叉站回 buy_rsi 門檻之上 (例如從 <30 站回 >30)
            3. 當前成交量放大 (volume > volume_ema * volume_multiplier)
        """
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        dataframe['enter_tag'] = ''

        # ------------------ 做多信號過濾 ------------------
        long_cond = (
            # 條件 1: 價格在 EMA200 之上
            (dataframe['close'] > dataframe['ema200']) &
            
            # 條件 2: RSI 重新站上買入臨界點 (金叉)
            (qtpylib.crossed_above(dataframe['rsi'], self.buy_rsi.value)) &
            
            # 條件 3: 成交量放大
            (dataframe['volume'] > (dataframe['volume_ema'] * self.volume_multiplier.value))
        )
        dataframe.loc[long_cond, ['enter_long', 'enter_tag']] = (1, 'rsi_ema_vol_long')

        # ------------------ 做空信號過濾 (對稱邏輯) ------------------
        short_cond = (
            # 條件 1: 價格在 EMA200 之下
            (dataframe['close'] < dataframe['ema200']) &
            
            # 條件 2: RSI 重新跌破賣出臨界點 (死叉)
            (qtpylib.crossed_below(dataframe['rsi'], self.sell_rsi.value)) &
            
            # 條件 3: 成交量放大
            (dataframe['volume'] > (dataframe['volume_ema'] * self.volume_multiplier.value))
        )
        dataframe.loc[short_cond, ['enter_short', 'enter_tag']] = (1, 'rsi_ema_vol_short')

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        出場信號：
        - 多頭平倉 (Exit Long)：RSI 向上突破 sell_rsi 超買區 (強勢止盈)
        - 空頭平倉 (Exit Short)：RSI 向下跌破 buy_rsi 超賣區 (強勢止盈)
        """
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        dataframe['exit_tag'] = ''

        # 多單平倉 (使用優化參數 sell_rsi)
        dataframe.loc[
            (dataframe['rsi'] > self.sell_rsi.value),
            ['exit_long', 'exit_tag']
        ] = (1, 'rsi_exit_long')

        # 空單平倉 (使用優化參數 buy_rsi)
        dataframe.loc[
            (dataframe['rsi'] < self.buy_rsi.value),
            ['exit_short', 'exit_tag']
        ] = (1, 'rsi_exit_short')

        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        """
        自訂槓桿乘數：全域設定為 10 倍槓桿 (可調整為 15)
        """
        return self.leverage_value

    def adjust_trade_position(
            self,
            trade,
            current_time: datetime,
            current_rate: float,
            current_profit: float,
            min_stake: float | None,
            max_stake: float,
            current_entry_rate: float,
            current_exit_rate: float,
            current_entry_profit: float,
            current_exit_profit: float,
            **kwargs
    ):
        """
        動態防禦補倉機制 (DCA)：
        
        當前交易產生虧損時，透過 3 階段加倉拉低均價，提高解套與反彈獲利的機率。
        """
        # 1. 檢查冷卻時間：距離上一次補倉成交不滿 5 分鐘，拒絕加倉
        if trade.date_last_filled_utc:
            seconds = (current_time - trade.date_last_filled_utc).total_seconds()
            if seconds < 5 * 60:
                return None

        # 2. 【安全守衛 1】可用資金防護：
        # 如果當前帳戶最大可追加資金 (max_stake) 已小於交易所規定的最小下單金額 (min_stake)，或小於等於 0，
        # 直接拒絕補倉，返回 None。這樣能徹底避免在高浮虧、高槓桿下計算出「負數補倉金額」而被誤判為部分平倉。
        if min_stake is not None and max_stake < min_stake:
            return None
        if max_stake <= 0:
            return None

        # 3. 獲取基礎注碼 (config_person.json 中的 stake_amount)
        base_stake = self.config.get('stake_amount', 30)

        # 安全防範：如果設定為 "unlimited" (字串) 則給予安全基礎值
        if isinstance(base_stake, str) or base_stake is None:
            base_stake = 30  

        # 4. 依據基礎注碼（base_stake）按比例去計算每一階的注碼
        stage1_config_stake = base_stake * 1.0
        stage2_config_stake = base_stake * 1.5
        stage3_config_stake = base_stake * 2.0
        
        # 交易所最小下單限制 (用於下方的限制保護)
        min_s = min_stake if min_stake is not None else 0.0
        
        # 【第一階】：當前合約利潤 <= dca_stage1_profit 且 總成交次數 < 4
        # 【安全守衛 2】: 使用 max(min_s, min(...)) 確保回傳金額絕對是合法的正數，雙重保險！
        if current_profit <= self.dca_stage1_profit.value and trade.nr_of_successful_entries < 4:
            stake = max(min_s, min(stage1_config_stake, max_stake))
            return stake, "test_dca_stage1"
            
        # 【第二階】：當前合約利潤 <= dca_stage2_profit 且 總成交次數 < 7
        elif current_profit <= self.dca_stage2_profit.value and trade.nr_of_successful_entries < 7:
            stake = max(min_s, min(stage2_config_stake, max_stake))
            return stake, "test_dca_stage2"
            
        # 【第三階】：當前合約利潤 <= dca_stage3_profit 且 總成交次數 < 11
        elif current_profit <= self.dca_stage3_profit.value and trade.nr_of_successful_entries < 11:
            stake = max(min_s, min(stage3_config_stake, max_stake))
            return stake, "test_dca_stage3"

        return None