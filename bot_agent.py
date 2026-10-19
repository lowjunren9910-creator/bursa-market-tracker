import os
import telebot
import yfinance as yf
from google import genai
from datetime import datetime

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

def get_stock_data(query):
    clean = query.strip().upper()
    ticker_code = clean if clean.endswith(".KL") else clean + ".KL"
    
    stock = yf.Ticker(ticker_code)
    info = stock.info
    
    if not info or ("currentPrice" not in info and "regularMarketPrice" not in info):
        return None, f"⚠️ 未能在马股匹配到代号 `{ticker_code}`，请输入纯数字代码（如 `1155`、`6742`）或官方代号。"

    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
    prev_close = info.get("regularMarketPreviousClose") or price
    pct_chg = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
    
    return {
        "ticker": ticker_code,
        "name": info.get("shortName") or info.get("longName") or ticker_code,
        "sector": info.get("sector", "未分类产业"),
        "industry": info.get("industry", "未分类领域"),
        "price": price,
        "pct_change": pct_chg,
        "market_cap": (info.get("marketCap") or 0.0) / 1e9,
        "pe": info.get("trailingPE", "N/A"),
        "forward_pe": info.get("forwardPE", "N/A"),
        "pb": info.get("priceToBook", "N/A"),
        "div_yield": (info.get("dividendYield") or 0.0) * 100,
        "roe": (info.get("returnOnEquity") or 0.0) * 100,
        "rev_growth": (info.get("revenueGrowth") or 0.0) * 100,
        "debt_equity": info.get("debtToEquity", "N/A"),
        "summary": info.get("longBusinessSummary", "暂无官方业务描述")[:250]
    }, None

def run_multi_agent(data):
    prompt = f"""
你是由四位性格迥异的顶尖马来西亚股票基金经理与投委会主席组成的专业研究团队。
截至今天，该股通过交易所公开数据采集到的 100% 真实确凿的财务与行情数据如下：

- 公司名称: {data['name']} ({data['ticker']})
- 产业分类: {data['sector']} / 细分行业: {data['industry']}
- 当前股价: RM {data['price']:.2f} (单日涨跌: {data['pct_change']:.2f}%)
- 总市值: RM {data['market_cap']:.2f} Billion
- 估值指标: 静态PE={data['pe']}, 远期PE={data['forward_pe']}, PB={data['pb']}
- 财务指标: 股息率={data['div_yield']:.2f}%, ROE={data['roe']:.2f}%, 营收增速={data['rev_growth']:.2f}%, 净负债比={data['debt_equity']}
- 业务摘要: {data['summary']}

请严格按照以下结构输出研报（中文为主，核心金融术语附英文）：

### 1. 产业定位与核心赛道
简述其所属产业地位与核心商业模式。

### 2. 真实业绩透视与财务体检
结合上述真实 PE、PB、ROE 与负债数据，客观评价其盈利质量与财务安全度。

### 3. 未来展望与估值研判（是否低估？）
指出行业催化剂与风险，判断当前估值处于合理、低估还是高估区间。

### 4. 投委会闭门研讨：4 位不同个性经理人观点辩论
- 🛡️ **稳健价值猎手（Value Hawk）**：极度保守，严苛挑剔负债、现金流与股息安全垫。
- 🚀 **激进成长开拓者（Growth Bull）**：胆大进取，极力挖掘赛道空间、行业天花板与未来催化剂。
- ⚖️ **冷酷量化风控官（Quant Officer）**：客观中立，仅用数字分位数、回撤与风险补偿下达评级。
- 🎯 **实战波段交易员（Tactical Trader）**：关注即时价量、短中期支撑阻力与资金博弈进出点。
"""
    res = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return res.text

@bot.message_handler(func=lambda msg: True)
def handle_msg(msg):
    # 安全验证，仅响应你自己的 Telegram ID
    if str(msg.chat.id) != str(ALLOWED_CHAT_ID):
        return
        
    code = msg.text.strip()
    wait = bot.reply_to(msg, f"🔍 正在调取 `{code}` 官方财务数据并召集 4 位基金经理，请稍候约 5 秒...")
    
    try:
        data, err = get_stock_data(code)
        if err:
            bot.edit_message_text(err, chat_id=msg.chat.id, message_id=wait.message_id, parse_mode="Markdown")
            return
            
        report = run_multi_agent(data)
        out = (
            f"📈 *【{data['name']} ({data['ticker']}) 投研会诊深度备忘录】*\n"
            f"⏱ *数据采集时间：* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"{report}"
        )
        if len(out) > 4000:
            bot.send_message(msg.chat.id, out[:4000], parse_mode="Markdown")
            bot.send_message(msg.chat.id, out[4000:], parse_mode="Markdown")
        else:
            bot.send_message(msg.chat.id, out, parse_mode="Markdown")
            
        bot.delete_message(chat_id=msg.chat.id, message_id=wait.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ 运行异常: {e}", chat_id=msg.chat.id, message_id=wait.message_id)

if __name__ == "__main__":
    print("Agent Bot is listening...")
    bot.infinity_polling()
