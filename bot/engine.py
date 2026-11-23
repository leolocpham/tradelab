"""TradeLab strategy engine (Python port of the indicators, strategies,
backtester and stats in index.html). Logic is kept line-for-line equivalent
so the bot picks strategies the same way the web page tests them."""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
NAN = float("nan")
fin = math.isfinite


# ---------------------------------------------------------------- bars
class Bars:
    """Column store of OHLCV bars plus Eastern-time date (d) and minute-of-day (m)."""

    def __init__(self, rows):
        # rows: list of (t_ms, o, h, l, c, v)
        self.t = [int(r[0]) for r in rows]
        self.o = np.array([r[1] for r in rows], float)
        self.h = np.array([r[2] for r in rows], float)
        self.l = np.array([r[3] for r in rows], float)
        self.c = np.array([r[4] for r in rows], float)
        self.v = np.array([r[5] for r in rows], float)
        self.d, self.m = [], []
        for t in self.t:
            dt = datetime.fromtimestamp(t / 1000, ET)
            self.d.append(dt.strftime("%Y-%m-%d"))
            self.m.append(dt.hour * 60 + dt.minute)
        self.n = len(self.t)
        self._cache = {}

    def k(self, key, f):
        if key not in self._cache:
            self._cache[key] = f()
        return self._cache[key]

    def sma(self, n): return self.k(("sma", n), lambda: sma(self.c, n))
    def ema(self, n): return self.k(("ema", n), lambda: ema(self.c, n))
    def rsi(self, n): return self.k(("rsi", n), lambda: rsi(self.c, n))
    def hh(self, n): return self.k(("hh", n), lambda: hi(self.h, n))
    def ll(self, n): return self.k(("ll", n), lambda: lo(self.l, n))
    def vwap(self): return self.k(("vwap",), lambda: vwap(self))

    def bb(self, n, k):
        def f():
            m = self.sma(n)
            s = sd(self.c, n)
            return m, m + k * s, m - k * s
        return self.k(("bb", n, k), f)

    def macd(self, f_, s_, g):
        def f():
            m = self.ema(f_) - self.ema(s_)
            sg = ema(m, g)
            return m, sg
        return self.k(("macd", f_, s_, g), f)

    def stoch(self, n, d):
        def f():
            H, L = self.hh(n), self.ll(n)
            raw = np.full(self.n, NAN)
            for i in range(self.n):
                raw[i] = 100 * (self.c[i] - L[i]) / (H[i] - L[i]) if H[i] > L[i] else 50.0
            raw[: max(n - 1, 0)] = NAN
            k = sma(raw, 3)
            return k, sma(k, d)
        return self.k(("st", n, d), f)


# ---------------------------------------------------------------- indicators
def sma(a, n):
    o = np.full(len(a), NAN); s = 0.0; f = -1
    for i, x in enumerate(a):
        if not fin(x): continue
        if f < 0: f = i
        s += x; c = i - f + 1
        if c > n: s -= a[i - n]
        if c >= n: o[i] = s / n
    return o


def ema(a, n):
    o = np.full(len(a), NAN); k = 2 / (n + 1); s = 0.0; c = 0; p = NAN
    for i, x in enumerate(a):
        if not fin(x): continue
        if c < n:
            s += x; c += 1
            if c == n: p = s / n; o[i] = p
        else:
            p = x * k + p * (1 - k); o[i] = p
    return o


def sd(a, n):
    o = np.full(len(a), NAN); s = 0.0; q = 0.0
    for i in range(len(a)):
        s += a[i]; q += a[i] * a[i]
        if i >= n: s -= a[i - n]; q -= a[i - n] * a[i - n]
        if i >= n - 1:
            m = s / n; o[i] = math.sqrt(max(q / n - m * m, 0))
    return o


