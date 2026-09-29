"""
股票业务逻辑层 — 纯 Python 实现，无需 numpy / pandas / scipy
兼容: Windows / Linux / Termux (Android arm64)

依赖: requests (仅此一个第三方库)
"""
import json, os, re, time, math, warnings
from typing import Optional

import requests

warnings.filterwarnings("ignore")


# ═══════════════════════════════════════════════════════════════
# 纯 Python 数值工具 (替代 numpy/pandas)
# ═══════════════════════════════════════════════════════════════

def _mean(xs):
    """Python 3.8+ 有 sum() float 升级，安全。"""
    if not xs: return 0.0
    return sum(float(x) for x in xs) / len(xs)

def _ema(xs, span, adjust=False):
    """pandas ewm(span=N, adjust=False).mean() 精确等价。"""
    if not xs: return []
    alpha = 2.0 / (span + 1)
    out = [0.0] * len(xs)
    out[0] = float(xs[0])
    for i in range(1, len(xs)):
        out[i] = alpha * float(xs[i]) + (1 - alpha) * out[i-1]
    return out

def _rolling_mean(xs, n):
    """pandas rolling(n).mean() 精确等价，前面 n-1 个为 None。"""
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        out[i] = _mean(xs[i - n + 1 : i + 1])
    return out

def _rolling_min(xs, n):
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        out[i] = min(xs[i - n + 1 : i + 1])
    return out

def _rolling_max(xs, n):
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        out[i] = max(xs[i - n + 1 : i + 1])
    return out


# ═══════════════════════════════════════════════════════════════
# HTTP 工具
# ═══════════════════════════════════════════════════════════════

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

def _get(url, timeout=12, params=None):
    return requests.get(url, headers=_HEADERS, timeout=timeout, verify=False, params=params)

def _sina_market(symbol: str) -> str:
    code = symbol.strip().lstrip("0")
    if re.match(r"^6", code):
        return "sh"
    return "sz"


# ═══════════════════════════════════════════════════════════════
# 历史行情获取 → 返回 list[dict] 而非 DataFrame
# ═══════════════════════════════════════════════════════════════

def fetch_hist_sina(symbol: str, n: int = 1500):
    mkt = _sina_market(symbol)
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={mkt}{symbol}&scale=240&ma=no&datalen={n}")
    try:
        r = _get(url)
        data = json.loads(r.text)
        if not data:
            return None
        rows = []
        for item in data:
            rows.append({
                "date": item["day"],
                "open": float(item["open"]), "high": float(item["high"]),
                "low": float(item["low"]), "close": float(item["close"]),
                "volume": float(item["volume"]),
            })
        rows.sort(key=lambda x: x["date"])
        return rows
    except Exception:
        return None

def fetch_hist_tencent(symbol: str, n: int = 1500):
    mkt = _sina_market(symbol)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"_var=kd&param={mkt}{symbol},day,,,{n},qfq&r=0.1")
    try:
        r = _get(url)
        text = r.text
        if "=" in text:
            text = text[text.index("=")+1:]
        d = json.loads(text)
        klines = d.get("data", {}).get(f"{mkt}{symbol}", {}).get("qfqday", [])
        if not klines:
            return None
        rows = []
        for item in klines:
            rows.append({
                "date": item[0],
                "open": float(item[1]), "close": float(item[2]),
                "high": float(item[3]), "low": float(item[4]),
                "volume": float(item[5]) if len(item) > 5 else 0,
            })
        rows.sort(key=lambda x: x["date"])
        return rows
    except Exception:
        return None

def fetch_hist(symbol: str, n: int = 1500):
    rows = fetch_hist_sina(symbol, n)
    if rows:
        return rows
    return fetch_hist_tencent(symbol, n)


# ═══════════════════════════════════════════════════════════════
# 实时行情
# ═══════════════════════════════════════════════════════════════

