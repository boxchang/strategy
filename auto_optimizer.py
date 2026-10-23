import subprocess
import json
import os
import zipfile
import ast
import anthropic
import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

STRATEGY_NAME = "TrendMeanReversionLX"
STRATEGY_FILE_PATH = f"user_data/strategies/{STRATEGY_NAME}.py"
CONFIG_PATH = "user_data/strategy_config/strategy03.json"

def run_backtest():
    print(f"🚀 正在透過 Docker Compose 執行策略 {STRATEGY_NAME} 的回測...")
    cmd = [
        "docker-compose", "run", "--rm", "strategy03",
        "backtesting",
        "--config", CONFIG_PATH,
        "--strategy", STRATEGY_NAME,
        "--timerange", "20250101-20261030",
        "--export", "trades"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("❌ 回測執行出錯：", result.stderr)
        return None
    print("✅ 回測完成！")
    return parse_latest_result()

def parse_latest_result():
    results_dir = "user_data/backtest_results"
    if not os.path.exists(results_dir):
        print(f"⚠️ 找不到回測結果目錄：{results_dir}")
        return {}
        
    files = [os.path.join(results_dir, f) for f in os.listdir(results_dir) if f.endswith('.zip')]
    if not files:
        json_files = [os.path.join(results_dir, f) for f in os.listdir(results_dir) if f.endswith('.json')]
        if not json_files:
            print("⚠️ 未找到任何回測結果檔 (.zip 或 .json)")
            return {}
        latest_file = max(json_files, key=os.path.getmtime)
        return parse_json_file(latest_file)
        
    latest_file = max(files, key=os.path.getmtime)
    print(f"正在讀取壓縮回測檔: {latest_file}")
    
    try:
        with zipfile.ZipFile(latest_file, 'r') as z:
            json_names = [name for name in z.namelist() if name.endswith('.json')]
            if not json_names:
                print("⚠️ Zip 壓縮檔內找不到任何 .json 檔案")
                return {}
            target_json = json_names[0]
            with z.open(target_json) as f:
                data = json.load(f)
                
        strategy_data = data.get("strategy", {}).get(STRATEGY_NAME, {})
        metrics = {
            "trades": strategy_data.get("total_trades", 0),
            "win_rate": strategy_data.get("winrate", 0),
            "profit_factor": strategy_data.get("profit_factor", 0),
            "sharpe": strategy_data.get("sharpe", 0),
            "max_drawdown": strategy_data.get("max_drawdown_account", 0)
        }
        return metrics
    except Exception as e:
        print(f"⚠️ 解析 Zip 內的 JSON 結果失敗：{e}")
        return {}

def parse_json_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        strategy_data = data.get("strategy", {}).get(STRATEGY_NAME, {})
        return {
            "trades": strategy_data.get("total_trades", 0),
            "win_rate": strategy_data.get("winrate", 0),
            "profit_factor": strategy_data.get("profit_factor", 0),
            "sharpe": strategy_data.get("sharpe", 0),
            "max_drawdown": strategy_data.get("max_drawdown_account", 0)
        }
    except Exception as e:
        print(f"⚠️ 解析 JSON 檔案失敗：{e}")
        return {}

def calculate_score(metrics: dict) -> float:
    trades = metrics.get('trades', 0)
    if trades < 10:
        return 0.0
    sharpe = max(0, metrics.get('sharpe', 0))
    pf = max(0, metrics.get('profit_factor', 0))
    wr = max(0, metrics.get('win_rate', 0)) / 100.0
    trade_penalty = min(1.0, trades / 30.0)
    return float(sharpe * pf * (1 + wr) * trade_penalty)

def validate_code(code: str) -> bool:
    if not code or len(code.strip()) < 100:
        print("❌ 產出的程式碼過短或為空")
        return False
    
    try:
        ast.parse(code)
    except SyntaxError as e:
        print(f"❌ 產出的程式碼含有語法錯誤 (SyntaxError): {e}")
        return False
        
    required_elements = [
        f"class {STRATEGY_NAME}(IStrategy)",
        "populate_indicators",
        "populate_entry_trend",
        "populate_exit_trend"
    ]
    for elem in required_elements:
        if elem not in code:
            print(f"❌ 產出的程式碼不完整，缺少必要元件: {elem}")
            return False
            
    return True

def ask_ai_to_fix_code(current_code: str, metrics: dict, score: float, best_score: float, improved: bool) -> str:
    status_msg = "上一輪修改成功提升了表現！" if improved else "上一輪修改導致表現下滑，請調整方向修正。"
    
    prompt = f"""
    當前 Fregtrade 策略回測表現（夏普值為負，代表策略呈穩定虧損狀態）：
    - 綜合評分: {score:.4f} (歷史最佳: {best_score:.4f})
    - 狀態回饋: {status_msg}
    - 交易筆數: {metrics.get('trades', 0)}
    - 勝率: {metrics.get('win_rate', 0)}
    - 盈虧比 (Profit Factor): {metrics.get('profit_factor', 0)}
    - 夏普值 (Sharpe): {metrics.get('sharpe', 0)}
    - 最大回撤: {metrics.get('max_drawdown', 0)}

    當前策略程式碼如下：
    ```python
    {current_code}
    ```

    【嚴重警告與優化指示】：
    過去幾輪的微調未能扭轉負夏普值，證明原有的進場邏輯方向有誤或摩擦成本過高。請以資深量化架構師的角度進行深度重構：
    1. 檢視進場訊號是否在「追漲殺跌」或「逆勢接刀」，導致勝率與盈虧比雙雙崩潰。
    2. 大膽重構進場或出場條件（例如：加入趨勢過濾、改變停利停損的計算方式、或調整順勢與均值回歸的邏輯）。
    3. 【嚴格檢查未來資料洩漏（Lookahead Bias）】：確保所有指標計算、條件判斷、以及自訂停損中，絕對沒有使用負數的 shift（如 shift(-n)）或直接呼叫當根 K 線收盤後才能確定的數值作為進場依據。

    【絕對規則】：
    1. 必須保留原有的 class 名稱（class {STRATEGY_NAME}(IStrategy):），絕對不能更改類別名稱！
    2. 必須輸出完整且合法的 Python 程式碼，不可中途截斷。
    3. 只輸出合法的完整 Python 程式碼，不要包在 Markdown 以外的其他冗長對話中。
    程式碼不用註解說明只需要版本號
    """
    
    candidate_models = [
        "claude-sonnet-5-5",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022"
    ]
    
    for model_name in candidate_models:
        try:
            print(f"🤖 嘗試使用模型 {model_name} 進行診斷...")
            response = client.messages.create(
                model=model_name,
                max_tokens=8192,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            full_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    full_text += block.text
            
            new_code = full_text.strip()
            
            if "```python" in new_code:
                new_code = new_code.split("```python")[1].split("```")[0].strip()
            elif "```" in new_code:
                new_code = new_code.split("```")[1].split("```")[0].strip()
                
            return new_code

        except anthropic.NotFoundError:
            print(f"⚠️ 模型 {model_name} 無權限或未找到 (404)，嘗試下一個模型...")
            continue
        except Exception as e:
            print(f"❌ 呼叫 Claude API 發生錯誤 ({model_name})：{e}")
            break
            
    return ""

def update_strategy_file(new_code: str):
    with open(STRATEGY_FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(new_code)

if __name__ == "__main__":
    MAX_ITERATIONS = 10
    
    TARGET_MIN_TRADES = 30         
    TARGET_MIN_WIN_RATE = 55.0     
    TARGET_MIN_PROFIT_FACTOR = 1.5 
    TARGET_MIN_SHARPE = 1.0        
    
    best_score = -1.0
    best_code = ""
    
    if os.path.exists(STRATEGY_FILE_PATH):
        with open(STRATEGY_FILE_PATH, 'r', encoding='utf-8') as f:
            best_code = f.read()

    for i in range(MAX_ITERATIONS):
        print(f"\n================================")
        print(f"--- 迴圈迭代第 {i+1}/{MAX_ITERATIONS} 次 ---")
        print(f"================================")
        
        metrics = run_backtest()
        if metrics is None:
            print("⚠️ 回測無法順利執行，暫停自動優化")
            break
            
        score = calculate_score(metrics)
        print(f"📊 當前表現：{metrics} | 綜合評分: {score:.4f} (歷史最佳: {best_score:.4f})")
        
        trades = metrics.get('trades', 0)
        win_rate = metrics.get('win_rate', 0)
        profit_factor = metrics.get('profit_factor', 0)
        sharpe = metrics.get('sharpe', 0)
        
        if (trades >= TARGET_MIN_TRADES and 
            win_rate >= TARGET_MIN_WIN_RATE and 
            profit_factor >= TARGET_MIN_PROFIT_FACTOR and 
            sharpe >= TARGET_MIN_SHARPE):
            print("\n🎉【恭喜】已找到符合標準的優秀策略！")
            print(f"🎯 達成指標 -> 交易數: {trades}, 勝率: {win_rate}%, 盈虧比: {profit_factor}, 夏普值: {sharpe}")
            print("✨ 自動優化閉環順利結束，策略已鎖定。")
            break
            
        improved = False
        if score > best_score:
            best_score = score
            with open(STRATEGY_FILE_PATH, 'r', encoding='utf-8') as f:
                best_code = f.read()
            print("🌟【新紀錄】超越歷史最佳評分！已更新最佳策略版本。")
            improved = True
        else:
            print("📉 表現未超越歷史最佳，將還原至最佳版本繼續優化。")
            update_strategy_file(best_code)
            
        with open(STRATEGY_FILE_PATH, 'r', encoding='utf-8') as f:
            current_code = f.read()
            
        print("🤖 Claude 正在診斷並改寫策略...")
        new_code = ask_ai_to_fix_code(current_code, metrics, score, best_score, improved)
        
        if validate_code(new_code):
            update_strategy_file(new_code)
            print("✨ 程式碼驗證通過並已更新，準備進入下一輪迴圈...")
        else:
            print("⚠️ 產出的程式碼驗證失敗（不完整或有語法錯誤），自動略過並維持上一版程式碼繼續運行。")