import os
from pathlib import Path

from dotenv import load_dotenv

# 以 config.py 所在目錄作為專案根目錄
BASE_DIR = Path(__file__).resolve().parent

# 載入專案根目錄的 .env
load_dotenv(BASE_DIR / ".env")


def get_required_env(name: str) -> str:
    """取得必要設定，未設定時直接報錯。"""
    value = os.getenv(name)

    if not value or not value.strip():
        raise RuntimeError(
            f"缺少必要環境變數：{name}，請檢查 .env 設定"
        )

    return value


def get_int_env(name: str, default: int) -> int:
    """取得整數設定。"""
    value = os.getenv(name)

    if value is None:
        return default

    return int(value)


# PostgreSQL
DB_HOST = get_required_env("DB_HOST")
DB_PORT = get_int_env("DB_PORT", 5432)
DB_NAME = get_required_env("DB_NAME")
DB_USER = get_required_env("DB_USER")
DB_PASSWORD = get_required_env("DB_PASSWORD")

# Anthropic
ANTHROPIC_API_KEY = get_required_env("ANTHROPIC_API_KEY")

# Freqtrade
FREQTRADE_SERVICE = os.getenv("FREQTRADE_SERVICE", "strategy03")
FREQTRADE_CONFIG = os.getenv(
    "FREQTRADE_CONFIG",
    "user_data/strategy_config/strategy03.json",
)
BACKTEST_RESULT_DIR = os.getenv(
    "BACKTEST_RESULT_DIR",
    "user_data/backtest_results",
)
VENV_PYTHON = os.getenv("VENV_PYTHON", "venv/bin/python3")