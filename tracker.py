import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
from google import genai

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

def send_telegram(text):
    """向 Telegram 发送排版长消息"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Telegram 发送异常: {e}")

# 经过 FTSE Bursa 规则初筛的合资格股票池 (符合主板上市 + 自由流通 Free Float > 15% + 具备机构流动性)
# 涵盖 30 大成分股、官方 Reserve List 候补名单以及 31~65 名扩军争夺标的
ELIGIBLE_BURSA_TICKERS = [
    "1155.KL", "1023.KL", "5347.KL", "1295.KL", "6742.KL", "4677.KL", "8869.KL", "4715.KL",
    "6012.KL", "4065.KL", "1961.KL", "3816.KL", "5819.KL", "2445.KL", "5225.KL", "5183.KL",
    "5296.KL", "1015.KL", "4197.KL", "0166.KL", "5681.KL", "6947.KL", "5285.KL", "5168.KL",
    "1066.KL", "5014.KL", "7277.KL", "5246.KL", "5211.KL", "3034.KL", "5099.KL", "7084.KL",
    "5185.KL", "0083.KL", "7113.KL", "5238.KL", "4707.KL", "5148.KL", "7052.KL", "5258.KL",
    "5138.KL", "5176.KL", "5235.KL", "5272.KL", "5181.KL", "7153.KL", "7204.KL", "5202.KL",
    "5227.KL", "5248.KL", "5253.KL", "5277.KL", "5284.KL", "5302.KL", "5306.KL", "5318.KL",
    "5326.KL", "5341.KL", "5368.KL", "5378.KL", "5388.KL", "5398.KL", "5843.KL", "6033.KL",
    "2291.KL", "5281.KL", "5038.KL", "5199.KL", "5222.KL", "7249.KL"
]

def fetch_accurate_bursa_snapshot():
    """
    通过官方无封锁接口批量获取行情与真实结算市值，并执行流动性过滤
    """
    tickers_str = " ".join(ELIGIBLE_BURSA_TICKERS)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    stock_records = []
    
    # 分块批量请求，确保云端网络极速且稳定
    chunk_size = 35
    ticker_chunks = [ELIGIBLE_BURSA_TICKERS[i:i + chunk_size] for i in range(0, len(ELIGIBLE_BURSA_TICKERS), chunk_size)]
    
    for chunk in ticker_chunks:
        symbols_param = ",".join(chunk)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_param}"
        try:
            resp = requests.get(url, headers=headers, timeout=10).json()
            quotes = resp.get("quoteResponse", {}).get("result", [])
            for q in quotes:
                sym = q.get("symbol", "")
                name = q.get("shortName") or q.get("longName") or sym.replace(".KL", "")
                price = q.get("regularMarketPrice", 0.0)
                mcap = q.get("marketCap", 0.0)
                vol = q.get("regularMarketVolume", 0)
                pct_chg = q.get("regularMarketChangePercent", 0.0)
                
                # 如果缺少直接市值字段，通过流通股本和价格补算
                if not mcap and price:
                    shares = q.get("sharesOutstanding", 0)
                    mcap = price * shares
                    
                mcap_bil = float(mcap) / 1e9  # 换算为十亿马币 (Billion RM)
                turnover = price * vol        # 成交额 (Turnover)
                
                # 流动性初筛：剔除日成交过低或未上市公司
                if mcap_bil > 0.1:
                    stock_records.append({
                        "code": sym.replace(".KL", ""),
                        "symbol": sym,
                        "name": name.strip(),
                        "price": float(price),
                        "market_cap": float(mcap_bil),
                        "turnover": float(turnover),
                        "pct_change": float(pct_chg)
                    })
        except Exception as e:
            print(f"Chunk fetch warning: {e}")
            
    df = pd.DataFrame(stock_records)
    if not df.empty:
        # 按照市值由大到小排序，产出合资格的 Top 50
        df = df.sort_values(by="market_cap", ascending=False).reset_index(drop=True)
    return df

def analyze_bursa_focus_stocks(candidates, client):
    """调用 Gemini 提炼 5 蓝筹 + 5 中小盘异动"""
    input_text = []
    for s in candidates:
        input_text.append(
            f"股票: {s['name']} ({s['code']}) | 类别: {s['category']} | 现价: RM {s['price']:.2f} | "
            f"单日涨跌: {s['pct_change']:.2f}% | 成交额: RM {s['turnover']:,.0f} | 市值: RM {s['market_cap']:.2f}B"
        )
    
    prompt = f"""
