import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
import yfinance as yf
from google import genai
from datetime import datetime

# 1. 环境变量
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# 2. 轻量 Web 端口保活（供 Render 免费层健康检查）
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bursa Multi-Agent Bot is Active & Running 24/7!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# 3. 常见马股名称映射字典
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
    raw = user_input.strip().upper().replace(" ", "")
    if raw.isdigit():
        return f"{raw}.KL"
    if raw in FAST_NAME_MAP:
        return FAST_NAME_MAP[raw]
    if raw.endswith(".KL"):
        return raw
    # 模糊搜索
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
    return f"{raw}.KL"

def fetch_stock_financials(query):
    ticker_sym = resolve_ticker(query)
    
    # 优先使用带完整请求头的 Yahoo 官方 Query API（防止云服务器被 Yahoo 反爬）
    price = 0.0
    prev_close = 0.0
    mcap = 0.0
    name = ticker_sym.replace(".KL", "")
    info = {}

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_sym}?interval=1d&range=5d"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=8).json()
        meta = res["chart"]["result"][0]["meta"]
        price = float(meta.get("regularMarketPrice", 0.0))
        prev_close = float(meta.get("chartPreviousClose", price))
        name = meta.get("shortName") or meta.get("symbol", name)
    except Exception:
        pass

    # 辅以 yfinance 提取财务报表深度指标
    stock = yf.Ticker(ticker_sym)
    try:
        info = stock.info or {}
        if price == 0.0:
            price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
            prev_close = info.get("regularMarketPreviousClose") or price
        mcap = (info.get("marketCap") or 0.0) / 1e9
        name = info.get("shortName") or name
    except Exception:
        pass

    if price == 0.0:
        return None, f"⚠️ 未能在马股数据库检索到 `{query}`（尝试识别代码: `{ticker_sym}`），请确认代码是否正确（例如输入 `1155`、`5225`、`YTL`）。"

    pct_chg = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

    return {
        "ticker": ticker_sym,
        "name": name,
        "sector": info.get("sector", "马股核心板块"),
        "industry": info.get("industry", "相关细分领域"),
        "price": float(price),
        "pct_change": float(pct_chg),
        "market_cap": float(mcap),
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
    today_str = datetime.now().strftime("%Y年%m月%d日")
    
    prompt = f"""
你是由四位性格迥异、投资逻辑大相径庭的顶尖马来西亚股票基金经理与投委会主席组成的研究团队。
截至今天（{today_str}），该股通过交易所公开数据采集到的 100% 真实确凿的财务与行情数据如下：

【标的真实数据概览】
- 公司名称: {data['name']} (马股代码: {data['ticker']})
- 产业分类: {data['sector']} / 细分行业: {data['industry']}
- 当前股价: RM {data['price']:.2f} (单日涨跌幅: {data['pct_change']:.2f}%)
- 总市值: RM {data['market_cap']:.2f} Billion
- 估值指标: 静态市盈率 PE={data['pe']}, 远期市盈率 Forward PE={data['forward_pe']}, 市净率 PB={data['pb']}
- 财务指标: 股息收益率={data['div_yield']:.2f}%, 净资产收益率 ROE={data['roe']:.2f}%, 营收同比增速={data['rev_growth']:.2f}%, 债务权益比={data['debt_equity']}
- 业务背景简述: {data['summary']}

请严格按照以下 4 个板块输出深度报告（中文为主，核心金融专业术语附带英文）：

### 1. 产业定位与核心赛道 (Industry & Sector Overview)
简明扼要指出该公司属于什么产业，在马股产业链上的定位以及核心竞争壁垒。

### 2. 真实业绩透视与财务体检 (Financial Health & Earnings)
结合上方真实给出的 PE、PB、ROE、营收增速与负债比率，客观剖析其盈利质量、资产负债表健康度。

### 3. 未来展望与估值研判（是否低估？）(Outlook & Valuation)
指出未来行业催化剂与潜在下行风险，基于当前估值水平给出其是否处于合理、低估还是估值透支区间的明确判断。

### 4. 投委会闭门研讨：4 位不同个性经理人观点辩论 (Multi-Agent Panel Debate)
请让以下 4 位角色围绕该股截至今天的真实表现展开针锋相对的辩论：
- 🛡️ **稳健价值猎手（Value & Dividend Hawk）**：极度保守、风险厌恶。重点苛责负债率、现金流以及股息率是否具备足够安全垫。
- 🚀 **激进成长开拓者（Aggressive Growth Bull）**：胆大进取。极力挖掘行业天花板、政策利好与未来想象空间，愿意为高成长支付溢价。
- ⚖️ **冷酷量化风控官（Quant & Risk Officer）**：客观中立、严谨理性。仅以估值分位数、下行回撤风险与赔率下达风险评级。
- 🎯 **实战波段交易员（Tactical Momentum Trader）**：关注即时价量走势、短中期支撑与阻力位，给出明确的短中线博弈与防守止损建议。
"""

    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 *欢迎使用 Bursa 多角色 AI 投研会诊终端！*\n\n"
        "随时在此窗口输入马股 4 位数字代码或名称（例如 `1155`、`5296`、`YTL`、`NOTION`），\n"
        "我将为你调取最新官方财务数据，并由 4 位不同性格与投资风格的基金经理展开深度会诊！",
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda msg: True)
def handle_stock_inquiry(msg):
    if str(msg.chat.id) != str(ALLOWED_CHAT_ID):
        bot.reply_to(msg, "⚠️ 该 Bot 设定为私人专属投研终端，未授权的用户无法触发分析。")
        return

    query_text = msg.text.strip()
    wait_msg = bot.reply_to(msg, f"🔍 正在调取 `{query_text}` 官方数据并召集 4 位基金经理，请稍候约 5 秒...")

    try:
        data, err = fetch_stock_financials(query_text)
        if err:
            bot.edit_message_text(err, chat_id=msg.chat.id, message_id=wait_msg.message_id, parse_mode="Markdown")
            return

        report = run_multi_agent_committee(data)
        
        final_reply = (
            f"📈 *【{data['name']} ({data['ticker']}) 投研会诊深度备忘录】*\n"
            f"⏱ *数据采集时间：* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"{report}"
        )
        
        if len(final_reply) > 4000:
            bot.send_message(msg.chat.id, final_reply[:4000], parse_mode="Markdown")
            bot.send_message(msg.chat.id, final_reply[4000:], parse_mode="Markdown")
        else:
            bot.send_message(msg.chat.id, final_reply, parse_mode="Markdown")
            
        bot.delete_message(chat_id=msg.chat.id, message_id=wait_msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ 运行异常: {e}", chat_id=msg.chat.id, message_id=wait_msg.message_id)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    print("Bursa Stock Agent Bot is successfully listening...")
    bot.infinity_polling()
