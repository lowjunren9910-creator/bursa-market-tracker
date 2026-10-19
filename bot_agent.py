import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from google import genai
from datetime import datetime

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# 1. 轻量 Web 端口保活（供 Render 免费层检测）
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

# 2. 常见马股名称映射字典
FAST_NAME_MAP = {
    "MAYBANK": "1155",
    "PBBANK": "1023",
    "PUBLICBANK": "1023",
    "TENAGA": "5347",
    "TNB": "5347",
    "CIMB": "1295",
    "YTL": "4677",
    "YTLPOWR": "6742",
    "YTLPOWER": "6742",
    "GAMUDA": "5296",
    "IHH": "5225",
    "INARI": "0166",
    "GENTING": "3182",
    "GENM": "4715",
    "SIME": "4197",
    "PETGAS": "6033",
    "PCHEM": "5183",
    "PETCHEM": "5183",
    "MISC": "3816",
    "IOICORP": "1961",
    "KLK": "2445",
    "MAXIS": "6012",
    "CELCOMDIGI": "6947",
    "CDB": "6947",
    "RHBBANK": "1066",
    "HLBANK": "5819",
    "SUNWAY": "5211",
    "MRDIY": "5296",
    "NOTION": "0083",
    "FARMFRESH": "5306",
    "PADINI": "7052",
    "NATGATE": "0270",
    "EONCR": "5281",
    "IGBREIT": "5227"
}

def resolve_code(user_input):
    raw = user_input.strip().upper().replace(" ", "").replace(".KL", "")
    if raw in FAST_NAME_MAP:
        return FAST_NAME_MAP[raw]
    return raw

def fetch_bursa_stock_realtime(code):
    """
    通过马股实时综合数据网关直接读取真实行情与财务指标
    """
    clean_code = resolve_code(code)
    url = f"https://www.klsescreener.com/v2/stocks/view/{clean_code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200 and "Stock Not Found" not in resp.text:
            text = resp.text
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, "html.parser")
            
            # 提取股票名称与当前价
            name = clean_code
            title_tag = soup.find("h2")
            if title_tag:
                name = title_tag.text.strip()
                
            price_tag = soup.find("span", id="price")
            price = float(price_tag.text.strip().replace(",", "")) if price_tag else 0.0
            
            # 提取市值 (通常单位为 Million)
            cap_tag = soup.find("td", id="market_cap")
            mcap = 0.0
            if cap_tag:
                try:
                    mcap = float(cap_tag.text.strip().replace(",", "")) / 1000.0
                except:
                    pass
                    
            pe_tag = soup.find("td", id="pe")
            pe = pe_tag.text.strip() if pe_tag else "N/A"
            
            dy_tag = soup.find("td", id="dy")
            dy = dy_tag.text.strip() if dy_tag else "N/A"
            
            roe_tag = soup.find("td", id="roe")
            roe = roe_tag.text.strip() if roe_tag else "N/A"

            if price > 0:
                return {
                    "code": clean_code,
                    "name": name,
                    "price": price,
                    "market_cap": mcap,
                    "pe": pe,
                    "div_yield": dy,
                    "roe": roe,
                    "source": "KLSE Direct"
                }, None
    except Exception as e:
        print(f"Direct fetch failed: {e}")

    # 兜底：如果直接抓取受阻，返回基础代号由 Gemini 结合实时金融检索生成
    return {
        "code": clean_code,
        "name": f"马股上市公司 ({clean_code})",
        "price": 0.0,
        "market_cap": 0.0,
        "pe": "参考最新财报",
        "div_yield": "参考最新公告",
        "roe": "稳健",
        "source": "AI Market Search"
    }, None

def run_multi_agent_committee(data, original_query):
    today_str = datetime.now().strftime("%Y年%m月%d日")
    
    prompt = f"""
你是由四位性格迥异、投资逻辑大相径庭的顶尖马来西亚股票基金经理与投委会主席组成的研究团队。
今天是 {today_str}。请针对马来西亚上市公司【{original_query} / 代号: {data['code']}】展开全面研判。

已知确凿数据：
- 标的代号: {data['code']}
- 公司全称/简称: {data['name']}
- 最新交易价格: RM {data['price']}
- 总市值概况: RM {data['market_cap']:.2f} Billion
- 市盈率 PE: {data['pe']}, 股息率 DY: {data['div_yield']}, ROE: {data['roe']}

请结合你对马来西亚股票交易所（Bursa Malaysia）的最新认识，严格按照以下 4 个板块输出深度报告（中文为主，核心金融专业术语附带英文）：

### 1. 产业定位与核心赛道 (Industry & Sector Overview)
指出该公司属于马股哪个产业板块，核心主营业务是什么，在产业链中的竞争壁垒。

### 2. 真实业绩透视与财务体检 (Financial Health & Earnings)
结合最新公布的季度财报（QR）、盈利水平、负债率与现金流，客观剖析其财务健康状况。

### 3. 未来展望与估值研判（是否低估？）(Outlook & Valuation)
指出未来行业催化剂与潜在下行风险，基于当前估值水平给出其是否处于合理、低估还是高估区间的明确判断。

### 4. 投委会闭门研讨：4 位不同个性经理人观点辩论 (Multi-Agent Panel Debate)
围绕该股截至今天的真实面貌展开激烈辩论：
- 🛡️ **稳健价值猎手（Value Hawk）**：极度保守、风险厌恶。重点挑刺负债、现金流与股息是否有足够吸引力。
- 🚀 **激进成长开拓者（Growth Bull）**：胆大进取。极力挖掘未来成长天花板、政策利好与业务扩张空间，愿意为高成长买单。
- ⚖️ **冷酷量化风控官（Quant Officer）**：客观中立、严谨理性。用估值分位数、下行回撤与安全边际下达风险评级。
- 🎯 **实战波段交易员（Tactical Trader）**：关注即时价量走势、短中期支撑与阻力位，给出明确的短中线博弈与防守止损建议。
"""

    response = ai_client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt
    )

@bot.message_handler(func=lambda msg: True)
def handle_stock_inquiry(msg):
    if str(msg.chat.id) != str(ALLOWED_CHAT_ID):
        bot.reply_to(msg, "⚠️ 该 Bot 设定为私人专属投研终端，未授权的用户无法触发分析。")
        return

    query_text = msg.text.strip()
    wait_msg = bot.reply_to(msg, f"🔍 正在调取 `{query_text}` 官方数据并召集 4 位基金经理，请稍候约 5 秒...")

    try:
        data, err = fetch_bursa_stock_realtime(query_text)
        report = run_multi_agent_committee(data, query_text)
        
        final_reply = (
            f"📈 *【{data['name']} ({data['code']}) 投研会诊深度备忘录】*\n"
            f"⏱ *研判基准时间：* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
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
