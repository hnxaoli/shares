"""
股票量价分析 — Android APK 适配版
══════════════════════════════════════
运行:   python app_apk.py           # Windows 桌面开发
打包:   buildozer android debug     # WSL2/Linux 生成 APK

关键设计（修复 Android 常见致命 bug）:
  ✓ KV 绑定全部加 () → root.do_search() 而非 root.do_search
  ✓ 配置持久化用 App.user_data_dir → Android 原生兼容
  ✓ CJK 字体 NotoSansSC 打包 → LabelBase 注册后全局 font_name
  ✓ 所有 Kivy 回调带 instance=None → 避免 TypeError 崩溃
  ✓ 网络请求放后台线程 → Clock.schedule_once 回主线程更新 UI
"""
import os, sys, json, math, re, threading, traceback

# ── 业务逻辑层（纯 Python，与 UI 无关） ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_logic as sl

# ── Kivy 配置（必须在 import kivy.app 之前） ──
os.environ.setdefault("KIVY_NO_ARGS", "1")

from kivy.app import App
from kivy.lang import Builder
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.graphics import Color, Line, Rectangle, Ellipse
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.text import LabelBase

# ═══════════════════════════════════════════════════════════════════
# 主题常量
# ═══════════════════════════════════════════════════════════════════
BG       = (0.10, 0.10, 0.18, 1)
BG2      = (0.06, 0.06, 0.14, 1)
PRIMARY  = (0.05, 0.27, 0.38, 1)
ACCENT   = (0.91, 0.27, 0.38, 1)
GREEN    = (0.00, 0.72, 0.44, 1)   # 涨/买信号
RED      = (0.91, 0.27, 0.38, 1)   # 跌/卖信号
TEXT_W   = (1.00, 1.00, 1.00, 1)
TEXT_D   = (0.78, 0.78, 0.88, 1)

# ═══════════════════════════════════════════════════════════════════
# 字体注册（Android 上 SimSun/FangSong 不存在，必须打包 CJK 字体）
# ═══════════════════════════════════════════════════════════════════
_FONT_NAME = "NotoSansSC"
def _register_font():
    """打包的 NotoSansSC.ttf 放 fonts/ 目录，静态权重 TTF 才能被 SDL2 正确渲染"""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "NotoSansSC.ttf"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "NotoSansSC-Regular.ttf"),
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                LabelBase.register(name=_FONT_NAME, fn_regular=p)
                return True
            except Exception:
                return False
    # 无打包字体 → 让 Kivy 用默认系统字体
    return False

_HAS_CJK_FONT = _register_font()

# Label 默认字体（KV 里用 font_name 引用）
def _lbl_font():
    return _FONT_NAME if _HAS_CJK_FONT else None


# ═══════════════════════════════════════════════════════════════════
# 配置持久化（Android 安全版本：App.user_data_dir）
# ═══════════════════════════════════════════════════════════════════
_DEFAULT_WATCHLIST = ["600519|贵州茅台", "000858|五粮液", "300750|宁德时代",
                      "000001|平安银行", "601318|中国平安"]

def _cfg_path():
    app = App.get_running_app()
    d = app.user_data_dir if app else os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "stock_cfg.json")

def _load_cfg():
    p = _cfg_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"watchlist": list(_DEFAULT_WATCHLIST), "last_code": "600519"}

def _save_cfg(cfg):
    p = _cfg_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# 绘图 Widget：迷你 K 线 + MA 线 + 买卖信号
