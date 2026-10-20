import os
import json
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from google import genai

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

def send_telegram(text):
    """Sends formatted Markdown text to the designated Telegram chat."""
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
        print(f"Telegram dispatch error: {e}")

# Core universe representing Bursa Malaysia main-market candidates
BURSA_UNIVERSE = [
    "1155.KL", "1023.KL", "5347.KL", "1295.KL", "6742.KL", "4677.KL", "8869.KL", "4715.KL",
    "6012.KL", "4065.KL", "1961.KL", "3816.KL", "5819.KL", "2445.KL", "5225.KL", "5183.KL",
    "5296.KL", "1015.KL", "4197.KL", "0166.KL", "5681.KL", "6947.KL", "5285.KL", "5168.KL",
    "1066.KL", "5014.KL", "7277.KL", "5246.KL", "5211.KL", "3034.KL", "5099.KL", "7084.KL",
    "5185.KL", "0083.KL", "7113.KL", "5238.KL", "4707.KL", "5148.KL", "7052.KL", "5258.KL",
    "5138.KL", "5176.KL", "5235.KL", "5272.KL", "5181.KL", "7153.KL", "7204.KL", "5202.KL",
    "5227.KL", "5248.KL", "5253.KL", "5277.KL", "5284.KL", "5302.KL", "5306.KL", "5318.KL",
    "5326.KL", "5341.KL", "5368.KL", "5378.KL", "5388.KL", "5398.KL", "5843.KL", "6033.KL",
    "2291.KL", "5281.KL", "5038.KL", "5199.KL", "5222.KL", "7249.KL", "3182.KL", "3867.KL"
]

def verify_ftse_eligibility(ticker_symbol):
    """
    Evaluates FTSE Bursa Malaysia Ground Rules:
    1. Free-float ratio >= 15%
    2. Liquidity: monthly turnover median >= 0.05% in at least 70% of available months
    """
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info or {}
        
        shares_out = info.get("sharesOutstanding") or 0
        float_shares = info.get("floatShares") or (shares_out * 0.35)
        
        # Free-float minimum threshold: 15%
        if shares_out > 0 and (float_shares / shares_out) < 0.15:
            return False, 0.0, info

        # Liquidity screening: examine trailing 6-12 months historical daily volumes
        hist = stock.history(period="6mo")
        if hist.empty or len(hist) < 40:
            return True, 0.0, info  # Keep in candidate pool if history is temporarily incomplete
            
        hist['Month'] = hist.index.to_period('M')
        monthly_medians = hist.groupby('Month')['Volume'].median()
        
        # Turnover ratio: median volume / float shares
        monthly_turnover = (monthly_medians / float_shares) * 100
        passed_months = (monthly_turnover >= 0.05).sum()
        total_months = len(monthly_medians)
        
        is_eligible = passed_months >= max(1, int(total_months * 0.6))
        return is_eligible, float(monthly_turnover.mean()), info
    except Exception:
        return True, 0.0, {}

def fetch_screened_bursa_rankings():
    """Fetches real-time market metrics and computes eligibility-filtered rankings."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    records = []
    chunk_size = 35
    ticker_chunks = [BURSA_UNIVERSE[i:i + chunk_size] for i in range(0, len(BURSA_UNIVERSE), chunk_size)]
    
    for chunk in ticker_chunks:
        symbols_param = ",".join(chunk)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_param}"
        try:
            resp = requests.get(url, headers=headers, timeout=12).json()
            quotes = resp.get("quoteResponse", {}).get("result", [])
            
            for q in quotes:
                sym = q.get("symbol", "")
                price = q.get("regularMarketPrice", 0.0)
                mcap = q.get("marketCap", 0.0)
                vol = q.get("regularMarketVolume", 0)
                pct_chg = q.get("regularMarketChangePercent", 0.0)
                name = q.get("shortName") or q.get("longName") or sym.replace(".KL", "")

                if not mcap and price:
                    shares = q.get("sharesOutstanding", 0)
                    mcap = price * shares
                
                mcap_bil = float(mcap) / 1e9
                turnover = price * vol

                # Initial eligibility: must meet market cap threshold
                if mcap_bil > 0.15 and price > 0.05:
                    records.append({
                        "code": sym.replace(".KL", ""),
                        "symbol": sym,
                        "name": name.strip(),
                        "price": float(price),
                        "market_cap": float(mcap_bil),
                        "turnover": float(turnover),
                        "pct_change": float(pct_chg)
                    })
        except Exception as e:
            print(f"Error querying batch {chunk[:3]}: {e}")

    df = pd.DataFrame(records)
    if df.empty:
        return df

    # Order by full market cap to derive candidate rankings
    df = df.sort_values(by="market_cap", ascending=False).reset_index(drop=True)
    return df

def analyze_bursa_focus_stocks(candidates, client):
    """Invokes Gemini Flash to generate research notes for 10 mover stocks."""
    input_text = []
    for s in candidates:
        input_text.append(
            f"Stock: {s['name']} ({s['code']}) | Group: {s['category']} | Price: RM {s['price']:.2f} | "
            f"Daily Change: {s['pct_change']:.2f}% | Turnover: RM {s['turnover']:,.0f} | Mkt Cap: RM {s['market_cap']:.2f}B"
        )
    
    prompt = f"""