def fetch_realtime_name(symbol: str) -> str:
    mkt = _sina_market(symbol)
    url = f"https://hq.sinajs.cn/list={mkt}{symbol}"
    try:
        r = _get(url, timeout=5)
        r.encoding = "gbk"
        m = re.search(r'"([^,]+),', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return symbol

def fetch_realtime_batch(codes):
    """批量实时行情 → {code: (name, price, pct)}"""
    if not codes:
        return {}
    symbols = ",".join(f"{_sina_market(c)}{c}" for c in codes)
    url = f"https://hq.sinajs.cn/list={symbols}"
    try:
        r = _get(url, timeout=6)
        r.encoding = "gbk"
        out = {}
        for m in re.finditer(r'hq_str_(?:sh|sz)(\d{6})="([^"]*)"', r.text):
            code, payload = m.group(1), m.group(2)
            f = payload.split(",")
            if len(f) < 4 or not f[0]:
                continue
            try:
                price = float(f[3]); prev = float(f[2])
                if price <= 0 or prev <= 0: continue
                out[code] = {"name": f[0], "price": price,
                             "change_percent": round((price - prev) / prev * 100, 2)}
            except Exception:
                continue
        return out
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════
# 板块/行业 API
# ═══════════════════════════════════════════════════════════════

_SECTOR_CACHE: dict = {}

def fetch_stock_sector(codes):
    global _SECTOR_CACHE
    result = {}
    to_fetch = [c for c in codes if c not in _SECTOR_CACHE]
    if not to_fetch:
        return {c: _SECTOR_CACHE.get(c, "") for c in codes}

    def _secid(code):
        return ("1." if code.startswith(("6", "9")) else "0.") + code

    for i in range(0, len(to_fetch), 30):
        batch = to_fetch[i:i+30]
        secids = ",".join(_secid(c) for c in batch)
        url = (f"https://push2.eastmoney.com/api/qt/ulist.np/get"
               f"?fltt=2&fields=f12,f100&secids={secids}")
        for attempt in range(4):
            try:
                r = _get(url, timeout=8)
                for item in r.json().get("data", {}).get("diff", []) or []:
                    code = str(item.get("f12", ""))
                    sec = str(item.get("f100", "") or "")
                    _SECTOR_CACHE[code] = sec
                break
            except Exception:
                if attempt < 3:
                    time.sleep(0.4 + attempt * 0.6)
        for c in batch:
            if c not in _SECTOR_CACHE:
                _SECTOR_CACHE[c] = ""
        time.sleep(0.15)
    return {c: _SECTOR_CACHE.get(c, "") for c in codes}


# ═══════════════════════════════════════════════════════════════
# 技术指标 → 直接在 rows (list[dict]) 上加字段
# ═══════════════════════════════════════════════════════════════

def compute_indicators(rows):
    """rows: list[dict] (含 date/open/high/low/close/volume)
    直接在每个 dict 上加 ma5/ma20/ma60/dif/dea/macd/rsi/sentiment/buy/sell 字段
    """
    if not rows:
        return rows
    
    close = [r["close"] for r in rows]
    n = len(rows)
    
    # ── MA ──
    ma5  = _rolling_mean(close, 5)
    ma20 = _rolling_mean(close, 20)
    ma60 = _rolling_mean(close, 60)
    
    # ── MACD ──
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = [ema12[i] - ema26[i] for i in range(n)]
    dea = _ema(dif, 9)
    macd = [(dif[i] - dea[i]) * 2 for i in range(n)]
    
    # ── RSI(14) ──
    rsi = [None] * n
    for i in range(14, n):
        gains, losses = 0.0, 0.0
        for j in range(i - 13, i + 1):
            diff = close[j] - close[j-1]
            if diff > 0: gains += diff
            else: losses += -diff
        if losses == 0:
            rsi[i] = 100.0
        else:
            rs = gains / losses
            rsi[i] = 100 - 100 / (1 + rs)
    
    # ── Sentiment (简化版：RSI*0.6 + MACD归一化*0.4，再做一个平滑) ──
    sentiment = [None] * n
    for i in range(n):
        rsi_n = rsi[i] if rsi[i] is not None else 50
        macd_abs_max = max(abs(x) for x in macd) + 1e-9
        macd_n = (dif[i] / macd_abs_max + 1) * 50
        sentiment[i] = rsi_n * 0.6 + macd_n * 0.4
    
    # ── 金叉死叉 ──
    buy = [False] * n
    sell = [False] * n
    for i in range(1, n):
        if ma5[i] is not None and ma20[i] is not None and ma5[i-1] is not None and ma20[i-1] is not None:
            if ma5[i] > ma20[i] and ma5[i-1] <= ma20[i-1]: buy[i] = True
            if ma5[i] < ma20[i] and ma5[i-1] >= ma20[i-1]: sell[i] = True
    
    # ── 写回 ──
    for i, r in enumerate(rows):
        r["ma5"] = round(ma5[i], 4) if ma5[i] is not None else None
        r["ma20"] = round(ma20[i], 4) if ma20[i] is not None else None
        r["ma60"] = round(ma60[i], 4) if ma60[i] is not None else None
        r["dif"] = round(dif[i], 4)
        r["dea"] = round(dea[i], 4)
        r["macd"] = round(macd[i], 4)
        r["rsi"] = round(rsi[i], 4) if rsi[i] is not None else None
        r["sentiment"] = round(sentiment[i], 4)
        r["buy"] = buy[i]
        r["sell"] = sell[i]
    
    return rows


def compute_kdj(rows, n=9):
    """纯 Python KDJ"""
    if not rows:
        return rows
    close = [r["close"] for r in rows]
    low   = [r["low"]   for r in rows]
    high  = [r["high"]  for r in rows]
    
    low_n  = _rolling_min(low, n)
    high_n = _rolling_max(high, n)
    
    rsv = [50.0] * len(rows)
    for i in range(n - 1, len(rows)):
        h = high_n[i] - low_n[i]
        rsv[i] = (close[i] - low_n[i]) / h * 100.0 if h > 0 else 50.0
    
    k = _ema(rsv, 3)   # com=2 → alpha=1/(2+1)*2=2/3 ≈ span=3
    d = _ema(k, 3)
    j = [3 * k[i] - 2 * d[i] for i in range(len(rows))]
    
    for i, r in enumerate(rows):
        r["k"] = round(k[i], 4)
        r["d"] = round(d[i], 4)
        r["j"] = round(j[i], 4)
    
    return rows


# ═══════════════════════════════════════════════════════════════
# 日线 → 周线/月线
# ═══════════════════════════════════════════════════════════════

def resample_ohlc(rows, period="W"):
    """纯 Python 重采样。period: 'W' 周 / 'M' 月"""
    if not rows:
        return []
    
    def _bucket(date_str):
        # date_str = "2026-09-28"
        y, m, d = map(int, date_str.split("-"))
        if period == "W":
            # ISO 周: 简化用 (年, 周序号) — 周一为起点
            import datetime
            dt = datetime.date(y, m, d)
            iso = dt.isocalendar()  # (年, 周, 日)
            return (iso[0], iso[1])
        elif period == "M":
            return (y, m)
        return (y, m, d)
    
    groups = {}
    for r in rows:
        key = _bucket(r["date"])
        if key not in groups:
            groups[key] = []
        groups[key].append(r)
    
    result = []
    for key in sorted(groups.keys()):
        gs = groups[key]
        result.append({
            "date": gs[0]["date"],   # 该桶第一天
            "open": gs[0]["open"],
            "high": max(g["high"] for g in gs),
            "low":  min(g["low"]  for g in gs),
            "close": gs[-1]["close"],
            "volume": sum(g["volume"] for g in gs),
        })
    return result


# ═══════════════════════════════════════════════════════════════
# 自选股 CRUD
# ═══════════════════════════════════════════════════════════════

def load_watchlist(cfg):
    return cfg.get("watchlist", [])

def watchlist_add(cfg, code, name=""):
    code = code.strip()
    if not code:
        return cfg
    wl = cfg.get("watchlist", [])
    existing_codes = [w.split("|", 1)[0] for w in wl]
    if code in existing_codes:
        return cfg
    wl.insert(0, f"{code}|{name}")
    cfg["watchlist"] = wl[:100]
    return cfg

def watchlist_remove(cfg, codes_to_remove):
    targets = set(codes_to_remove) if isinstance(codes_to_remove, list) else {codes_to_remove}
    wl = [w for w in cfg.get("watchlist", []) if w.split("|", 1)[0] not in targets]
    cfg["watchlist"] = wl
    return cfg

def watchlist_has(cfg, code):
    return code in [w.split("|", 1)[0] for w in cfg.get("watchlist", [])]


# ═══════════════════════════════════════════════════════════════
# 指数/板块历史行情
# ═══════════════════════════════════════════════════════════════

def fetch_index_hist(sina_code: str, n: int = 500):
    """获取指数/板块历史行情。sina_code 如 sh000001 / sz399006"""
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={n}")
    try:
        r = _get(url)
        raw = r.text.strip()
        if raw in ("null", "", "[]"):
            return None
        data = json.loads(raw)
        if not data:
            return None
        rows = []
        for item in data:
            rows.append({
                "date": item["day"],
                "close": float(item["close"]),
            })
        rows.sort(key=lambda x: x["date"])
        return rows
    except Exception:
        return None

def fetch_index_name(sina_code: str) -> str:
    """用新浪实时行情获取指数名称"""
    url = f"https://hq.sinajs.cn/list={sina_code}"
    try:
        r = _get(url, timeout=5)
        r.encoding = "gbk"
        m = re.search(r'"([^,]+),', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return sina_code


# ═══════════════════════════════════════════════════════════════
# 板块列表 (160+)
# ═══════════════════════════════════════════════════════════════

SECTOR_LIST = [
    # 大盘指数
    ("sh000001", "上证指数"), ("sz399001", "深证成指"),
    ("sh000300", "沪深300"), ("sz399006", "创业板指"),
    ("sh000016", "上证50"), ("sh000905", "中证500"),
    ("sh000852", "中证1000"), ("sh000906", "中证800"),
    ("sh000688", "科创50"), ("sh000903", "中证A100"),
    ("sh000902", "中证流通"), ("sh000985", "中证全指"),
    ("sz399005", "中小100"), ("sz399850", "深证50"),
    # 医药医疗
    ("sz399989", "中证医疗"), ("sh000991", "全指医药"),
    ("sh000913", "300医药"), ("sh000933", "中证医药"),
    ("sh000808", "医药生物"), ("sh000814", "细分医药"),
    ("sh000841", "800医药"), ("sh000857", "500医药"),
    ("sh000863", "CS精准医"), ("sh000978", "医药100"),
    ("sz399993", "CSWD生科"), ("sz399812", "养老产业"),
    # 科技/AI/半导体
    ("sz399811", "CSSW电子"), ("sh000935", "中证信息"),
    ("sh000993", "全指信息"), ("sh000858", "500信息"),
    ("sh000915", "300信息"), ("sh000916", "300电信"),
    ("sh000936", "中证电信"), ("sh000994", "全指电信"),
    ("sz399970", "移动互联"), ("sz399994", "信息安全"),
    ("sz399971", "中证传媒"), ("sz399810", "CSSW传媒"),
    ("sz399803", "工业4.0"), ("sz399996", "智能家居"),
    ("sh000964", "中证新兴"), ("sh000998", "中证TMT"),
    # 金融
    ("sz399986", "中证银行"), ("sz399975", "证券公司"),
    ("sh000934", "中证金融"), ("sh000914", "300金融"),
    ("sh000992", "全指金融"), ("sz399805", "互联金融"),
    ("sz399966", "800非银"), ("sh000974", "800金融"),
    ("sh000849", "300非银"), ("sz399809", "保险主题"),
    ("sh000951", "300银行"), ("sh000947", "内地银行"),
    ("sh000946", "内地金融"),
    # 消费
    ("sh000932", "中证消费"), ("sh000990", "全指消费"),
    ("sh000912", "300消费"), ("sh000942", "内地消费"),
    ("sz399987", "中证酒"), ("sz399997", "中证白酒"),
    ("sh000989", "全指可选"), ("sh000931", "中证可选"),
    ("sh000806", "消费服务"), ("sh000807", "食品饮料"),
    ("sh000815", "细分食品"), ("sh000997", "大消费"),
    ("sh000911", "300可选"),
    # 新能源/环保
    ("sz399976", "CS新能车"), ("sz399808", "中证新能"),
    ("sh000941", "新能源"), ("sh000977", "内地低碳"),
    ("sh000827", "中证环保"), ("sz399806", "环境治理"),
    ("sz399817", "生态100"), ("sz399814", "大农业"),
    ("sh000801", "资源80"), ("sh000805", "A股资源"),
    ("sh000949", "内地农业"), ("sh000809", "细分农业"),
    # 军工/国防
    ("sz399967", "中证军工"), ("sz399959", "军工指数"),
    ("sz399973", "中证国防"), ("sz399813", "中证国安"),
    # 周期/资源
    ("sh000819", "有色金属"), ("sh000823", "800有色"),
    ("sh000811", "细分有色"), ("sh000850", "300有色"),
    ("sh000987", "全指材料"), ("sh000929", "中证材料"),
    ("sz399990", "煤炭等权"), ("sz399998", "中证煤炭"),
    ("sh000820", "煤炭指数"), ("sh000928", "中证能源"),
    ("sh000810", "细分能源"), ("sh000944", "内地资源"),
    ("sh000986", "全指能源"), ("sh000854", "500原料"),
    ("sh000856", "500工业"), ("sh000930", "中证工业"),
    ("sh000813", "细分化工"), ("sh000909", "300材料"),
    ("sh000979", "大宗商品"), ("sh000960", "中证龙头"),
    ("sh000961", "中证上游"), ("sh000962", "中证中游"),
    ("sh000963", "中证下游"),
    # 基建/地产
    ("sz399995", "基建工程"), ("sz399807", "高铁产业"),
    ("sz399965", "800地产"), ("sz399983", "地产等权"),
    ("sz399991", "一带一路"), ("sh000943", "内地基建"),
    ("sh000816", "细分地产"), ("sh000952", "300地产"),
    ("sh000948", "内地地产"),
    # 红利/价值
    ("sh000922", "中证红利"), ("sh000821", "300红利"),
    ("sh000822", "500红利"), ("sh000824", "国企红利"),
    ("sh000825", "央企红利"), ("sh000826", "民企红利"),
    # 主题/特色
    ("sz399974", "国企改革"), ("sz399992", "CSWD并购"),
    ("sz399804", "中证体育"), ("sz399972", "300深市"),
    ("sh000853", "CSSW丝路"), ("sh000865", "上海国企"),
    ("sh000851", "百发100"), ("sz399750", "深主板50"),
    ("sh000855", "央视500"), ("sh000891", "新兴综指"),
    ("sh000888", "上证收益"), ("sh000926", "中证央企"),
    ("sh000955", "中证国企"), ("sh000847", "腾讯济安"),
    ("sh000846", "ESG100"), ("sh000812", "细分机械"),
    ("sh000908", "300能源"), ("sh000910", "300工业"),
    ("sh000917", "300公用"), ("sh000937", "中证公用"),
    ("sh000995", "全指公用"), ("sh000945", "内地运输"),
    ("sh000988", "全指工业"), ("sh000919", "300价值"),
    ("sh000918", "300成长"),
    # 热门概念ETF
    ("sh515790", "光伏ETF"), ("sz159857", "光伏ETF天弘"),
    ("sh561160", "锂电池ETF"), ("sz159840", "锂电池50"),
    ("sz159995", "芯片ETF"), ("sh512760", "芯片ETF国泰"),
    ("sh512480", "半导体ETF"),
    ("sz159819", "AI智能ETF"), ("sh515070", "人工智能ETF"),
    ("sh562500", "机器人ETF"), ("sz159770", "机器人AI"),
    ("sz159861", "储能ETF"),
    ("sz159871", "有色金属ETF"), ("sh512400", "有色ETF"),
    ("sh512660", "军工ETF"), ("sh512010", "医药ETF"),
    ("sh560080", "中药ETF"), ("sz159647", "中药ETF"),
    ("sz159992", "创新药ETF"), ("sh515120", "创新药ETF"),
    ("sh560800", "数字经济ETF"), ("sz159713", "稀土ETF"),
    ("sh515030", "新能源车ETF"), ("sz159806", "新能车ETF"),
    ("sz159928", "消费ETF"), ("sh512690", "酒ETF"),
    ("sz159805", "传媒ETF"),
    ("sh515050", "5GETF"), ("sh515880", "通信ETF"),
    ("sh516510", "云计算ETF"), ("sh515400", "大数据ETF"),
    ("sh512000", "券商ETF"), ("sh512800", "银行ETF"),
    ("sh512200", "房地产ETF"),
    ("sh515220", "煤炭ETF"), ("sh515210", "钢铁ETF"),
    ("sz159825", "农业ETF"), ("sh561560", "电力ETF"),
    ("sz159869", "游戏ETF"), ("sh562510", "旅游ETF"),
    ("sz159790", "碳中和ETF"),
    ("sh588000", "科创50ETF"), ("sz159915", "创业板ETF"),
    ("sh513130", "恒生科技ETF"), ("sh513600", "恒生ETF"),
    ("sh513100", "纳指ETF"),
]

def get_sector_list_online():
    return SECTOR_LIST
