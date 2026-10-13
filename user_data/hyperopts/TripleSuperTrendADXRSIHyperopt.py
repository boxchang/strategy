# --- Hyperopt for TripleSuperTrendADXRSI ---
# 放到 user_data/hyperopts/ 目錄下即可使用
# 執行: freqtrade hyperopt --hyperopt TripleSuperTrendADXRSIHyperopt

from freqtrade.optimize.hyperopt import IHyperOpt
from freqtrade.optimize.hyperopt_loss import SharpeHyperOptLoss
from freqtrade.optimize.space import Integer, Real

class TripleSuperTrendADXRSIHyperopt(IHyperOpt):
    """
    Hyperopt configuration for TripleSuperTrendADXRSI strategy
    """

    @staticmethod
    def indicator_space():
        return [
            # ATR 週期
            Integer(5, 50, name="atr_period_fast"),
            Integer(10, 60, name="atr_period_mid"),
            Integer(20, 100, name="atr_period_slow"),

            # ATR 倍數
            Real(1.5, 5.0, name="atr_multiplier_fast"),
            Real(1.5, 5.0, name="atr_multiplier_mid"),
            Real(2.0, 6.0, name="atr_multiplier_slow"),

            # ADX 閾值
            Integer(15, 40, name="adx_threshold"),

            # RSI 區間
            Integer(40, 60, name="rsi_long"),
            Integer(60, 80, name="rsi_exit_long"),
            Integer(20, 40, name="rsi_short"),
            Integer(20, 40, name="rsi_exit_short"),
        ]

    @staticmethod
    def generate_parameters(trial):
        return {
            "atr_period_fast": trial.suggest_int("atr_period_fast", 5, 50),
            "atr_period_mid": trial.suggest_int("atr_period_mid", 10, 60),
            "atr_period_slow": trial.suggest_int("atr_period_slow", 20, 100),

            "atr_multiplier_fast": trial.suggest_float("atr_multiplier_fast", 1.5, 5.0),
            "atr_multiplier_mid": trial.suggest_float("atr_multiplier_mid", 1.5, 5.0),
            "atr_multiplier_slow": trial.suggest_float("atr_multiplier_slow", 2.0, 6.0),

            "adx_threshold": trial.suggest_int("adx_threshold", 15, 40),

            "rsi_long": trial.suggest_int("rsi_long", 40, 60),
            "rsi_exit_long": trial.suggest_int("rsi_exit_long", 60, 80),
            "rsi_short": trial.suggest_int("rsi_short", 20, 40),
            "rsi_exit_short": trial.suggest_int("rsi_exit_short", 20, 40),
        }

    @staticmethod
    def hyperopt_loss_function(results):
        # 使用 Sharpe Ratio 作為最佳化目標
        return SharpeHyperOptLoss(results)