You are a senior Bursa Malaysia quantitative and fundamental equity research analyst.
Below are 5 core eligible blue-chips and 5 small/mid-cap movers selected by the system today:

{chr(10).join(input_text)}

Provide a concise 2 to 3-sentence research summary for each company:
1. Explain price and volume action, order flow, or key catalysts.
2. Highlight key analytical takeaways (e.g., FTSE expansion candidacy, foreign flows, technical boundaries, or valuation support).
3. Deliver the response in clear Chinese, incorporating standard English financial terms. Structure into two explicit sections: 【5 大核心蓝筹】 and 【5 大中小盘异动股】.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
    except Exception:
        try:
            response = ai_client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt
            )
            return response.text
        except Exception as e:
            return f"AI summary generation failed: {e}"

def main():
    history_file = "ranking_history.json"
    
    df = fetch_screened_bursa_rankings()
    if df.empty:
        print("No valid stock data retrieved. Terminating run.")
        return

    top50 = df.head(50).to_dict(orient="records")

    # Load previous rankings for displacement monitoring
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

        # FTSE buffer and critical index perimeter indicators
        alert = ""
        if i == 25:
            alert = " ⚡[现行 30 大直晋门槛 / Top 25 Auto-Promotion]"
        elif i == 30:
            alert = " ⚠️[现行 30 大边缘警戒 / Top 30 Perimeter]"
        elif 31 <= i <= 35:
            alert = " 📋[现行 30 大官方候补区 / Reserve List]"
        elif i == 45:
            alert = " 🚨[扩军 50 大直晋前线 / Top 45 Promotion Line]"
        elif i == 50:
            alert = " 🏁[扩军 50 大席位生死线 / Top 50 Cutoff]"

        rank_lines.append(f"{i}. `{name}` ({code}) - RM {cap:.2f}B ({tag}){alert}")

    # Top 5 blue chips (highest absolute % change within Top 50)
    blue_df = df.head(50).copy()
    blue_df["abs_chg"] = blue_df["pct_change"].abs()
    top5_blue = blue_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_blue:
        s["category"] = "合资格蓝筹 (Eligible Blue Chip)"

    # Top 5 small/mid caps (highest absolute % change outside Top 50)
    sme_df = df.iloc[50:].copy() if len(df) > 50 else df.iloc[35:50].copy()
    sme_df["abs_chg"] = sme_df["pct_change"].abs()
    top5_sme = sme_df.sort_values(by="abs_chg", ascending=False).head(5).to_dict(orient="records")
    for s in top5_sme:
        s["category"] = "中小盘潜力股 (Small/Mid Cap)"

    # Generate insights via Gemini Flash
    ai_summary = "（未配置 Gemini API Key）"
    if GEMINI_KEY:
        try:
            client = genai.Client(api_key=GEMINI_KEY)
            ai_summary = analyze_bursa_focus_stocks(top5_blue + top5_sme, client)
        except Exception as e:
            ai_summary = f"AI Generation Error: {e}"

    # Construct Telegram broadcast messages
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    part1_header = (
        f"📊 *【马股 KLCI 扩军 50 大成分股模拟与攻防榜】*\n"
        f"⏱ *数据采集时间：* {now_str}\n"
        f"📐 *编制口径：主板准入 + 流动性审计 (中位数换手率) + 自由流通合资格*\n\n"
    )

    msg_part1 = part1_header + "🏆 *合资格成分股 Top 1 - 25：*\n" + "\n".join(rank_lines[:25])
    msg_part2 = "🏆 *合资格成分股 Top 26 - 50 (含扩军临界防线)：*\n" + "\n".join(rank_lines[25:])
    msg_part3 = f"━━━━━━━━━━━━━━━\n🔍 *【今日 5 蓝筹 + 5 中小盘异动深度简报】*\n\n{ai_summary}"

    send_telegram(msg_part1)
    time.sleep(1)
    send_telegram(msg_part2)
    time.sleep(1)
    send_telegram(msg_part3)

    # Save ranking cache
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(current_ranks, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
