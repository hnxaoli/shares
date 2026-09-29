"""
股票分析 Web 服务 — 澎湃 OS 3.0 手机版（完整版 v2.3）

完整复刻 stock_analyzer.py 功能：
  Tab1: 个股分析（K线/MA/MACD/RSI/KDJ/买卖信号/历史切换）
  Tab2: 板块概览（160+概念板块叠加对比）
  Tab3: 选股器（按历史涨幅筛选）
  自选股面板（批量添加+实时行情）

启动: python app_web.py
依赖: pip install flask requests
"""
import os, sys, json, threading, concurrent.futures, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock_logic import (
    fetch_hist, fetch_realtime_batch, fetch_realtime_name,
    compute_indicators, compute_kdj, fetch_stock_sector,
    watchlist_add, watchlist_remove, load_watchlist,
    resample_ohlc, get_sector_list_online,
    fetch_index_hist, fetch_index_name,
)

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", template_folder="templates")

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_config.json")
_config_lock = threading.Lock()
# 简单内存缓存，避免重复请求
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 120  # 秒


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"watchlist": [], "last_code": "600519", "history": ["600519", "000001", "300750"]}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def cached_get(key, fetch_fn):
    with _cache_lock:
        if key in _cache and time.time() - _cache[key]["t"] < CACHE_TTL:
            return _cache[key]["d"]
    d = fetch_fn()
    with _cache_lock:
        _cache[key] = {"t": time.time(), "d": d}
    return d


# ─── 静态页面 ───
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ─── API: 个股完整数据 ───
@app.route("/api/chart")
def api_chart():
    code = request.args.get("code", "600519").strip()
    period = request.args.get("period", "day")  # day / week / month
    try:
        n = int(request.args.get("n", 500))
    except ValueError:
        n = 500

    cache_key = f"chart:{code}:{period}:{n}"
    cached = cached_get(cache_key, lambda: _fetch_chart(code, period, n))
    return jsonify(cached)


def _fetch_chart(code, period, n):
    rows = fetch_hist(code, n=n)
    if not rows:
        return {"error": f"获取 {code} 数据失败"}

    if period == "week":
        rows = resample_ohlc(rows, "W")
    elif period == "month":
        rows = resample_ohlc(rows, "M")

    rows = compute_indicators(rows)
    rows = compute_kdj(rows)

    # 只返回最近 min(n, 500) 条 + 简化字段（省流量）
    rows = rows[-min(n, 500):]

    # 并发获取名称 + 实时行情
    name = fetch_realtime_name(code) or code
    rt = fetch_realtime_batch([code])
    rt_code = rt.get(code, {})

    return {
        "code": code,
        "name": name,
        "realtime": rt_code,
        "data": rows,
    }


# ─── API: 批量实时行情 ───
@app.route("/api/realtime")
def api_realtime():
    codes = [c.strip() for c in request.args.get("codes", "").split(",") if c.strip()]
    if not codes:
        return jsonify({})
    result = fetch_realtime_batch(codes) or {}
    return jsonify(result)


# ─── API: 板块列表 ───
@app.route("/api/sector-list")
def api_sector_list():
    cached = cached_get("sector_list", get_sector_list_online)
    return jsonify(cached)


# ─── API: 板块历史 ───
@app.route("/api/sector-chart")
def api_sector_chart():
    codes = request.args.get("codes", "").split(",")
    codes = [c.strip() for c in codes if c.strip()]
    if not codes:
        return jsonify({})

    # 并发获取多个板块
    def one(code):
        name = fetch_index_name(code) or code
        df = fetch_index_hist(code, n=500)
        if df is None:
            return (code, name, [])
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "date": str(r.name.date()),
                "close": round(float(r["close"]), 3),
            })
        return (code, name, rows)

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for code, name, rows in ex.map(one, codes[:20]):
            results[code] = {"name": name, "data": rows[-300:]}

    return jsonify(results)


# ─── API: 自选股 ───
@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    with _config_lock:
        cfg = load_config()
    return jsonify(cfg.get("watchlist", []))


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_post():
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "add")
    code = data.get("code", "").strip()
    name = data.get("name", "").strip()

    with _config_lock:
        cfg = load_config()
        if action == "add":
            if not name:
                name = fetch_realtime_name(code) or ""
            cfg = watchlist_add(cfg, code, name)
        elif action == "remove":
            cfg = watchlist_remove(cfg, code)
        elif action == "clear":
            cfg["watchlist"] = []
        elif action == "batch":
            for item in data.get("items", []):
                c = item.get("code", "").strip()
                n = item.get("name", "").strip()
                if c:
                    cfg = watchlist_add(cfg, c, n)
        save_config(cfg)
    return jsonify(cfg.get("watchlist", []))


# ─── API: 配置（含历史代码） ───
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    with _config_lock:
        cfg = load_config()
        if request.method == "POST":
            data = request.get_json(force=True, silent=True) or {}
            if "last_code" in data:
                cfg["last_code"] = str(data["last_code"]).strip()
            if "history" in data:
                h = [c.strip() for c in data["history"] if c.strip()]
                # 不超过 50
                cfg["history"] = h[:50]
            save_config(cfg)
        return jsonify(cfg)


# ─── API: 选股（涨幅排行） ───
@app.route("/api/screener")
def api_screener():
    """简易选股：fetch_stock_sector 查行业涨幅排行"""
    # 返回热门概念板块（东方财富 push2 接口的行业涨幅）
    codes = request.args.get("codes", "").split(",")
    codes = [c.strip() for c in codes if c.strip()]
    if not codes:
        codes = ["000001", "000002", "000651", "000725", "000858",
                 "002415", "002594", "300750", "600036", "600519",
                 "601318", "601899", "600276", "000333", "002475"]

    # 并发获取
    def rt(c):
        data = fetch_realtime_batch([c]) or {}
        name = fetch_realtime_name(c) or c
        if c in data:
            return {
                "code": c,
                "name": name,
                "price": data[c]["price"],
                "pct": data[c]["change_percent"],
            }
        return {"code": c, "name": name, "price": 0, "pct": 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(rt, codes))

    results.sort(key=lambda x: -x["pct"])
    return jsonify(results)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
