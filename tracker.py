import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
import yfinance as yf
from google import genai

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

def send_telegram(text):
    """通过 Telegram API 发送长文本消息"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            print(f"Telegram 发送失败: {resp.text}")
    except Exception as e:
        print(f"网络异常: {e}")

def get_bursa_top_stocks():
    """
    通过雅虎财经批量拉取马股核心候选池的真实收盘价与官方总市值
    覆盖 KLCI 30大成分股、扩军候选名单以及主要中盘股
    """
    # 覆盖马股前 80 大重点蓝筹与中盘股的代号清单
    tickers = [
        "1155.KL", "1023.KL", "5347.KL", "1295.KL", "6742.KL", "4677.KL", "8869.KL", "4715.KL",
        "6012.KL", "4065.KL", "1961.KL", "3816.KL", "5819.KL", "2445.KL", "5225.KL", "5183.KL",
        "5296.KL", "1015.KL", "4197.KL", "0166.KL", "5681.KL", "6947.KL", "5285.KL", "5168.KL",
        "1066.KL", "5014.KL", "7277.KL", "5246.KL", "5211.KL", "3034.KL", "5099.KL", "7084.KL",
        "5185.KL", "0083.KL", "7113.KL", "5238.KL", "4707.KL", "5148.KL", "7052.KL", "5258.KL",
        "5138.KL", "5176.KL", "5235.KL", "5272.KL", "5181.KL", "7153.KL", "7204.KL", "5202.KL",
        "5227.KL", "5248.KL", "5253.KL", "5277.KL", "5284.KL", "5302.KL", "5306.KL", "5318.KL",
        "5326.KL", "5341.KL", "5368.KL", "5378.KL", "5388.KL", "5398.KL", "5843.KL", "6033.KL"
    ]
    
    print("正在拉取马股官方实时/收盘数据...")
    stocks = []
    
    # 批量下载行情（比单只拉取快 10 倍以上，且不易被限制）
    data = yf.Tickers(" ".join(tickers))
    
    for sym in tickers:
        try:
            info = data.tickers[sym].info
            # 严格获取交易所官方总市值 (marketCap)
            mcap = info.get("marketCap")
            if not mcap:
                # 备用方案：以当前价格乘以实际发行股本计算
                price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
                shares = info.get("sharesOutstanding") or 0
                mcap = price * shares
            
            if mcap and mcap > 0:
                price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
                prev_close = info.get("regularMarketPreviousClose") or price
                pct_chg = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
                name = info.get("shortName") or sym.replace(".KL", "")
                vol = info.get("regularMarketVolume") or 0
                
                stocks.append({
                    "code": sym.replace(".KL", ""),
                    "symbol": sym,
                    "name": name.strip(),
                    "price": float(price),
                    "market_cap": float(mcap) / 1e9, # 统一转换为十亿马币 (Billion RM)
                    "volume": float(vol),
                    "pct_change": float(pct_chg)
                })
        except Exception:
            continue
            
    df = pd.DataFrame(stocks)
    # 按真实市值降序排序
    df = df.sort_values(by="market_cap", ascending=False).reset_index(drop=True)
    return df

def analyze_candidates_with_ai(candidates, client):
    """调用 Gemini 生成专业研报点评"""
    summary_input = []
    for s in candidates:
        summary_input.append(
            f"股票: {s['name']} ({s['code']}) | 类别: {s['category']} | 现价: RM {s['price']:.2f} | "
            f"单日涨跌: {s['pct_change']:.2f}% | 成交量: {s['volume']:,.0f} 股 | 市值: RM {s['market_cap']:.2f}B"
        )
    
    prompt = f"""
你是一名资深马来西亚股票量化与基本面研究员。
以下是系统根据今日真实结算数据筛选出的 5 只焦点蓝筹与 5 只中小盘异动股：

{chr(10).join(summary_input)}

请为这 10 只股票分别写一段简明（2-3 句话）的研究点评：
1. 总结发生了什么（价量表现与盘面特征）。
2. 从专业投资视角指出最值得研究或留意的逻辑点（如估值支撑、外资动向、防守/进攻属性、阻力与支撑位）。
3. 语言以中文为主，重要金融词汇附带英文。分为【5 大核心蓝筹】与【5 大中小盘异动股】两组输出。
"""
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt
    )
    return response.text

def main():
    history_file = "ranking_history.json"
    
    # 1. 抓取真实数据并排序
    df = get_bursa_top_stocks()
    if df.empty:
        print("未获取到股票数据，退出流程")
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

        # KLCI 30大成分股攻防与候补临界区标记
        alert = ""
        if i == 25:
            alert = " ⚡[突破晋级线 Rank 25]"
        elif i == 30:
            alert = " ⚠️[成分股边缘警戒 Rank 30]"
        elif 31 <= i <= 35:
            alert = " 📋[候补名单区 Reserve List]"

        rank_lines.append(f"{i}. `{name}` ({code}) - RM {cap:.2f}B ({tag}){alert}")

    # 3. 算法提取 5 蓝筹 + 5 中小盘
    # 蓝筹池：前 50 名中单日振幅最大的 5 只
    blue_df = df.head(50).copy()
    blue_df["abs_chg"] = blue_df["pct_change"].abs()
    top5_blue = blue_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_blue: s["category"] = "蓝筹股 (Blue Chip)"

    # 中小盘池：51 名开外，按成交量与涨跌幅综合表现选前 5 只
    sme_df = df.iloc[50:].copy() if len(df) > 50 else df.iloc[30:50].copy()
    sme_df["abs_chg"] = sme_df["pct_change"].abs()
    top5_sme = sme_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_sme: s["category"] = "中小盘 (Small/Mid Cap)"

    # 4. 调用 AI 提炼研报
    ai_summary = "（未配置 API Key，跳过研报总结）"
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
        f"💡 *官方收盘市值口径，精确到小数点两位*\n\n"
    )
    
    # 拆分消息发送，避免超过 Telegram 消息长度限制
    msg_part1 = part1_header + "🏆 *市值排名 Top 1 - 25：*\n" + "\n".join(rank_lines[:25])
    msg_part2 = "🏆 *市值排名 Top 26 - 50 (含 KLCI 攻防临界区)：*\n" + "\n".join(rank_lines[25:])
    msg_part3 = f"━━━━━━━━━━━━━━━\n🔍 *【今日 5 蓝筹 + 5 中小盘异动深度简报】*\n\n{ai_summary}"

    send_telegram(msg_part1)
    time.sleep(1)
    send_telegram(msg_part2)
    time.sleep(1)
    send_telegram(msg_part3)

    # 6. 保存本次排名历史
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(current_ranks, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
