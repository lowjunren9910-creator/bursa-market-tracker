import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
import yfinance as yf
from google import genai
from datetime import datetime

# 1. 读取云端环境变量
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# 2. 启动极简 Web 端口（满足 Render Free Web Service 的 24/7 保活检测）
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bursa Multi-Agent Bot is Active & Running 24/7!".encode('utf-8'))

    def log_message(self, format, *args):
        # 屏蔽普通心跳请求日志，保持控制台整洁
        return

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# 3. 常见重点马股别名映射字典（优先毫秒级命中）
FAST_NAME_MAP = {
    "MAYBANK": "1155.KL",
    "PBBANK": "1023.KL",
    "PUBLICBANK": "1023.KL",
    "TENAGA": "5347.KL",
    "TNB": "5347.KL",
    "CIMB": "1295.KL",
    "YTL": "4677.KL",
    "YTLPOWR": "6742.KL",
    "YTLPOWER": "6742.KL",
    "GAMUDA": "5296.KL",
    "IHH": "5225.KL",
    "INARI": "0166.KL",
    "GENTING": "3182.KL",
    "GENM": "4715.KL",
    "SIME": "4197.KL",
    "PETGAS": "6033.KL",
    "PCHEM": "5183.KL",
    "PETCHEM": "5183.KL",
    "MISC": "3816.KL",
    "IOICORP": "1961.KL",
    "KLK": "2445.KL",
    "MAXIS": "6012.KL",
    "CELCOMDIGI": "6947.KL",
    "CDB": "6947.KL",
    "RHBBANK": "1066.KL",
    "HLBANK": "5819.KL",
    "SUNWAY": "5211.KL",
    "MRDIY": "5296.KL",
    "NOTION": "0083.KL",
    "FARMFRESH": "5306.KL",
    "PADINI": "7052.KL",
    "NATGATE": "0270.KL",
    "EONCR": "5281.KL",
    "IGBREIT": "5227.KL"
}

def resolve_ticker(user_input):
    """
    智能将用户输入的任何内容转为标准的马股 Yahoo Ticker:
    1. 纯数字代号 (如 1155 -> 1155.KL)
    2. 常用英文名称快速映射 (如 maybank -> 1155.KL)
    3. 任意生僻英文全网检索 (如 GENTING PLANTATIONS -> 2291.KL)
    """
    raw = user_input.strip().upper().replace(" ", "")
    
    # 纯数字代号（马股核心 4 位代号）
    if raw.isdigit():
        return f"{raw}.KL"
    
    # 别名字典匹配
    if raw in FAST_NAME_MAP:
        return FAST_NAME_MAP[raw]
    
    # 已自带 .KL 后缀
    if raw.endswith(".KL"):
        return raw
        
    # 联网模糊搜索任意马股全名或英文简称
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={user_input.strip()}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=5).json()
        for quote in resp.get("quotes", []):
            sym = quote.get("symbol", "")
            if sym.endswith(".KL"):
                return sym
    except Exception:
        pass
        
    # 兜底直接补 .KL
    return f"{raw}.KL"

def fetch_stock_financials(query):
    """
    准确调取目标马股的最新价格与核心财务数据
    """
    ticker_sym = resolve_ticker(query)
    stock = yf.Ticker(ticker_sym)
    
    price = None
    prev_close = None
    mcap = 0.0

    # 优先从 fast_info 提取最新成交价格（避免被反爬拦截）
    try:
        fast = stock.fast_info
        price = fast.last_price
        prev_close = fast.previous_close
        mcap = (fast.market_cap or 0.0) / 1e9
    except Exception:
        pass

    # 尝试从 info 字典中提取详细信息
    info = {}
    try:
        info = stock.info or {}
    except Exception:
        pass

    # 如果价格仍为空，采用近 5 日 K 线收盘价兜底
    if price is None or price == 0:
        hist = stock.history(period="5d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
            mcap = (info.get("marketCap", 0) or 0.0) / 1e9
        else:
            return None, f"⚠️ 未能在马股数据库检索到 `{query}`（对应代码 `{ticker_sym}`），请确认代码是否正确（例如直接输入纯数字代码 `1155`、`5225`、`0083`）。"

    pct_chg = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
    
    # 格式化核心财务指标
    return {
        "ticker": ticker_sym,
        "name": info.get("shortName") or info.get("longName") or ticker_sym.replace(".KL", ""),
        "sector": info.get("sector", "马股核心板块"),
        "industry": info.get("industry", "相关细分领域"),
        "price": float(price),
        "pct_change": float(pct_chg),
        "market_cap": float(mcap) if mcap > 0 else ((info.get("marketCap") or 0.0) / 1e9),
        "pe": info.get("trailingPE", "N/A"),
        "forward_pe": info.get("forwardPE", "N/A"),
        "pb": info.get("priceToBook", "N/A"),
        "div_yield": ((info.get("dividendYield") or 0.0) * 100),
        "roe": ((info.get("returnOnEquity") or 0.0) * 100),
        "rev_growth": ((info.get("revenueGrowth") or 0.0) * 100),
        "debt_equity": info.get("debtToEquity", "N/A"),
        "summary": info.get("longBusinessSummary", "马来西亚知名上市公司，主营业务涵盖行业核心供应链及商业运作。")[:280]
    }, None

def run_multi_agent_committee(data):
    """
    组织 4 位不同性格与投资风格的基金经理展开针对性研讨
    """
    today_str = datetime.now().strftime("%Y年%m月%d日")
    
    prompt = f"""
你是由四位性格迥异、投资逻辑大相径庭的顶尖马来西亚股票基金经理与投委会主席组成的研究团队。
截至今天（{today_str}），该股通过交易所公开数据采集到的 100% 真实确凿的财务与行情数据如下：

【标的真实数据概览】
- 公司名称: {data['name']} (马股代码:
