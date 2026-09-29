"""
股票分析 Web 服务 — 澎湃 OS 3.0 手机版后端
访问方式: 浏览器打开 http://localhost:5000
数据: 新浪财经 / 腾讯财经 公共 API

启动: python app_web.py
依赖: pip install flask requests  (仅此两个！零 numpy/pandas!)
"""
import os, sys, json, threading

# 确保能 import stock_logic
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock_logic import (
    fetch_hist, fetch_realtime_batch, fetch_realtime_name,
    compute_indicators, compute_kdj, fetch_stock_sector,
    watchlist_add, watchlist_remove, watchlist_has, load_watchlist,
    resample_ohlc,
)

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", template_folder="templates")

# 本地配置文件
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_config.json")
_config_lock = threading.Lock()


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"watchlist": [], "last_code": "600519"}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ─── 静态页面 ───
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ─── API: 历史行情 + 指标 ───
@app.route("/api/chart")
def api_chart():
    code = request.args.get("code", "600519").strip()
    period = request.args.get("period", "day")  # day / week / month
    try:
        n = int(request.args.get("n", 500))
    except ValueError:
        n = 500

    rows = fetch_hist(code, n=n)
    if not rows:
        return jsonify({"error": f"获取 {code} 数据失败"}), 404

    if period == "week":
        rows = resample_ohlc(rows, "W")
    elif period == "month":
        rows = resample_ohlc(rows, "M")

    rows = compute_indicators(rows)
    rows = compute_kdj(rows)

    # 取最近 min(n, 500) 条
    rows = rows[-min(n, 500):]

    return jsonify({
        "code": code,
        "name": fetch_realtime_name(code),
        "data": rows,
    })


# ─── API: 实时行情 ───
@app.route("/api/realtime")
def api_realtime():
    codes = request.args.get("codes", "600519,000001").split(",")
    codes = [c.strip() for c in codes if c.strip()]
    if not codes:
        return jsonify({"error": "codes 为空"}), 400

    result = fetch_realtime_batch(codes)
    return jsonify(result)


# ─── API: 板块行情 ───
@app.route("/api/sector")
def api_sector():
    codes = request.args.get("codes", "000001,600519,300750").split(",")
    codes = [c.strip() for c in codes if c.strip()]
    result = fetch_stock_sector(codes)
    return jsonify(result)


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

    if not code:
        return jsonify({"error": "缺少 code"}), 400

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
        save_config(cfg)

    return jsonify(cfg.get("watchlist", []))


# ─── API: 搜索 ───
@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    name = fetch_realtime_name(q)
    return jsonify({"code": q, "name": name})


# ─── API: 配置（last_code） ───
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    with _config_lock:
        cfg = load_config()
        if request.method == "POST":
            data = request.get_json(force=True, silent=True) or {}
            if "last_code" in data:
                cfg["last_code"] = str(data["last_code"]).strip()
            save_config(cfg)
        return jsonify(cfg)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # 监听所有网卡，手机 Termux 用 localhost 访问
    app.run(host="0.0.0.0", port=port, debug=False)