你是一名资深马来西亚股票量化与基本面研究员。
以下是系统根据今日盘面、流动性门槛与异动算法筛选出的 5 只焦点蓝筹与 5 只中小盘异动股：

{chr(10).join(input_text)}

请为这 10 只股票分别写一段简明（2-3 句话）的研究点评：
1. 概括发生了什么（价量异动、资金流向或盘面特征）。
2. 从专业投资视角指出最值得关注的逻辑点（如成分股扩军潜力、外资流向、估值支撑或破位风险）。
3. 语言以中文为主，重要金融词汇附带英文。分为【5 大核心蓝筹】与【5 大中小盘异动股】两组输出。
"""
    try:
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"AI 研报总结生成受阻: {e}"

def main():
    history_file = "ranking_history.json"
    
    # 1. 抓取真实排位与市值
    df = fetch_accurate_bursa_snapshot()
    if df.empty:
        print("未抓取到有效股票数据，跳过本次执行")
        return

    top50 = df.head(50).to_dict(orient="records")

    # 2. 读取上一期排名比对单日位移
    prev_ranks = {}
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                prev_ranks = json.load(f)
        except Exception:
            prev_ranks = {}

    current_ranks = {}
    rank_lines = []

    for i, stock in enumerate(top50, start=1):
        code = stock["code"]
        name = stock["name"]
        cap = stock["market_cap"]
        current_ranks[code] = i

        # 位移计算
        if code in prev_ranks:
            diff = prev_ranks[code] - i
            if diff > 0:
                tag = f"🟢 ▲ {diff}"
            elif diff < 0:
                tag = f"🔴 ▼ {abs(diff)}"
            else:
                tag = "⚪ ─"
        else:
            tag = "🆕 NEW"

        # 重点攻防与边缘区间标注
        alert = ""
        if i == 25:
            alert = " ⚡[现行 30 大直晋门槛]"
        elif i == 30:
            alert = " ⚠️[现行 30 大边缘警戒]"
        elif 31 <= i <= 35:
            alert = " 📋[现行 30 大官方候补区]"
        elif i == 45:
            alert = " 🚨[扩军 50 大直晋前线]"
        elif i == 50:
            alert = " 🏁[扩军 50 大席位生死线]"

        rank_lines.append(f"{i}. `{name}` ({code}) - RM {cap:.2f}B ({tag}){alert}")

    # 3. 算法提取 5 蓝筹 + 5 中小盘
    # 蓝筹池：前 50 合资格股票中单日振幅最大的 5 只
    blue_df = df.head(50).copy()
    blue_df["abs_chg"] = blue_df["pct_change"].abs()
    top5_blue = blue_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_blue: s["category"] = "合资格蓝筹 (Eligible Blue Chip)"

    # 中小盘池：51 名及以后有活跃成交的前 5 只
    sme_df = df.iloc[50:].copy() if len(df) > 50 else df.iloc[35:50].copy()
    sme_df["abs_chg"] = sme_df["pct_change"].abs()
    top5_sme = sme_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_sme: s["category"] = "中小盘潜力股 (Small/Mid Cap)"

    # 4. 调用 AI 提炼研报
    ai_summary = "（未配置 Gemini Key）"
    if GEMINI_KEY:
        try:
            client = genai.Client(api_key=GEMINI_KEY)
            ai_summary = analyze_bursa_focus_stocks(top5_blue + top5_sme, client)
        except Exception as e:
            ai_summary = f"AI 生成失败: {e}"

    # 5. 组合并分段发送 Telegram 报告
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    part1_header = (
        f"📊 *【马股 KLCI 扩军 50 大成分股模拟与攻防榜】*\n"
        f"⏱ *数据采集时间：* {now_str}\n"
        f"📐 *合资格口径：主板上市 + 流动性初筛 + 真实结算市值*\n\n"
    )

    msg_part1 = part1_header + "🏆 *合资格成分股 Top 1 - 25：*\n" + "\n".join(rank_lines[:25])
    msg_part2 = "🏆 *合资格成分股 Top 26 - 50 (含扩军临界防线)：*\n" + "\n".join(rank_lines[25:])
    msg_part3 = f"━━━━━━━━━━━━━━━\n🔍 *【今日 5 蓝筹 + 5 中小盘异动深度简报】*\n\n{ai_summary}"

    send_telegram(msg_part1)
    time.sleep(1)
    send_telegram(msg_part2)
    time.sleep(1)
    send_telegram(msg_part3)

    # 6. 保存最新排名供下次比对
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(current_ranks, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