# ═══════════════════════════════════════════════════════════════════
class StockChart(Widget):
    """纯 Kivy Canvas 自绘，无需 matplotlib（省 ~96MB APK）"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._df = None
        self.bind(pos=self._draw, size=self._draw)

    def set_data(self, df):
        self._df = df
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        if self._df is None or len(self._df) < 2:
            return
        w, h = self.width, self.height
        pad = dp(14)
        recent = self._df.tail(120).reset_index(drop=True)
        prices = recent["close"].tolist()
        if not prices:
            return
        lo = min(prices) * 0.995
        hi = max(prices) * 1.005
        rng = hi - lo or 1.0
        n = len(prices)

        def px(i, v):
            x = pad + (w - 2 * pad) * i / max(n - 1, 1)
            y = pad + (h - 2 * pad) * (1 - (v - lo) / rng)
            return x, y

        with self.canvas:
            # 网格
            Color(rgba=(0.22, 0.22, 0.30, 1))
            for k in range(5):
                yp = pad + (h - 2 * pad) * k / 4
                Line(points=[pad, yp, w - pad, yp], width=0.5)

            # 价格线（亮蓝）
            Color(rgba=(0.50, 0.85, 1.00, 1))
            pts = []
            for i, v in enumerate(prices):
                pts.extend(px(i, v))
            if len(pts) >= 4:
                Line(points=pts, width=1.5)

            # MA5（橙）
            if "ma5" in recent.columns:
                Color(rgba=(1.00, 0.70, 0.30, 1))
                p5 = []
                for i, v in enumerate(recent["ma5"].tolist()):
                    if v and not math.isnan(v):
                        p5.extend(px(i, v))
                if len(p5) >= 4:
                    Line(points=p5, width=1)

            # MA20（红）
            if "ma20" in recent.columns:
                Color(rgba=(0.95, 0.30, 0.30, 1))
                p20 = []
                for i, v in enumerate(recent["ma20"].tolist()):
                    if v and not math.isnan(v):
                        p20.extend(px(i, v))
                if len(p20) >= 4:
                    Line(points=p20, width=1)

            # 买卖信号点（绿=买/红=卖）
            for col, color, is_buy in [("buy", GREEN, True), ("sell", RED, False)]:
                if col not in recent.columns:
                    continue
                Color(rgba=color)
                for idx, row in recent.iterrows():
                    if row.get(col, False):
                        x, y = px(idx, prices[idx])
                        Ellipse(pos=(x - dp(5), y - dp(5)), size=(dp(10), dp(10)))


class MacdChart(Widget):
    """MACD 红绿柱状图"""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._vals = []
        self.bind(pos=self._draw, size=self._draw)

    def set_data(self, df):
        if df is None or "macd" not in df.columns:
            self._vals = []
        else:
            self._vals = df.tail(120)["macd"].tolist()
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        if not self._vals:
            return
        w, h = self.width, self.height
        pad = dp(6)
        vals = [v for v in self._vals if v is not None and not math.isnan(v)]
        if not vals:
            return
        rng = max(abs(max(vals)), abs(min(vals))) * 1.1 or 1.0
        n = len(self._vals)
        bw = max(1, (w - 2 * pad) / n * 0.7)

        with self.canvas:
            Color(rgba=(0.40, 0.40, 0.50, 0.8))
            zy = h / 2
            Line(points=[pad, zy, w - pad, zy], width=0.8)

            for i, v in enumerate(self._vals):
                if v is None or math.isnan(v):
                    continue
                x = pad + (w - 2 * pad) * i / n
                bh = abs(v) / rng * (h / 2 - pad)
                if v >= 0:
                    Color(rgba=RED)
                    Rectangle(pos=(x - bw / 2, zy), size=(bw, bh))
                else:
                    Color(rgba=GREEN)
                    Rectangle(pos=(x - bw / 2, zy - bh), size=(bw, bh))


# ═══════════════════════════════════════════════════════════════════
# KV 语言（所有 root.method 都带 () — Android 不报错）
# ═══════════════════════════════════════════════════════════════════
_MAIN_KV = f"""
<MainScreen>:
    BoxLayout:
        orientation: 'vertical'
        canvas.before:
            Color: rgba(0.10, 0.10, 0.18, 1)
            Rectangle: pos: self.pos, size: self.size

        # 搜索栏
        BoxLayout:
            size_hint_y: None
            height: dp(52)
            padding: dp(8)
            spacing: dp(6)
            canvas.before:
                Color: rgba(0.06, 0.06, 0.14, 1)
                Rectangle: pos: self.pos, size: self.size
            TextInput:
                id: code_input
                hint_text: '输入股票代码 如 600519'
                size_hint_x: 0.6
                multiline: False
                foreground_color: 1,1,1,1
                background_color: 0.2,0.2,0.3,1
                padding: dp(10)
                font_size: dp(16)
                font_name: '{_FONT_NAME}'
                on_text_validate: root.do_search()
            Button:
                text: '查询'
                size_hint_x: 0.2
                background_normal: ''
                background_color: 0.91, 0.27, 0.38, 1
                color: 1,1,1,1
                font_size: dp(14)
                font_name: '{_FONT_NAME}'
                on_press: root.do_search()
            Button:
                text: '自选'
                size_hint_x: 0.2
                background_normal: ''
                background_color: 0.05, 0.27, 0.38, 1
                color: 1,1,1,1
                font_size: dp(14)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_watchlist()

        # 内容区
        ScrollView:
            id: content_scroll
            size_hint_y: 1
            BoxLayout:
                id: content_box
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                padding: dp(10)
                spacing: dp(8)

        # 底部导航
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            canvas.before:
                Color: rgba(0.06, 0.06, 0.14, 1)
                Rectangle: pos: self.pos, size: self.size
            Button:
                text: '📈 行情'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.91, 0.27, 0.38, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_main()
            Button:
                text: '⭐ 自选'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.78, 0.78, 0.88, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_watchlist()
            Button:
                text: '📊 板块'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.78, 0.78, 0.88, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_sector()
