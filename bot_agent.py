import time

def run_multi_agent_committee(data, original_query):
    """
    具备自动重试与多模型故障转移（Failover）的稳健调用机制
    """
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

请严格按照以下 4 个板块输出深度研报（中文为主，核心金融专业术语附带英文）：

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

    # 免费模型候选池（按优先级排列）
    # 彻底弃用 gemini-3.8-flash，锁定 1,500 次/天的稳定免费模型池
    candidate_models = ["gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"]
    last_err = None

    for model_name in candidate_models:
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                return response.text
        except Exception as e:
            last_err = e
            continue

    raise last_err
