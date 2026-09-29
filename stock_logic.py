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
