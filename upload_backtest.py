import argparse
import glob
import json
import os
import zipfile
import psycopg2
import anthropic

# 初始化 Anthropic (Claude) 客戶端
api_key = os.getenv("ANTHROPIC_API_KEY", "dummy-key-to-prevent-crash")
client = anthropic.Anthropic(api_key=api_key)

def find_latest_zip(results_dir: str):
    """尋找回測結果資料夾中最新的 zip 檔案"""
    search_path = os.path.join(results_dir, "*.zip")
    zip_files = glob.glob(search_path)
    if not zip_files:
        return None
    return max(zip_files, key=os.path.getmtime)

def parse_backtest_from_zip(zip_path: str):
    """解壓縮 ZIP 並解析內部的 JSON 檔案"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            json_filenames = [name for name in z.namelist() if name.endswith('.json') and not name.startswith('_')]
            if not json_filenames:
                print("❌ 錯誤：ZIP 壓縮檔內找不到任何 .json 檔案")
                return None
            
            target_json = json_filenames[0]
            print(f"📂 正在讀取壓縮檔內的: {target_json}")
            
            with z.open(target_json) as f:
                return json.load(f)
    except Exception as e:
        print(f"❌ 解壓縮或解析 JSON 失敗: {e}")
        return None

import re

def get_ai_evaluation(metrics: dict, comparison_list: list):
    """調用 Claude 進行綜合績效與風險評分"""
    comp_summary_str = ""
    if comparison_list:
        for comp in comparison_list:
            comp_summary_str += f"""
            - 策略鍵值(Key): {comp.get('key')}
              交易次數: {comp.get('trades')}
              總報酬率(%): {comp.get('profit_total_pct')}%
              總報酬金額(Abs): {comp.get('profit_total_abs')}
              勝率: {comp.get('winrate', 0) * 100:.2f}% (勝:{comp.get('wins')} / 敗:{comp.get('losses')} / 平:{comp.get('draws')})
              夏普比率 (Sharpe): {comp.get('sharpe')}
              索提諾比率 (Sortino): {comp.get('sortino')}
              卡爾瑪比率 (Calmar): {comp.get('calmar')}
              年化報酬率 (CAGR): {comp.get('cagr')}
              期望值 (Expectancy): {comp.get('expectancy')}
              盈虧比 (Profit Factor): {comp.get('profit_factor')}
              最大回撤(帳戶比例): {comp.get('max_drawdown_account')}
              平均持倉時間: {comp.get('duration_avg')}
            """
    else:
        comp_summary_str = "無額外比較清單"

    prompt = f"""
    分析以下量化策略的回測報告與綜合評比參數：

    【核心績效指標 (Strategy Metrics)】
    - 總報酬率: {metrics.get('profit_total_pct')}%
    - 勝率: {metrics.get('winrate', 0) * 100}%
    - 交易次數: {metrics.get('total_trades')}
    - 最大回撤比例: {metrics.get('max_drawdown_account_pct', 0) * 100}%
    - 夏普比率: {metrics.get('sharpe')}

    【策略綜合比較清單 (Strategy Comparison)】
    {comp_summary_str}

    請根據以上所有詳細數據進行深度分析，並輸出嚴格的 JSON 格式：
    {{
      "score": 0到100的綜合評分整數,
      "risk_level": "LOW", "MEDIUM" 或 "HIGH",
      "summary": "簡短總評，結合Sharpe、勝率與回撤表現 (50字內)",
      "suggestions": "具體改進建議"
    }}
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=2048,  # 提升 max_tokens 避免內容被截斷
            messages=[{"role": "user", "content": prompt}]
        )
        
        # 合併所有文字區塊
        content_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                content_text += block.text
        content_text = content_text.strip()
        
        # 使用正規表達式自動尋找並提取第一個 '{' 到最後一個 '}' 的 JSON 內容
        match = re.search(r'\{[\s\S]*\}', content_text)
        if match:
            json_str = match.group(0)
        else:
            json_str = content_text
            
        return json.loads(json_str)
    except Exception as e:
        print(f"⚠️ 略過 Claude AI 評分 (原因: {e})")
        return {
            "score": 0, 
            "risk_level": "N/A", 
            "summary": "AI 評分暫時略過 (額度不足或無金鑰)", 
            "suggestions": "請檢查 ANTHROPIC_API_KEY 環境變數與餘額"
        }