def rsi(c, n):
    o = np.full(len(c), NAN); g = 0.0; l = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; u = d if d > 0 else 0.0; w = -d if d < 0 else 0.0
        if i <= n:
            g += u; l += w
            if i == n: g /= n; l /= n
            else: continue
        else:
            g = (g * (n - 1) + u) / n; l = (l * (n - 1) + w) / n
        o[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return o


def hi(a, n):
    o = np.full(len(a), NAN)
    for i in range(n - 1, len(a)): o[i] = a[i - n + 1: i + 1].max()
    return o


def lo(a, n):
    o = np.full(len(a), NAN)
    for i in range(n - 1, len(a)): o[i] = a[i - n + 1: i + 1].min()
    return o


def vwap(x):
    o = np.zeros(x.n); pv = 0.0; v = 0.0; d = ""
    for i in range(x.n):
        if x.d[i] != d: d = x.d[i]; pv = 0.0; v = 0.0
        tp = (x.h[i] + x.l[i] + x.c[i]) / 3
        pv += tp * x.v[i]; v += x.v[i]
        o[i] = pv / v if v > 0 else tp
    return o


# ---------------------------------------------------------------- strategies
def _regime(n, f): return [f(i) for i in range(n)]


def _machine(n, step):
    out = []; s = 0
    for i in range(n):
        s = step(i, s); out.append(s)
    return out


def _gt(a, b): return fin(a) and fin(b) and a > b
def _lt(a, b): return fin(a) and fin(b) and a < b
def _intra(x): return x.n > 1 and (x.t[1] - x.t[0]) < 864e5


def s_sma(x, p, sh):
    f, s = x.sma(p["fast"]), x.sma(p["slow"])
    return _regime(x.n, lambda i: 1 if _gt(f[i], s[i]) else (-1 if _lt(f[i], s[i]) and sh else 0))


def s_ema(x, p, sh):
    f, s = x.ema(p["fast"]), x.ema(p["slow"])
    return _regime(x.n, lambda i: 1 if _gt(f[i], s[i]) else (-1 if _lt(f[i], s[i]) and sh else 0))


def s_rsi(x, p, sh):
    r = x.rsi(p["n"])
    def step(i, s):
        v = r[i]
        if not fin(v): return s
        if s == 0: return 1 if v < p["lo"] else (-1 if sh and v > p["hi"] else 0)
        if s == 1: return (-1 if sh else 0) if v > p["hi"] else 1
        return 1 if v < p["lo"] else -1
    return _machine(x.n, step)


def s_bb(x, p, sh):
    m, up, lo_ = x.bb(p["n"], p["k"]); c = x.c
    def step(i, s):
        if not fin(m[i]): return s
        if s == 0: return 1 if c[i] < lo_[i] else (-1 if sh and c[i] > up[i] else 0)
        if s == 1: return 0 if c[i] >= m[i] else 1
        return 0 if c[i] <= m[i] else -1
    return _machine(x.n, step)


def s_don(x, p, sh):
    H, L, HX, LX, c = x.hh(p["n"]), x.ll(p["n"]), x.hh(p["m"]), x.ll(p["m"]), x.c
    def step(i, s):
        if i < 1 or not fin(H[i - 1]): return s
        if s == 0: return 1 if c[i] > H[i - 1] else (-1 if sh and c[i] < L[i - 1] else 0)
        if s == 1: return 0 if c[i] < LX[i - 1] else 1
        return 0 if c[i] > HX[i - 1] else -1
    return _machine(x.n, step)


def s_macd(x, p, sh):
    m, sg = x.macd(p["f"], p["s"], p["g"])
    return _regime(x.n, lambda i: 1 if _gt(m[i], sg[i]) else (-1 if _lt(m[i], sg[i]) and sh else 0))


def s_vwap(x, p, sh):
    if not _intra(x): return [0] * x.n
    w, c, b = x.vwap(), x.c, p["band"] / 100
    def step(i, s):
        m = x.m[i]
        if m < 570 + p["wait"] or m >= 955: return 0
        if s == 0: return 1 if c[i] > w[i] * (1 + b) else (-1 if sh and c[i] < w[i] * (1 - b) else 0)
        if s == 1: return 0 if c[i] < w[i] else 1
        return 0 if c[i] > w[i] else -1
    return _machine(x.n, step)


def s_orb(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    d = ""; H = -math.inf; L = math.inf; done = 0; s = 0; orm = p["or"]
    for i in range(x.n):
        if x.d[i] != d: d = x.d[i]; H = -math.inf; L = math.inf; done = 0; s = 0
        m = x.m[i]
        if 570 <= m < 570 + orm:
            H = max(H, x.h[i]); L = min(L, x.l[i]); s = 0
        elif 570 + orm <= m < 955 and math.isfinite(H):
            mid = (H + L) / 2
            if s == 0 and not done:
                if x.c[i] > H: s = 1; done = 1
                elif sh and x.c[i] < L: s = -1; done = 1
            elif s == 1 and x.c[i] < mid: s = 0
            elif s == -1 and x.c[i] > mid: s = 0
        else:
            s = 0
        o[i] = s
    return o


def s_stoch(x, p, sh):
    K, D = x.stoch(p["n"], p["d"])
    def step(i, s):
        if i < 1 or not fin(D[i - 1]): return s
        up = K[i] > D[i] and K[i - 1] <= D[i - 1]
        dn = K[i] < D[i] and K[i - 1] >= D[i - 1]
        if s == 0: return 1 if up and K[i] < p["lo"] else (-1 if sh and dn and K[i] > p["hi"] else 0)
        if s == 1: return 0 if K[i] > p["hi"] else 1
        return 0 if K[i] < p["lo"] else -1
    return _machine(x.n, step)


STRATS = {
    "sma":   ("SMA crossover", s_sma, {"fast": 10, "slow": 30}),
    "ema":   ("EMA crossover", s_ema, {"fast": 9, "slow": 21}),
    "rsi":   ("RSI mean reversion", s_rsi, {"n": 14, "lo": 30, "hi": 70}),
    "bb":    ("Bollinger mean reversion", s_bb, {"n": 20, "k": 2}),
    "don":   ("Donchian breakout", s_don, {"n": 20, "m": 10}),
    "macd":  ("MACD signal cross", s_macd, {"f": 12, "s": 26, "g": 9}),
    "vwap":  ("VWAP trend (intraday)", s_vwap, {"band": 0.1, "wait": 15}),
    "orb":   ("Opening range breakout (intraday)", s_orb, {"or": 15}),
    "stoch": ("Stochastic reversal", s_stoch, {"n": 14, "d": 3, "lo": 20, "hi": 80}),
}


def signals(x, sid, params=None, shorts=False):
    name, fn, dp = STRATS[sid]
    return fn(x, {**dp, **(params or {})}, shorts)


# ---------------------------------------------------------------- backtest
def backtest(x, pos, o, frm=0, to=None):
    """o: dict cap, size(0-1), comm($/sh), slip(bps), stop(frac), tp(frac), eod(bool)."""
    to = x.n if to is None else to
    cs = o["slip"] / 1e4
    eq = np.zeros(to - frm); trades = []
    st = {"cash": o["cap"], "s": 0, "q": 0, "ep": 0.0, "ei": 0}
    blk = None; inp = 0

    def fill(px, buy): return px * (1 + cs if buy else 1 - cs)

    def close(i, px, why):
        s, q = st["s"], st["q"]; xp = fill(px, s < 0)
        st["cash"] += s * q * xp - q * o["comm"]
        trades.append(dict(side=s, ei=st["ei"], xi=i, ep=st["ep"], xp=xp, q=q,
                           pnl=s * q * (xp - st["ep"]) - 2 * q * o["comm"], ret=s * (xp / st["ep"] - 1), why=why))
        st["s"] = 0; st["q"] = 0

    def open_(i, px, side):
        p = fill(px, side > 0); n = math.floor(st["cash"] * o["size"] / p)
        if n < 1: return
        st["q"] = n; st["cash"] -= side * n * p + n * o["comm"]; st["s"] = side; st["ep"] = p; st["ei"] = i

    for i in range(frm, to):
        last = i == to - 1
        eod_bar = o["eod"] and (i + 1 >= x.n or x.d[i + 1] != x.d[i])
        if i > frm:
            tg = pos[i - 1]
            if blk is not None:
                if tg != blk: blk = None
                else: tg = 0
            if eod_bar and st["s"] == 0: tg = 0
            if tg != st["s"]:
                if st["s"] != 0: close(i, x.o[i], "signal")
                if tg != 0 and not last: open_(i, x.o[i], tg)
        if st["s"] != 0 and (o["stop"] > 0 or o["tp"] > 0):
            side = st["s"]; ep = st["ep"]
            if side > 0:
                sp, tp = ep * (1 - o["stop"]), ep * (1 + o["tp"])
                if o["stop"] > 0 and x.l[i] <= sp: close(i, min(x.o[i], sp), "stop"); blk = side
                elif o["tp"] > 0 and x.h[i] >= tp: close(i, max(x.o[i], tp), "target"); blk = side
            else:
                sp, tp = ep * (1 + o["stop"]), ep * (1 - o["tp"])
                if o["stop"] > 0 and x.h[i] >= sp: close(i, max(x.o[i], sp), "stop"); blk = side
                elif o["tp"] > 0 and x.l[i] <= tp: close(i, min(x.o[i], tp), "target"); blk = side
        if st["s"] != 0 and (eod_bar or last): close(i, x.c[i], "end" if last else "eod")
        if st["s"] != 0: inp += 1
        eq[i - frm] = st["cash"] + st["s"] * st["q"] * x.c[i]
    r = dict(eq=eq, trades=trades, frm=frm, to=to, inp=inp)
    r["m"] = stats(x, r, o)
    return r


def stats(x, r, o):
    eq = r["eq"]; n = len(eq)
    if not n: return {}
    yrs = max((x.t[r["to"] - 1] - x.t[r["frm"]]) / (365.25 * 864e5), 1 / 365)
    pk = o["cap"]; dd = 0.0; prev = o["cap"]; sm = sq = dq = 0.0
    for e in eq:
        pk = max(pk, e); dd = max(dd, 1 - e / pk)
        rt = e / prev - 1; prev = e; sm += rt; sq += rt * rt
        if rt < 0: dq += rt * rt
    mean = sm / n; sdv = math.sqrt(max(sq / n - mean * mean, 0)); dsd = math.sqrt(dq / n); bpy = n / yrs; f = eq[-1]
    gw = gl = 0.0; w = 0; ar = 0.0; hb = 0
    for t in r["trades"]:
        if t["pnl"] > 0: gw += t["pnl"]; w += 1
        else: gl -= t["pnl"]
        ar += t["ret"]; hb += t["xi"] - t["ei"]
    nt = len(r["trades"]); cagr = (f / o["cap"]) ** (1 / yrs) - 1 if f > 0 else -1
    pf = gw / gl if gl > 0 else (math.inf if gw > 0 else NAN)
    return dict(tot=f / o["cap"] - 1, cagr=cagr, dd=dd, sharpe=mean / sdv * math.sqrt(bpy) if sdv > 0 else 0.0,
                sortino=mean / dsd * math.sqrt(bpy) if dsd > 0 else 0.0, n=nt, win=w / nt if nt else NAN,
                pf=pf, avg=ar / nt if nt else NAN, hold=hb / nt if nt else NAN,
                bh=x.c[r["to"] - 1] / x.c[r["frm"]] - 1, exp=r["inp"] / n, calmar=cagr / dd if dd > 0 else 0.0, fin=f)
