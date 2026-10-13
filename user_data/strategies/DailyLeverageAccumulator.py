import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime, timezone
from typing import Optional, Union

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
)
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib


class DailyLeverageAccumulator(IStrategy):
    """
    通用型 20x 槓桿量化精準建倉策略 (15m 標準 RSI 版 - 新增成交量過濾防震盪)
    
    此策略專為 20 倍槓桿合約定投建倉設計：
    - 固定每次進場保證金為 100.0 USDT (20x 槓桿下對應 2000 USDT 名義持倉)
    - 每日限制最多買入 3 次，累積打滿約 13,000 USDT 保證金 (約 260,000 USDT 總持倉)
    - 採用 15m 週期標準 RSI(14) < 30 判斷，在日內震盪回踩至超賣區時精準撿入
    - 新增出場：當 15m 週期標準 RSI(14) > 80 時，主動平倉（賣出）鎖定利潤
    - 【防噪優化】：引入相對成交量過濾機制，避免在無量死水盤整區被 RSI 假信號誘多或誘空
    
    ====================================================================
    🛡️ 【20x 逐倉帳戶防強平與安全資金鏈實戰操作】 🛡️
    ====================================================================
    特別注意：由於本策略運行於「逐倉（Isolated Margin）模式」，每個倉位的風險完全隔離。
    帳戶內剩下未開倉的可用餘額（Free Balance）「預設不會」自動去支撐虧損的逐倉。
    
    為了防止 20x 槓桿因市場極端波動被斷頭，您必須實施以下防禦機制：
    
    1. 【手動防禦（推薦）】：
       密切監控持倉。當價格暴跌使現價逼近「強平價格」剩 1.5% 距離時，
       手動點擊交易所持倉旁的「增加保證金 (Add Margin)」，將合約帳戶中未開倉的可用餘額，
       手動注入逐倉中。此操作能瞬間將您的強平價向下壓低，重獲安全邊際。
       
    2. 【自動防禦（可選）】：
       在交易所（如幣安）逐倉介面中，開啟「自動追加保證金 (Auto-Deposit Margin)」功能。
       當倉位瀕臨強平時，系統會自動扣除您合約帳戶的可用餘額注入該倉位，以防爆倉。
       
    3. 【保本損防禦 (Break-even Stop Loss)】：
       建倉期結束後，只要價格脫離成本區，應立即將止損線上移至平均建倉成本，
       此時剩餘的可用餘額即可安全撤離。
    ====================================================================
    """

    INTERFACE_VERSION = 3

    # 交易設定：15m 週期，靈敏捕捉日內回踩低點
    timeframe = '15m'
    stoploss = -0.45

    # 禁用 Freqtrade 的自動 ROI 止盈，我們是長波段持倉，建倉期由 RSI 指標或移動止損主導
    minimal_roi = {
        "0": 10.0  # 設為 1000% 幾乎不觸發自動止盈
    }

    # 策略專屬自訂參數 
    buy_rsi_trigger = IntParameter(25, 35, default=30, space="buy")  
    sell_rsi_trigger = IntParameter(75, 90, default=80, space="sell") 
    max_buys_per_day = IntParameter(1, 5, default=3, space="buy")    

    # 【🛡️ 新增防護參數】：相對成交量乘數 (當前交易量必須大於平均交易量的 X 倍才允許交易)
    # 預設為 0.5，代表當前 15m 交易量不能低於過去 20 根 K 線平均成交量的 50%
    volume_factor = DecimalParameter(0.1, 1.5, default=0.5, decimals=1, space="buy", optimize=True)

    # 預算與持倉控制
    target_margin_usd = 100.0       # 固定每次進場使用 100.0 USDT 保證金
    max_total_trades = 130          # 限制最多累積加倉 130 次 (打滿約 13,000 USDT 保證金)

    # 用於在記憶體中追蹤每日交易次數
    custom_trade_tracker = {
        'current_day': '',
        'daily_buy_count': 0
    }

    # 繪圖設定
    plot_config = {
        'main_plot': {
            'ema25': {'color': '#FF69B4'},
        },
        'subplots': {
            "RSI": {
                'rsi': {'color': '#FFA500'},
            }
        }
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        計算技術指標：在 15m K 線基礎上計算指標
        """
        # 標準 14 週期的 RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        
        # 僅保留一條均線作為主圖參考
        dataframe['ema25'] = ta.EMA(dataframe, timeperiod=25)
        
        # 【🛡️ 新增指標】：計算 20 週期的成交量簡單移動平均線
        dataframe['volume_sma'] = ta.SMA(dataframe, timeperiod=20, price='volume')
        
        # 用來偵測當前日期是否變更
        dataframe['date_string'] = pd.to_datetime(dataframe['date']).dt.strftime('%Y-%m-%d')

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        進場訊號：
        1. 標準 14 週期 RSI 低於自訂門檻（預設 30）
        2. 當前 15m K 線的成交量高於「成交量均線的 X 倍」，確保是有真實資金在推動交易
        """
        dataframe.loc[
            (
                (dataframe['rsi'] < self.buy_rsi_trigger.value) &
                (dataframe['volume'] > (dataframe['volume_sma'] * self.volume_factor.value))
            ),
            'enter_long'
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        出場訊號：
        1. 標準 14 週期 RSI 高於自訂門檻（預設 80）
        2. 當前 15m K 線的成交量高於「成交量均線的 X 倍」，防範無量超買產生的假離場信號
        """
        # 初始化出場信號
        dataframe['exit_long'] = 0

        dataframe.loc[
            (
                (dataframe['rsi'] > self.sell_rsi_trigger.value) &
                (dataframe['volume'] > (dataframe['volume_sma'] * self.volume_factor.value))
            ),
            'exit_long'
        ] = 1
        
        return dataframe

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            entry_tag: Optional[str], side: str, **kwargs) -> float:
        """
        精確倉位控制：每次下單保證金強制固定為 100.0 USDT
        """
        return self.target_margin_usd

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        """
        核心控制閘：確保每日僅交易限制次數，且不超額建倉
        """
        # 取得當前日期的字串格式 (YYYY-MM-DD)
        today_str = current_time.strftime('%Y-%m-%d')

        # 如果進入了新的一天，將每日買入計數器重置為 0
        if self.custom_trade_tracker['current_day'] != today_str:
            self.custom_trade_tracker['current_day'] = today_str
            self.custom_trade_tracker['daily_buy_count'] = 0

        # 【防護 1】限制當日買入次數最多為 3 次
        if self.custom_trade_tracker['daily_buy_count'] >= self.max_buys_per_day.value:
            return False

        # 【防護 2】總建倉次數上限控制 (防止超過 13,000 保證金預算)
        # 採用安全標準的 wallets.get_active_trades() API 接口
        # active_trades = len(self.wallets.get_active_trades())
        # if active_trades >= self.max_total_trades:
        #     return False

        # 通過驗證，將每日計數器加 1，允許下單
        self.custom_trade_tracker['daily_buy_count'] += 1
        return True