"""

_WL_KV = f"""
<WatchlistScreen>:
    BoxLayout:
        orientation: 'vertical'
        canvas.before:
            Color: rgba(0.10, 0.10, 0.18, 1)
            Rectangle: pos: self.pos, size: self.size

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(10)
            canvas.before:
                Color: rgba(0.06, 0.06, 0.14, 1)
                Rectangle: pos: self.pos, size: self.size
            Label:
                text: '⭐ 自选股'
                font_size: dp(20)
                bold: True
                color: 1,1,1,1
                size_hint_x: 0.55
                font_name: '{_FONT_NAME}'
            Button:
                text: '批量添加'
                size_hint_x: 0.225
                background_normal: ''
                background_color: 0.49, 0.34, 0.76, 1
                color: 1,1,1,1
                font_size: dp(13)
                font_name: '{_FONT_NAME}'
                on_press: root.do_batch_add()
            Button:
                text: '刷新'
                size_hint_x: 0.225
                background_normal: ''
                background_color: 0.05, 0.48, 0.70, 1
                color: 1,1,1,1
                font_size: dp(13)
                font_name: '{_FONT_NAME}'
                on_press: root.do_refresh()

        ScrollView:
            size_hint_y: 1
            BoxLayout:
                id: wl_box
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                padding: dp(4)
                spacing: dp(1)

        BoxLayout:
            size_hint_y: None
            height: dp(56)
            canvas.before:
                Color: rgba(0.06, 0.06, 0.14, 1)
                Rectangle: pos: self.pos, size: self.size
            Button:
                text: '📈 行情'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.78, 0.78, 0.88, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_main()
            Button:
                text: '⭐ 自选'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.91, 0.27, 0.38, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.do_refresh()
            Button:
                text: '📊 板块'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.78, 0.78, 0.88, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_sector()
"""

_SECTOR_KV = f"""
<SectorScreen>:
    BoxLayout:
        orientation: 'vertical'
        canvas.before:
            Color: rgba(0.10, 0.10, 0.18, 1)
            Rectangle: pos: self.pos, size: self.size

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(10)
            spacing: dp(6)
            canvas.before:
                Color: rgba(0.06, 0.06, 0.14, 1)
                Rectangle: pos: self.pos, size: self.size
            TextInput:
                id: sector_search
                hint_text: '搜索板块...'
                multiline: False
                foreground_color: 1,1,1,1
                background_color: 0.2,0.2,0.3,1
                padding: dp(10)
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_text_validate: root.do_search()
            Button:
                text: '🔍'
                size_hint_x: 0.2
                background_normal: ''
                background_color: 0.05, 0.48, 0.70, 1
                color: 1,1,1,1
                font_size: dp(18)
                on_press: root.do_search()

        ScrollView:
            size_hint_y: 1
            BoxLayout:
                id: sector_box
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                padding: dp(4)
                spacing: dp(1)

        BoxLayout:
            size_hint_y: None
            height: dp(56)
            canvas.before:
                Color: rgba(0.06, 0.06, 0.14, 1)
                Rectangle: pos: self.pos, size: self.size
            Button:
                text: '📈 行情'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.78, 0.78, 0.88, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_main()
            Button:
                text: '⭐ 自选'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.78, 0.78, 0.88, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.app.go_watchlist()
            Button:
                text: '📊 板块'
                background_normal: ''
                background_color: 0.09, 0.09, 0.18, 1
                color: 0.91, 0.27, 0.38, 1
                font_size: dp(15)
                font_name: '{_FONT_NAME}'
                on_press: root.do_search()