def main():
    parser = argparse.ArgumentParser(description="自動解壓縮 Fregtrade ZIP 回測並寫入 PostgreSQL")
    parser.add_argument("--dir", default="user_data/backtest_results", help="回測結果資料夾路徑")
    parser.add_argument("--strategy", required=True, help="策略名稱")
    parser.add_argument("--version", type=int, default=1, help="策略版本數字")
    parser.add_argument("--timerange", default="20260101-20261031", help="回測時間區間")
    parser.add_argument("--timeframe", default="15m", help="K線週期")
    parser.add_argument("--pairs", nargs="*", default=[], help="本次執行的交易對清單")
    
    # 保留的回測控制參數
    parser.add_argument("--max-open-trades", type=int, default=1, help="最大同時持倉數")
    parser.add_argument("--stake-amount", default="unlimited", help="每單下注金額")
    parser.add_argument("--dry-run-wallet", type=float, default=1000.0, help="初始回測資金")
    
    # 資料庫連線參數
    parser.add_argument("--db-host", default="115.79.43.113", help="PostgreSQL 主機")
    parser.add_argument("--db-port", default="8000", help="PostgreSQL Port")
    parser.add_argument("--db-name", default="postgres", help="資料庫名稱")
    parser.add_argument("--db-user", default="ft_user", help="資料庫使用者")
    parser.add_argument("--db-password", default="luxy@20201010", help="資料庫密碼")

    args = parser.parse_args()

    # 1. 尋找並解析最新的 ZIP 檔
    latest_zip = find_latest_zip(args.dir)
    if not latest_zip:
        print(f"❌ 錯誤：在 {args.dir} 找不到任何回測 zip 檔案")
        return

    print(f"🚀 找到最新回測壓縮檔: {latest_zip}")
    raw_data = parse_backtest_from_zip(latest_zip)
    if not raw_data:
        return

    # 2. 萃取策略指標與 comparison 清單
    strategy_metrics = raw_data.get("strategy", {}).get(args.strategy, {})
    if not strategy_metrics:
        print(f"❌ 錯誤：ZIP 內的 JSON 裡找不到策略 [{args.strategy}] 的數據")
        return

    strategy_summary_data = strategy_metrics.copy()
    trades_list = strategy_summary_data.pop("trades", [])  # 抽離 trades 陣列
    comparison_list = raw_data.get("strategy_comparison", [])

    total_profit_pct = strategy_summary_data.get("profit_total", 0) * 100
    win_rate = strategy_summary_data.get("winrate", 0) * 100
    total_trades = strategy_summary_data.get("total_trades", 0)
    max_drawdown = strategy_summary_data.get("max_drawdown_account", 0) * 100
    sharpe = strategy_summary_data.get("sharpe", 0)
    
    starting_balance = args.dry_run_wallet
    final_balance = starting_balance * (1 + total_profit_pct / 100)

    print(f"📊 成功解析回測指標 (策略: {args.strategy} | 版本: v{args.version})：")
    print(f"   - 執行 Pairs: {args.pairs}")
    print(f"   - 參數設定: 初始資金={starting_balance}, 最大持倉={args.max_open_trades}")
    print(f"   - 總投報率: {total_profit_pct}% | 勝率: {win_rate:.2f}% | 總交易筆數: {len(trades_list)}")

    # 3. 取得 Claude AI 評分
    print("🤖 正在將完整指標與 Comparison 數據傳送給 Claude AI 進行策略評分...")
    ai_result = get_ai_evaluation(strategy_metrics, comparison_list)

    # 4. 直接連線寫入 PostgreSQL
    print("💾 正在將主檔、參數、比較數據與交易明細寫入 PostgreSQL...")
    try:
        conn = psycopg2.connect(
            host=args.db_host,
            port=args.db_port,
            database=args.db_name,
            user=args.db_user,
            password=args.db_password
        )
        cur = conn.cursor()

        # A. 相容性補丁：確保資料庫欄位完整
        cur.execute("""
            ALTER TABLE strategies 
            ALTER COLUMN version TYPE INT USING version::integer;
            
            ALTER TABLE backtest_runs 
            ADD COLUMN IF NOT EXISTS strategy_summary JSONB,
            ADD COLUMN IF NOT EXISTS pairs TEXT[],
            ADD COLUMN IF NOT EXISTS max_open_trades INT,
            ADD COLUMN IF NOT EXISTS stake_amount VARCHAR(50);
        """)

        # B. 寫入或更新策略基本資料表
        cur.execute("""
            INSERT INTO strategies (name, version) VALUES (%s, %s)
            ON CONFLICT (name, version) DO UPDATE SET version = EXCLUDED.version
            RETURNING id;
        """, (args.strategy, args.version))
        strategy_id = cur.fetchone()[0]

        # C. 寫入回測績效記錄主檔
        cur.execute("""
            INSERT INTO backtest_runs 
            (strategy_id, timeframe, timerange, starting_balance, final_balance, total_profit_pct, win_rate, total_trades, max_drawdown_pct, sharpe_ratio, raw_json_data, strategy_summary, pairs, max_open_trades, stake_amount)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            strategy_id, args.timeframe, args.timerange, starting_balance, final_balance,
            total_profit_pct, win_rate, total_trades, max_drawdown, sharpe, 
            json.dumps(raw_data), json.dumps(strategy_summary_data), args.pairs,
            args.max_open_trades, args.stake_amount
        ))
        backtest_run_id = cur.fetchone()[0]

        # D. 寫入 AI 評分表
        cur.execute("""
            INSERT INTO ai_evaluations (backtest_run_id, overall_score, risk_level, summary, suggestions)
            VALUES (%s, %s, %s, %s, %s);
        """, (
            backtest_run_id, ai_result.get("score"), ai_result.get("risk_level"),
            ai_result.get("summary"), ai_result.get("suggestions")
        ))

        # E. 寫入 strategy_comparison 比較清單
        inserted_comp_count = 0
        for comp in comparison_list:
            cur.execute("""
                INSERT INTO strategy_comparison 
                (backtest_run_id, strategy_key, trades, profit_mean, profit_mean_pct, profit_total_abs, profit_total, profit_total_pct, duration_avg, wins, draws, losses, winrate, cagr, expectancy, expectancy_ratio, sortino, sharpe, calmar, sqn, profit_factor, max_drawdown_account, max_drawdown_abs)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                backtest_run_id,
                comp.get("key"),
                comp.get("trades"),
                comp.get("profit_mean"),
                comp.get("profit_mean_pct"),
                comp.get("profit_total_abs"),
                comp.get("profit_total"),
                comp.get("profit_total_pct"),
                comp.get("duration_avg"),
                comp.get("wins"),
                comp.get("draws"),
                comp.get("losses"),
                comp.get("winrate"),
                comp.get("cagr"),
                comp.get("expectancy"),
                comp.get("expectancy_ratio"),
                comp.get("sortino"),
                comp.get("sharpe"),
                comp.get("calmar"),
                comp.get("sqn"),
                comp.get("profit_factor"),
                comp.get("max_drawdown_account"),
                comp.get("max_drawdown_abs")
            ))
            inserted_comp_count += 1

        # F. 寫入每一筆獨立交易明細 (backtest_trades)
        inserted_trades_count = 0
        skipped_trades_count = 0
        for trade in trades_list:
            pair = trade.get("pair")
            open_date = trade.get("open_date")

            cur.execute("""
                SELECT 1 FROM backtest_trades bt
                JOIN backtest_runs br ON bt.backtest_run_id = br.id
                WHERE br.strategy_id = %s AND bt.pair = %s AND bt.open_date = %s
                LIMIT 1;
            """, (strategy_id, pair, open_date))

            if cur.fetchone():
                skipped_trades_count += 1
                continue

            cur.execute("""
                INSERT INTO backtest_trades 
                (backtest_run_id, pair, stake_amount, amount, open_date, close_date, 
                 open_rate, close_rate, trade_duration, profit_ratio, profit_abs, 
                 exit_reason, is_short, enter_tag, funding_fees, weekday, orders_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                backtest_run_id,
                pair,
                trade.get("stake_amount"),
                trade.get("amount"),
                trade.get("open_date"),
                trade.get("close_date"),
                trade.get("open_rate"),
                trade.get("close_rate"),
                trade.get("trade_duration"),
                trade.get("profit_ratio"),
                trade.get("profit_abs"),
                trade.get("exit_reason"),
                trade.get("is_short"),
                trade.get("enter_tag"),
                trade.get("funding_fees", 0.0),
                trade.get("weekday"),
                json.dumps(trade.get("orders", []))
            ))
            inserted_trades_count += 1

        conn.commit()
        cur.close()
        conn.close()

        print(f"✅ 成功完成！回測數據已寫入 PostgreSQL：")
        print(f"   - 策略名稱: {args.strategy} (版本: {args.version})")
        print(f"   - 紀錄 Pairs 數: {len(args.pairs)} 個 | 最大持倉: {args.max_open_trades}")
        print(f"   - Strategy Comparison 匯入: {inserted_comp_count} 筆")
        print(f"   - 獨立交易明細 (Trades) 新增: {inserted_trades_count} 筆 (已自動過濾重複: {skipped_trades_count} 筆)")
        print(f"   - Claude AI 綜合評分: {ai_result.get('score')} 分 ({ai_result.get('risk_level')})")

    except Exception as e:
        print(f"❌ 資料庫寫入失敗: {e}")

if __name__ == "__main__":
    main()