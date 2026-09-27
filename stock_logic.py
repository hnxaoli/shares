"""
股票业务逻辑层 — 与 UI 框架无关的纯 Python 模块
可被 Tkinter / Kivy / Flutter / Web 等任意 UI 层调用
"""
import json, os, re, time, math, warnings
from typing import Optional

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ─── 纯 numpy 替代 scipy.signal.savgol_filter ───
def _savgol_filter(x, window_length, polyorder, deriv=0):
    """精确等价 scipy.signal.savgol_filter。纯 numpy，无需 scipy 96MB。"""
    x = np.asarray(x, dtype=np.float64)
    half = window_length // 2
    n = len(x)
    if n < window_length:
        return x if deriv == 0 else np.zeros_like(x)
    pos = np.arange(-half, half + 1, dtype=float)
    A = np.vander(pos, polyorder + 1, increasing=True)
    coeffs = np.linalg.pinv(A)[deriv]
    pad = np.zeros(half)
    xp = np.concatenate([pad, x, pad])
    y = np.convolve(xp, coeffs[::-1], mode="valid")[:n]
    if half > 0:
        win_left = x[:window_length]
        poly_left = np.polyfit(np.arange(window_length), win_left, polyorder)
        y[:half] = np.polyval(poly_left, np.arange(half))
        win_right = x[n - window_length:]
        poly_right = np.polyfit(np.arange(window_length), win_right, polyorder)
        y[n - half : n] = np.polyval(poly_right, np.arange(window_length - half, window_length))
    return y


# ─── HTTP 工具 ───
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

def _get(url, timeout=12, params=None):
    return requests.get(url, headers=_HEADERS, timeout=timeout, verify=False, params=params)

# ─── 市场代码转换 ───
def _sina_market(symbol: str) -> str:
    code = symbol.strip().lstrip("0")
    if re.match(r"^6", code):
        return "sh"
    return "sz"

# ─── 历史行情获取 ───
def fetch_hist_sina(symbol: str, n: int = 1500) -> Optional[pd.DataFrame]:
    mkt = _sina_market(symbol)
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={mkt}{symbol}&scale=240&ma=no&datalen={n}")
    try:
        r = _get(url)
        data = json.loads(r.text)
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["day"])
        df.set_index("date", inplace=True)
        for c in ["open", "high", "low", "close"]:
            df[c] = df[c].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df.sort_index()
    except Exception:
        return None

def fetch_hist_tencent(symbol: str, n: int = 1500) -> Optional[pd.DataFrame]:
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
                "date": pd.to_datetime(item[0]),
                "open": float(item[1]), "close": float(item[2]),
                "high": float(item[3]), "low": float(item[4]),
                "volume": float(item[5]) if len(item) > 5 else 0,
            })
        return pd.DataFrame(rows).set_index("date").sort_index()
    except Exception:
        return None

def fetch_hist(symbol: str, n: int = 1500) -> Optional[pd.DataFrame]:
    df = fetch_hist_sina(symbol, n)
    if df is not None and len(df) > 0:
        return df
    return fetch_hist_tencent(symbol, n)

# ─── 实时行情 ───
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
                out[code] = (f[0], price, (price - prev) / prev * 100.0)
            except Exception:
                continue
        return out
    except Exception:
        return None

# ─── 板块/行业 API ───
_SECTOR_CACHE: dict = {}

def fetch_stock_sector(codes):
    """批量查行业板块 → {code: 行业名}"""
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

# ─── 技术指标 ───
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["close"]

    df["ma5"]  = c.rolling(5).mean()
    df["ma20"] = c.rolling(20).mean()
    df["ma60"] = c.rolling(60).mean()

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["macd"] = (df["dif"] - df["dea"]) * 2

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    rsi_n  = df["rsi"].fillna(50)
    macd_n = (df["dif"].fillna(0) / (df["dif"].abs().max() + 1e-9) + 1) * 50
    raw = rsi_n * 0.6 + macd_n * 0.4
    if len(df) > 30:
        wl = min(51, (len(df) // 2) * 2 - 1)
        try:
            df["sentiment"] = _savgol_filter(raw.fillna(50), wl, 3)
        except Exception:
            df["sentiment"] = raw.rolling(20, min_periods=1).mean()
    else:
        df["sentiment"] = raw

    ma5p  = df["ma5"].shift(1)
    ma20p = df["ma20"].shift(1)
    df["buy"]  = (df["ma5"] > df["ma20"]) & (ma5p <= ma20p)
    df["sell"] = (df["ma5"] < df["ma20"]) & (ma5p >= ma20p)
    return df

# ─── 自选股 CRUD ───
def load_watchlist(cfg):
    """从 cfg dict 提取自选股列表"""
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

# ─── KDJ 计算 ───
def compute_kdj(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    df = df.copy()
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.fillna(50).ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    df["k"], df["d"], df["j"] = k, d, j
    return df

# ─── 日线转周线/月线 ───
def resample_ohlc(df: pd.DataFrame, period: str = "W") -> pd.DataFrame:
    """period: 'W'=周, 'M'=月"""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(period).agg(agg).dropna(subset=["close"])