"""


# ═══════════════════════════════════════════════════════════════════
# 主页面（个股行情 + 图表 + 指标）
# ═══════════════════════════════════════════════════════════════════
class MainScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._cfg = None
        self._last_df = None
        self._code = ""
        self._name = ""
        self._rt = None
        self._sector = ""

    def on_enter(self, *a):
        self._cfg = _load_cfg()
        last = self._cfg.get("last_code", "600519")
        self.ids.code_input.text = last
        self.do_search()

    # ── KV 绑定的目标（必须 instance=None） ──
    def do_search(self, instance=None):
        code = self.ids.code_input.text.strip()
        if not code:
            return
        self._code = code
        self._cfg["last_code"] = code
        _save_cfg(self._cfg)
        # 清内容 → loading
        box = self.ids.content_box
        box.clear_widgets()
        box.add_widget(self._mk_label(f"⏳ 加载 {code}...", TEXT_D, dp(16), dp(40)))
        threading.Thread(target=self._bg_fetch, args=(code,), daemon=True).start()

    def _bg_fetch(self, code):
        try:
            name = sl.fetch_realtime_name(code)
            hist = sl.fetch_hist(code, 500)
            if hist is None or len(hist) < 30:
                Clock.schedule_once(lambda dt: self._err(f"无法获取 {code} 历史数据"))
                return
            ind = sl.compute_indicators(hist)
            rt = sl.fetch_realtime_batch([code])
            sec = sl.fetch_stock_sector([code])
            self._last_df = ind
            self._name = name
            self._rt = rt.get(code, (name, 0, 0)) if rt else (name, 0, 0)
            self._sector = sec.get(code, "")
            Clock.schedule_once(lambda dt: self._build())
        except Exception as e:
            traceback.print_exc()
            Clock.schedule_once(lambda dt: self._err(str(e)))

    def _err(self, msg):
        box = self.ids.content_box
        box.clear_widgets()
        box.add_widget(self._mk_label(f"❌ {msg}", (1, 0.4, 0.4, 1), dp(14), dp(40)))

    def _mk_label(self, text, color=None, size=None, h=None, bold=False):
        """统一创建带 CJK 字体的 Label"""
        kw = dict(text=text, font_name=_FONT_NAME, color=color or TEXT_W)
        if size: kw["font_size"] = size
        if h: kw["size_hint_y"] = None; kw["height"] = h
        if bold: kw["bold"] = True
        return Label(**kw)

    def _build(self):
        box = self.ids.content_box
        box.clear_widgets()
        df = self._last_df
        code, name = self._code, self._name or self._code
        rt_name, price, pct = self._rt
        sec = self._sector
        last = df.iloc[-1]

        def fmt(v, d=2):
            return f"{v:.{d}f}" if v and not math.isnan(v) else "-"

        pct_color = GREEN if pct >= 0 else RED

        # ── 标题卡 ──
        header = GridLayout(cols=2, size_hint_y=None, height=dp(96), padding=dp(8))
        tb = BoxLayout(orientation="vertical")
        tb.add_widget(self._mk_label(f"[b]{name}[/b]", size=dp(20), h=dp(28), bold=True))
        tb.add_widget(self._mk_label(f"{code}  {sec}", color=TEXT_D, size=dp(12), h=dp(20)))
        header.add_widget(tb)
        pb = BoxLayout(orientation="vertical")
        pb.add_widget(self._mk_label(f"¥{price:.2f}", size=dp(28), h=dp(36), bold=True))
        pb.add_widget(self._mk_label(f"{pct:+.2f}%", color=pct_color, size=dp(16), h=dp(22)))
        header.add_widget(pb)
        box.add_widget(header)

        # ── 走势图 ──
        box.add_widget(self._mk_label("📈 近120日走势 (亮蓝=K线, 橙=MA5, 红=MA20, 圆点=买卖信号)",
                                       color=TEXT_D, size=dp(12), h=dp(22)))
        sc = StockChart(size_hint_y=None, height=dp(200))
        sc.set_data(df)
        box.add_widget(sc)

        # ── MACD ──
        box.add_widget(self._mk_label("MACD (红正/绿负)", color=TEXT_D, size=dp(12), h=dp(20)))
        mc = MacdChart(size_hint_y=None, height=dp(60))
        mc.set_data(df)
        box.add_widget(mc)

        # ── 指标网格 ──
        box.add_widget(self._mk_label("📊 技术指标", size=dp(15), h=dp(26), bold=True))
        grid = GridLayout(cols=2, size_hint_y=None, height=dp(180), padding=dp(4), spacing=dp(4))
        items = [
            ("MA5", fmt(last.ma5)), ("MA20", fmt(last.ma20)), ("MA60", fmt(last.ma60)),
            ("DIF", fmt(last.dif, 4)), ("DEA", fmt(last.dea, 4)), ("MACD", fmt(last.macd, 4)),
            ("RSI(14)", fmt(last.rsi, 1)), ("情绪值", fmt(last.sentiment, 1)),
        ]
        for k, v in items:
            row = BoxLayout(size_hint_y=None, height=dp(20), padding=dp(6))
            row.add_widget(self._mk_label(k, color=TEXT_D, size=dp(13)))
            row.add_widget(Widget())
            row.add_widget(self._mk_label(str(v), size=dp(14), bold=True))
            grid.add_widget(row)
        box.add_widget(grid)

        # ── 加自选按钮 ──
        in_wl = sl.watchlist_has(self._cfg, code)
        btn = Button(
            text="⭐ 已在自选" if in_wl else "＋ 加入自选",
            size_hint_y=None, height=dp(48),
            background_normal='',
            background_color=(0.25, 0.55, 0.35, 1) if in_wl else ACCENT,
            color=TEXT_W, font_size=dp(16), font_name=_FONT_NAME,
        )
        btn.bind(on_press=self._toggle_watch)
        box.add_widget(btn)

    def _toggle_watch(self, instance=None):
        code = self._code
        if sl.watchlist_has(self._cfg, code):
            self._cfg = sl.watchlist_remove(self._cfg, code)
        else:
            self._cfg = sl.watchlist_add(self._cfg, code, self._name)
        _save_cfg(self._cfg)
        self._build()


# ═══════════════════════════════════════════════════════════════════
# 自选股页面
# ═══════════════════════════════════════════════════════════════════
class WatchlistScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._cfg = None

    def on_enter(self, *a):
        self._cfg = _load_cfg()
        self.do_refresh()

    def do_refresh(self, instance=None):
        """重新拉实时行情刷新列表"""
        self._cfg = _load_cfg()
        box = self.ids.wl_box
        box.clear_widgets()
        wl = self._cfg.get("watchlist", [])
        if not wl:
            box.add_widget(self._lbl("还没有自选股，去主页面搜索添加吧！", TEXT_D, dp(14)))
            return
        # loading
        box.add_widget(self._lbl("⏳ 拉取实时行情...", TEXT_D, dp(14)))

        codes = [w.split("|", 1)[0] for w in wl]
        threading.Thread(target=self._bg_refresh, args=(codes,), daemon=True).start()

    def _bg_refresh(self, codes):
        try:
            rt = sl.fetch_realtime_batch(codes) or {}
            sec = sl.fetch_stock_sector(codes)
            Clock.schedule_once(lambda dt: self._render(rt, sec))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._render({}, {}))

    def _lbl(self, text, color=None, size=None, h=None, bold=False):
        kw = dict(text=text, font_name=_FONT_NAME, color=color or TEXT_W)
        if size: kw["font_size"] = size
        if h: kw["size_hint_y"] = None; kw["height"] = h
        if bold: kw["bold"] = True
        return Label(**kw)

    def _render(self, rt, sec):
        box = self.ids.wl_box
        box.clear_widgets()
        wl = self._cfg.get("watchlist", [])
        # 表头
        hdr = GridLayout(cols=5, size_hint_y=None, height=dp(26), spacing=dp(2))
        for t in ["代码", "名称", "板块", "最新", "涨跌"]:
            hdr.add_widget(self._lbl(t, color=TEXT_D, size=dp(12), bold=True))
        box.add_widget(hdr)

        for w in wl:
            code, _, name = w.partition("|")
            name = name or code
            q = rt.get(code, (name, 0, 0))
            s = sec.get(code, "")
            self._add_row(code, q[0], s, q[1], q[2])

    def _add_row(self, code, name, sec, price, pct):
        pct_color = GREEN if pct >= 0 else RED
        inner = GridLayout(cols=5, size_hint_y=None, height=dp(40), spacing=dp(2))
        inner.add_widget(self._lbl(code, size=dp(12)))
        inner.add_widget(self._lbl(name[:6], size=dp(12)))
        inner.add_widget(self._lbl(sec[:5] or "-", color=TEXT_D, size=dp(11)))
        inner.add_widget(self._lbl(f"{price:.2f}" if price else "-", size=dp(13), bold=True))
        inner.add_widget(self._lbl(f"{pct:+.2f}%" if price else "-", color=pct_color, size=dp(13), bold=True))

        btn = Button(text='', background_normal='',
                     background_color=(0.13, 0.13, 0.22, 1),
                     size_hint_y=None, height=dp(42))
        btn.add_widget(inner)
        btn.bind(on_press=lambda b, c=code: self._open(c))
        self.ids.wl_box.add_widget(btn)

    def _open(self, code):
        main = self.manager.get_screen("main")
        main.ids.code_input.text = code
        self.manager.current = "main"
        Clock.schedule_once(lambda dt: main.do_search(), 0.2)

    def do_batch_add(self, instance=None):
        """批量添加弹窗"""
        ti = TextInput(hint_text="粘贴 6 位代码，逗号/空格/换行分隔", multiline=True,
                       size_hint_y=None, height=dp(130),
                       foreground_color=TEXT_W, background_color=(0.2, 0.2, 0.3, 1),
                       font_name=_FONT_NAME, font_size=dp(14))
        status = self._lbl("", TEXT_D, dp(12), dp(28))
        content = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        content.add_widget(self._lbl("批量添加自选股", size=dp(16), h=dp(32), bold=True))
        content.add_widget(ti)
        content.add_widget(status)
        btns = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        ok = Button(text="确定", background_normal='',
                    background_color=(0.49, 0.34, 0.76, 1), color=TEXT_W,
                    font_name=_FONT_NAME)
        cancel = Button(text="取消", background_normal='',
                        background_color=(0.3, 0.3, 0.3, 1), color=TEXT_W,
                        font_name=_FONT_NAME)
        btns.add_widget(ok); btns.add_widget(cancel)
        content.add_widget(btns)
        popup = Popup(title='', content=content, size_hint=(0.92, 0.55),
                      background_color=(0.1, 0.1, 0.18, 1),
                      separator_height=0, padding=dp(10), title_size=0,
                      auto_dismiss=False)

        def do_add(inst=None):
            codes = list(dict.fromkeys(re.findall(r"(?<!\d)(\d{6})(?!\d)", ti.text)))
            if not codes:
                status.text = "⚠️ 没找到合法 6 位代码"
                return
            existing = [w.split("|", 1)[0] for w in self._cfg.get("watchlist", [])]
            added = 0
            for c in codes:
                if c in existing:
                    continue
                self._cfg = sl.watchlist_add(self._cfg, c)
                added += 1
            _save_cfg(self._cfg)
            status.text = f"✅ 添加 {added} 只（跳过 {len(codes)-added} 只已存在）"
            Clock.schedule_once(lambda dt: (popup.dismiss(), self.do_refresh()), 1.0)

        ok.bind(on_press=do_add)
        cancel.bind(on_press=lambda inst=None: popup.dismiss())
        popup.open()


# ═══════════════════════════════════════════════════════════════════
# 板块概览页面（概念/主题板块列表）
# ═══════════════════════════════════════════════════════════════════
# 常用概念板块指数（东方财富代码 secid）
_SECTOR_LIST = [
    # 新能源 / 科技
    ("光伏", "90.BK1036"),
    ("锂电池", "90.BK1128"),
    ("储能", "90.BK0564"),
    ("半导体", "90.BK1042"),
    ("AI算力", "90.BK1156"),
    ("机器人", "90.BK1180"),
    ("数字经济", "90.BK0800"),
    ("AI应用", "90.BK1162"),
    # 医药 / 消费
    ("创新药", "90.BK0557"),
    ("医疗器械", "90.BK0547"),
    ("白酒", "90.BK0477"),
    ("食品饮料", "90.BK0438"),
    ("免税", "90.BK0986"),
    # 周期 / 金融
    ("稀土永磁", "90.BK1020"),
    ("有色金属", "90.BK0478"),
    ("钢铁", "90.BK0479"),
    ("煤炭", "90.BK0483"),
    ("银行", "90.BK0475"),
    ("券商", "90.BK0477"),
    # 军工 / 制造
    ("军工", "90.BK0481"),
    ("航天航空", "90.BK0480"),
    ("汽车整车", "90.BK1028"),
    ("新能源车", "90.BK1141"),
]


class SectorScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._filter = ""

    def on_enter(self, *a):
        self.do_search()

    def do_search(self, instance=None):
        """过滤板块列表"""
        self._filter = self.ids.sector_search.text.strip() if hasattr(self.ids, "sector_search") else ""
        box = self.ids.sector_box
        box.clear_widgets()
        items = [(n, c) for n, c in _SECTOR_LIST if not self._filter or self._filter in n]
        if not items:
            box.add_widget(self._lbl("没找到匹配的板块", TEXT_D, dp(14)))
            return
        for name, code in items:
            self._add_row(name, code)

    def _lbl(self, text, color=None, size=None, h=None, bold=False):
        kw = dict(text=text, font_name=_FONT_NAME, color=color or TEXT_W)
        if size: kw["font_size"] = size
        if h: kw["size_hint_y"] = None; kw["height"] = h
        if bold: kw["bold"] = True
        return Label(**kw)

    def _add_row(self, name, code):
        inner = BoxLayout(orientation="horizontal", spacing=dp(6), padding=dp(8))
        inner.add_widget(self._lbl(name, size=dp(15), bold=True))
        inner.add_widget(self._lbl(code, color=TEXT_D, size=dp(12)))
        inner.add_widget(Widget())
        inner.add_widget(self._lbl("点击查看成分股", color=TEXT_D, size=dp(11)))

        btn = Button(text='', background_normal='',
                     background_color=(0.13, 0.13, 0.22, 1),
                     size_hint_y=None, height=dp(44))
        btn.add_widget(inner)
        btn.bind(on_press=lambda b, n=name: self._show_detail(n))
        self.ids.sector_box.add_widget(btn)

        sep = Widget(size_hint_y=None, height=dp(1), size_hint_x=1)
        with sep.canvas:
            Color(rgba=(0.2, 0.2, 0.3, 1))
            Rectangle(pos=sep.pos, size=(sep.width, dp(1)))
        self.ids.sector_box.add_widget(sep)

    def _show_detail(self, name):
        """板块详情弹窗（简化版：显示板块描述 + 提示）"""
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        content.add_widget(self._lbl(f"📊 {name}", size=dp(20), h=dp(36), bold=True))
        content.add_widget(self._lbl(
            f"「{name}」是当前热门概念板块。\n"
            "完整板块 K 线图 + 成分股列表 + 资金流向\n"
            "将在后续版本中加入。\n\n"
            "请在主页面搜索具体个股代码进行分析。",
            color=TEXT_D, size=dp(14), h=dp(180)))
        close = Button(text="关闭", size_hint_y=None, height=dp(44),
                       background_normal='', background_color=PRIMARY,
                       color=TEXT_W, font_name=_FONT_NAME)
        content.add_widget(close)
        popup = Popup(title='', content=content, size_hint=(0.9, 0.55),
                      background_color=(0.1, 0.1, 0.18, 1),
                      separator_height=0, title_size=0)
        close.bind(on_press=lambda inst=None: popup.dismiss())
        popup.open()


# ═══════════════════════════════════════════════════════════════════
# 主 App
# ═══════════════════════════════════════════════════════════════════
class StockApp(App):
    title = "股票量价分析"

    def build(self):
        # 注册 KV
        try:
            Builder.load_string(_MAIN_KV)
            Builder.load_string(_WL_KV)
            Builder.load_string(_SECTOR_KV)
        except Exception as e:
            traceback.print_exc()

        self.sm = ScreenManager(transition=SlideTransition())
        self.main = MainScreen(name="main")
        self.wl = WatchlistScreen(name="watchlist")
        self.sector = SectorScreen(name="sector")

        for s in (self.main, self.wl, self.sector):
            s.app = self

        self.sm.add_widget(self.main)
        self.sm.add_widget(self.wl)
        self.sm.add_widget(self.sector)
        return self.sm

    def go_main(self, *a):
        self.sm.current = "main"

    def go_watchlist(self, *a):
        self.sm.current = "watchlist"

    def go_sector(self, *a):
        self.sm.current = "sector"


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    try:
        StockApp().run()
    except Exception as e:
        traceback.print_exc()
        try:
            log_path = os.path.join(
                App.get_running_app().user_data_dir if App.get_running_app() else os.path.dirname(__file__),
                "crash.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
