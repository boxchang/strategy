import subprocess
import os
import streamlit as st
import pandas as pd
import psycopg2
import config

# 頁面基本設定
st.set_page_config(
    page_title="Freqtrade 自動化回測與戰情室",
    page_icon="📈",
    layout="wide"
)

st.title("🚀 Freqtrade 策略回測與自動化戰情室")

# ==================== 資料庫連線函式 ====================
def get_db_connection():
    return psycopg2.connect(
        host=config.DB_NAME,
        port="8000",
        database="postgres",
        user="ft_user",
        password=config.DB_PASSWORD
    )

# ==================== 側邊欄：回測控制面板 ====================
st.sidebar.header("⚙️ 回測參數控制面板")

with st.sidebar.form("backtest_form"):
    strategy_name = st.text_input("策略名稱 (Strategy)", value="TrendMeanReversionV4")
    strategy_version = st.number_input("策略版本 (Version)", min_value=1, value=4, step=1)
    
    col1, col2 = st.columns(2)
    with col1:
        timeframe = st.selectbox("K線週期", ["5m", "15m", "1h", "4h"], index=1)
    with col2:
        max_open_trades = st.number_input("最大持倉數", min_value=1, value=3)
        
    timerange = st.text_input("回測時間區間 (Timerange)", value="20260501-20261030")
    dry_run_wallet = st.number_input("初始資金 (Wallet)", value=1000.0)
    stake_amount = st.text_input("每單資金模式", value="unlimited")
    
    # Pairs 勾選與自定義區
    st.markdown("### 📊 測試交易對選擇 (Pairs)")
    default_pairs = [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "NVDA/USDT:USDT", 
        "MU/USDT:USDT", "TSM/USDT:USDT", "BZ/USDT:USDT", "CL/USDT:USDT", "SNDK/USDT:USDT"
    ]
    selected_pairs = st.multiselect("勾選或取消測試幣種", default_pairs, default=default_pairs)
    
    submitted = st.form_submit_button("🔥 開始執行回測並上傳資料庫")

# ==================== 執行回測邏輯 ====================
if submitted:
    if not selected_pairs:
        st.sidebar.error("❌ 至少必須選擇一個 Pair！")
    else:
        with st.spinner("⏳ 正在背景執行 Freqtrade 回測與 Claude AI 評分，請稍候..."):
            # 組裝指令傳遞給 upload_backtest.py 與 docker
            pairs_arg = " ".join(selected_pairs)
            
            # 1. 執行 Docker 回測
            docker_cmd = [
                "docker-compose", "run", "--rm", "strategy03", "backtesting",
                "--config", "user_data/strategy_config/strategy03.json",
                "--strategy", strategy_name,
                "--timeframe", timeframe,
                "--timerange", timerange,
                "--max-open-trades", str(max_open_trades),
                "--stake-amount", stake_amount,
                "--dry-run-wallet", str(dry_run_wallet),
                "--export", "trades",
                "--export-filename", "user_data/backtest_results/result.json",
                "-p"
            ] + selected_pairs
            
            res_dock = subprocess.run(docker_cmd, capture_output=True, text=True)
            
            if res_dock.returncode != 0:
                st.sidebar.error(f"❌ 回測執行失敗:\n{res_dock.stderr}")
            else:
                # 組合指令：透過 bash 先 source 虛擬環境，再執行 python3
                pairs_str = " ".join(selected_pairs)

                api_key = config.ANTHROPIC_API_KEY

                cmd_str = (
                    f"export ANTHROPIC_API_KEY='{api_key}' && "
                    f"source venv/bin/activate && python3 upload_backtest.py "
                    f"--dir user_data/backtest_results "
                    f"--strategy {strategy_name} "
                    f"--version {strategy_version} "
                    f"--timerange {timerange} "
                    f"--timeframe {timeframe} "
                    f"--max-open-trades {max_open_trades} "
                    f"--stake-amount {stake_amount} "
                    f"--dry-run-wallet {dry_run_wallet} "
                    f"--pairs {pairs_str}"
                )
                
                # 使用 shell=True 與 /bin/bash 確保能夠成功執行 source 指令
                res_py = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, executable="/bin/bash")
                
                if res_py.returncode == 0:
                    st.sidebar.success("✅ 回測完成、Claude AI 評分完畢並成功上傳至資料庫！")
                else:
                    st.sidebar.error(f"❌ 上傳資料庫失敗:\n{res_py.stderr}")

# ==================== 主畫面：回測戰情室 (Dashboard) ====================
st.markdown("---")
st.subheader("📊 歷史回測戰情室與績效總覽")

try:
    conn = get_db_connection()
    query = """
        SELECT 
            br.id,
            s.name AS strategy_name,
            s.version,
            br.timeframe,
            br.timerange,
            br.total_profit_pct,
            br.win_rate,
            br.total_trades,
            br.max_drawdown_pct,
            br.sharpe_ratio,
            br.pairs,
            ae.overall_score,
            ae.risk_level,
            ae.summary,
            br.created_at
        FROM backtest_runs br
        JOIN strategies s ON br.strategy_id = s.id
        LEFT JOIN ai_evaluations ae ON ae.backtest_run_id = br.id
        ORDER BY br.created_at DESC;
    """
    df = pd.read_sql(query, conn)
    conn.close()

    if df.empty:
        st.info("ℹ️ 目前資料庫尚無回測紀錄，請透過左側面板執行第一次回測。")
    else:
        # 顯示總覽表格
        st.dataframe(
            df[[
                'id', 'strategy_name', 'version', 'timeframe', 'timerange', 
                'total_profit_pct', 'win_rate', 'total_trades', 'max_drawdown_pct', 
                'sharpe_ratio', 'overall_score', 'risk_level', 'created_at'
            ]],
            use_container_width=True
        )
        
        # 點選詳細紀錄查看
        st.markdown("### 🔍 回測詳細與 AI 建議")
        selected_id = st.selectbox("選擇要查看的回測 ID", df['id'].tolist())
        
        if selected_id:
            row = df[df['id'] == selected_id].iloc[0]
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("總報酬率", f"{row['total_profit_pct']:.2f}%")
            col2.metric("勝率", f"{row['win_rate']:.2f}%")
            col3.metric("最大回撤 (MDD)", f"{row['max_drawdown_pct']:.2f}%")
            col4.metric("Claude AI 評分", f"{row['overall_score']} 分 ({row['risk_level']})")  # 💡 已改為 Claude
            
            st.info(f"💡 **AI 總評**: {row['summary']}")
            st.write(f"**測試 Pairs 清單**: {row['pairs']}")

except Exception as e:
    st.error(f"⚠️ 無法連線至 PostgreSQL 或讀取資料: {e}")
