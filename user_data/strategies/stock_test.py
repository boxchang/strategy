import yfinance as yf

# 建立 NVIDIA 股票物件
nvda = yf.Ticker("NVDA")

# 取得最近一天的股價資料
data = nvda.history(period="1d")

# 取最後一筆收盤價
last_close = data['Close'].iloc[-1]
print("NVIDIA 最後收盤價:", last_close)