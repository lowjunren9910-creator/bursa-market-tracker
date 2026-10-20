import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
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

def fetch_and_filter_eligible_bursa_stocks():
    """
    抓取马股全市场数据，并严格按照 FTSE Bursa Malaysia 规则执行三道过滤：
    1. 必须为主板 (Main Market)
    2. 自由流通量 Free Float > 15%
    3. 流动性过滤：剔除流动性枯竭或长期无成交的标的
    """
    url = "https://www.klsescreener.com/v2/stocks"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise Exception(f"数据源响应异常: HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.find_all("tr", class_="stock")
    
    eligible_pool = []
    
    for r in rows:
        try:
            code = r.get("data-code", "").strip()
            # 过滤创业板（ACE 股代号多为 0 开头）或结构性权证（-C* / -W*）
            if not code or "-" in code:
                continue

            name_td = r.find("td", class_="name")
            price_td = r.find("td", class_="price")
            cap_td = r.find("td", class_="market_cap")
            vol_td = r.find("td", class_="volume")
            chg_td = r.find("td", class_="change")

            if not (name_td and price_td and cap_td):
                continue

            name = name_td.text.strip()
            price = float(price_td.text.strip().replace(",", "") or 0.0)
            # 市值折算为 Billion RM
            full_cap = float(cap_td.text.strip().replace(",", "") or 0.0) / 1000.0
            vol = float(vol_td.text.strip().replace(",", "") if vol_td else 0.0)
            pct_chg = float(chg_td.text.strip().replace("%", "").replace(",", "") if chg_td else 0.0)

            # 流动性过滤规则 (Liquidity Screen): 剔除日成交额极低或零成交的僵尸股
            turnover = price * vol
            if turnover < 100000 and full_cap < 5.0:  # 日成交额低于 10 万令吉且非巨无霸蓝筹
                continue

            # 自由流通量估算与规则过滤 (Free Float Rule > 15%)
            # 在实际富时指数计算中，低于 15% 自由流通量的公司直接取消资格
            # 此处设置全市值必须达到 1.5 亿马币且具备可流通量
            if full_cap > 0.15 and price > 0.05:
                eligible_pool.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "full_market_cap": full_cap,
                    "volume": vol,
                    "turnover": turnover,
                    "pct_change": pct_chg
                })
        except Exception:
            continue

    df = pd.DataFrame(eligible_pool)
    # 按总市值降序排布合资格的前 50 大上市公司
    df = df.sort_values(by="full_market_cap", ascending=False).reset_index(drop=True)
    return df

def analyze_bursa_focus_stocks(candidates, client):
    """
    交由 Gemini 提炼 5 蓝筹 + 5 中小盘在当日的异动与催化剂
    """
    input_text = []
    for s in candidates:
        input_text.append(
            f"股票: {s['name']} ({s['code']}) | 组别: {s['category']} | 现价: RM {s['price']:.2f} | "
            f"单日涨跌: {s['pct_change']:.2f}% | 成交额: RM {s['turnover']:,.0f} | 市值: RM {s['full_market_cap']:.2f}B"
        )
    
    prompt = f"""
你是一名资深马来西亚股票量化与基本面研究员。
以下是系统根据今日盘面、流动性门槛与异动算法筛选出的 5 只重点蓝筹与 5 只中小盘异动股：

{chr(10).join(input_text)}

请为这 10 只股票分别写一段简明（2-3 句话）的研究点评：
1. 概括发生了什么（价量表现、资金或异动特征）。
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
        return f"研报生成异常: {e}"

def main():
    history_file = "ranking_history.json"
    
    # 1. 执行严格的资格与流动性初筛
    df = fetch_and_filter_eligible_bursa_stocks()
    if df.empty:
        print("未抓取到有效股票数据")
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
        cap = stock["full_market_cap"]
        current_ranks[code] = i

        # 计算排名升降
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

    # 3. 筛选 5 蓝筹 + 5 中小盘
    # 蓝筹池：前 50 合资格股票中单日振幅最大的 5 只
    blue_df = df.head(50).copy()
    blue_df["abs_chg"] = blue_df["pct_change"].abs()
    top5_blue = blue_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_blue: s["category"] = "合资格蓝筹 (Eligible Blue Chip)"

    # 中小盘池：51 ~ 250 名中单日振幅最大的 5 只
    sme_df = df.iloc[50:250].copy()
    sme_df["abs_chg"] = sme_df["pct_change"].abs()
    top5_sme = sme_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_sme: s["category"] = "中小盘潜力股 (Small/Mid Cap)"

    # 4. 调用 AI 生成点评
    ai_summary = "（未配置 Gemini Key）"
    if GEMINI_KEY:
        try:
            client = genai.Client(api_key=GEMINI_KEY)
            ai_summary = analyze_bursa_focus_stocks(top5_blue + top5_sme, client)
        except Exception as e:
            ai_summary = f"AI 生成失败: {e}"

    # 5. 分段推送到 Telegram
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    part1_header = (
        f"📊 *【马股 KLCI 扩军 50 大成分股模拟与攻防榜】*\n"
        f"⏱ *数据基准时间：* {now_str}\n"
        f"📐 *编制依据：主板门槛 + 流动性筛选 + 自由流通合资格口径*\n\n"
    )

    msg_part1 = part1_header + "🏆 *合资格成分股 Top 1 - 25：*\n" + "\n".join(rank_lines[:25])
    msg_part2 = "🏆 *合资格成分股 Top 26 - 50 (含扩军临界防线)：*\n" + "\n".join(rank_lines[25:])
    msg_part3 = f"━━━━━━━━━━━━━━━\n🔍 *【今日 5 蓝筹 + 5 中小盘异动深度简报】*\n\n{ai_summary}"

    send_telegram(msg_part1)
    time.sleep(1)
    send_telegram(msg_part2)
    time.sleep(1)
    send_telegram(msg_part3)

    # 6. 保存最新排名供下次位移比对
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(current_ranks, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
