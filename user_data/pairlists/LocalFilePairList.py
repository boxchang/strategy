import json
import logging
from pathlib import Path
from typing import List
from freqtrade.plugins.pairlist.IPairList import IPairList

logger = logging.getLogger(__name__)

class LocalFilePairList(IPairList):
    """
    自定義本地檔案 Pairlist：直接讀取指定 JSON 內的 pairs，不作多餘的時間過期限制
    """

    def __init__(self, exchange, pairlistmanager, config: dict, pairlistconfig: dict, 
                 graph_modules: dict = None) -> None:
        super().__init__(exchange, pairlistmanager, config, pairlistconfig)
        
        # 讀取你在 config.json 裡面指定的檔案路徑，預設抓 watchlist.json
        self.file_path = pairlistconfig.get(
            'file_path', 
            "/freqtrade/user_data/pairs_list/watchlist.json"
        )

    @property
    def short_desc(self) -> str:
        return f"{self.__class__.__name__} - 從本地檔案讀取清單: {self.file_path}"

    def gen_pairlist(self, allowed_pairs: List[str]) -> List[str]:
        """
        每次刷新時直接讀取本地 JSON 檔案並過濾交易所合法合約
        """
        pairs = []
        path = Path(self.file_path)
        
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 支援純陣列或帶有 "pairs" key 的字典格式
                    if isinstance(data, list):
                        pairs = data
                    elif isinstance(data, dict):
                        pairs = data.get("pairs", [])
                logger.info(f"成功從 {self.file_path} 讀取到 {len(pairs)} 個交易對")
            else:
                logger.warning(f"找不到指定的清單檔案: {self.file_path}")
        except Exception as e:
            logger.error(f"讀取清單檔案失敗: {e}")

        # 確保讀出來的 pair 在當前交易所支援的 allowed_pairs 裡面（自動濾掉美股或不存在的合約）
        final_pairs = [p for p in pairs if p in allowed_pairs]
        return final_pairs

    def refresh_pairlist(self) -> None:
        pass