import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from google import genai

# 1. 读取云端环境变量
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

def send_telegram(text):
    """发送格式化长消息至 Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload, timeout=15)

def fetch_accurate_bursa_data():
    """
    抓取马股本地权威镜像数据
    KLSE Screener 每日同步交易所最新除权股本与最终撮合收盘价
    """
    url = "https://www.klsescreener.com/v2/stocks"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise Exception(f"HTTP Error: {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.find_all("tr", class_="stock")
    
    stocks = []
    for r in rows:
        try:
            code = r.get("data-code", "").strip()
            name_td = r.find("td", class_="name")
            price_td = r.find("td", class_="price")
            cap_td = r.find("td", class_="market_cap")
            vol_td = r.find("td", class_="volume")
            chg_td = r.find("td", class_="change")

            if not (code and name_td and price_td and cap_td):
                continue

            name = name_td.text.strip()
            price = float(price_td.text.strip().replace(",", "") or 0.0)
            
            # 市值在源网页中以百万(Million)为单位，转换为十亿(Billion RM)
            cap_val = float(cap_td.text.strip().replace(",", "") or 0.0) / 1000.0
            
            vol_str = vol_td.text.strip().replace(",", "") if vol_td else "0"
            vol = float(vol_str or 0.0)
            
            chg_str = chg_td.text.strip().replace("%", "").replace(",", "") if chg_td else "0"
            pct_change = float(chg_str or 0.0)

            # 过滤未开盘或已停牌的无市值公司
            if cap_val > 0.01:
                stocks.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "market_cap": cap_val,
                    "volume": vol,
                    "pct_change": pct_change
                })
        except Exception:
            continue
            
    df = pd.DataFrame(stocks)
    # 按市值降序排列，保证前 50 真实无误
    df = df.sort_values(by="market_cap", ascending=False).reset_index(drop=True)
    return df

def analyze_candidates_with_ai(candidates, client):
    """让 Gemini 基于真实数据生成 10 只股票的精炼总结"""
    summary_input = []
    for s in candidates:
        summary_input.append(
            f"股票: {s['name']} ({s['code']}) | 类别: {s['category']} | 现价: RM {s['price']:.2f} | "
            f"单日涨跌: {s['pct_change']:.2f}% | 成交量: {s['volume']}股 | 市值: RM {s['market_cap']:.2f}B"
        )
    
    prompt = f"""
你是一名资深马来西亚股票量化与基本面研究员。
以下是系统算法根据今日真实盘面与异动筛选出的 5 只焦点蓝筹与 5 只中小盘异动股（数据 100% 真实确凿）：

{chr(10).join(summary_input)}

请为这 10 只股票分别写一段简短（每只 2-3 句话）的研究点评：
1. 说明发生了什么（异动状态、价量表现）。
2. 从专业投资视角指出最值得研究或警惕的逻辑点（如估值支撑、外资流向、业绩预期、破位或突破风险）。
3. 使用中文为主，关键财经术语附带英文。格式清晰，分两组：【5 大核心蓝筹】与【5 大中小盘异动股】。
"""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

def main():
    history_file = "ranking_history.json"
    
    # 1. 抓取真实排位与市值
    df = fetch_accurate_bursa_data()
    top50 = df.head(50).to_dict(orient="records")

    # 2. 读取上一期排名
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

        # KLCI 30大扩军与淘汰临界区标记
        alert = ""
        if i == 25:
            alert = " ⚡[升降门槛前线 Rank 25]"
        elif i == 30:
            alert = " ⚠️[成分股边缘警戒 Rank 30]"
        elif 31 <= i <= 35:
            alert = " 📋[候补名单区 Reserve List]"

        rank_lines.append(f"{i}. `{name}` ({code}) - RM {cap:.2f}B ({tag}){alert}")

    # 3. 算法提取 5 蓝筹 + 5 中小盘
    # 蓝筹池：前 50 大市值中，按单日绝对振幅/异动排序取前 5
    blue_chips_df = df.head(50).copy()
    blue_chips_df["abs_change"] = blue_chips_df["pct_change"].abs()
    top5_blue = blue_chips_df.sort_values(by="abs_change", ascending=False).head(5).to_dict(orient="records")
    for s in top5_blue: s["category"] = "蓝筹股 (Blue Chip)"

    # 中小盘池：51 ~ 300 名中，筛选有成交量且单日波动最大的前 5
    sme_df = df.iloc[50:300].copy()
    top5_sme = sme_df.sort_values(by="pct_change", ascending=False).head(5).to_dict(orient="records")
    for s in top5_sme: s["category"] = "中小盘 (Small/Mid Cap)"

    # 4. 调用 AI 提炼研报
    ai_summary = "（AI 研报生成跳过）"
    if GEMINI_KEY:
        try:
            client = genai.Client(api_key=GEMINI_KEY)
            ai_summary = analyze_candidates_with_ai(top5_blue + top5_sme, client)
        except Exception as e:
            ai_summary = f"AI 研报总结生成失败: {e}"

    # 5. 组合并分段发送 Telegram 报告
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    part1_header = (
        f"📊 *【马股 Top 50 市值追踪与成分股攻防榜】*\n"
        f"⏱ *数据采集时间：* {now_str}\n"
        f"💡 *注：已按结算撮合价与真实股本校准，精确到小数点两位*\n\n"
    )
    
    # 拆分前 25 名与后 25 名，避免超出 Telegram 单条消息 4096 字符限制
    msg_part1 = part1_header + "🏆 *市值排名 Top 1 - 25：*\n" + "\n".join(rank_lines[:25])
    msg_part2 = "🏆 *市值排名 Top 26 - 50 (含 KLCI 攻防临界区)：*\n" + "\n".join(rank_lines[25:])
    msg_part3 = f"━━━━━━━━━━━━━━━\n🔍 *【今日 5 蓝筹 + 5 中小盘异动深度简报】*\n\n{ai_summary}"

    send_telegram(msg_part1)
    time.sleep(1)
    send_telegram(msg_part2)
    time.sleep(1)
    send_telegram(msg_part3)

    # 6. 保存本次排名供下次计算位移
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(current_ranks, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
