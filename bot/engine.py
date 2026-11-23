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
    def atr(self, n): return self.k(("atr", n), lambda: rma(tr(self), n))
    def dmi(self, n): return self.k(("dmi", n), lambda: dmi(self, n))

    def hma(self, n):
        def f():
            a = wma(self.c, max(1, math.floor(n / 2))); b = wma(self.c, n)
            return wma(2 * a - b, max(1, round(math.sqrt(n))))
        return self.k(("hma", n), f)

    def tfm(self):
        def f():
            m = math.inf
            for i in range(1, min(self.n, 300)):
                if self.d[i] == self.d[i - 1]:
                    dd = (self.t[i] - self.t[i - 1]) / 6e4
                    if 0 < dd < m: m = dd
            return m if math.isfinite(m) else 5
        return self.k(("tfm",), f)

    def days(self):
        def f():
            di = [0] * self.n; o, h, l, c = [], [], [], []; k = -1; d = ""
            for i in range(self.n):
                if self.d[i] != d:
                    d = self.d[i]; k += 1; o.append(self.o[i]); h.append(self.h[i]); l.append(self.l[i]); c.append(self.c[i])
                else:
                    if self.h[i] > h[k]: h[k] = self.h[i]
                    if self.l[i] < l[k]: l[k] = self.l[i]
                    c[k] = self.c[i]
                di[i] = k
            return dict(di=di, o=o, h=h, l=l, c=c)
        return self.k(("days",), f)

    def datr(self, n):
        def f():
            D = self.days(); K = len(D["c"]); trs = [NAN] * K; A = [NAN] * K
            for k in range(1, K):
                trs[k] = max(D["h"][k] - D["l"][k], abs(D["h"][k] - D["c"][k - 1]), abs(D["l"][k] - D["c"][k - 1]))
            for k in range(K):
                s_ = 0.0; c_ = 0
                for j in range(max(1, k - n), k): s_ += trs[j]; c_ += 1
                if c_ >= 5: A[k] = s_ / c_
            return A
        return self.k(("datr", n), f)

    def cci(self, n):
        def f():
            tp = (self.h + self.l + self.c) / 3; ma = sma(tp, n); o = np.full(self.n, NAN)
            for i in range(n - 1, self.n):
                md = 0.0
                for j in range(i - n + 1, i + 1): md += abs(tp[j] - ma[i])
                md /= n
                o[i] = (tp[i] - ma[i]) / (0.015 * md) if md > 0 else 0.0
            return o
        return self.k(("cci", n), f)

    def willr(self, n):
        def f():
            H, L = self.hh(n), self.ll(n); w = np.full(self.n, NAN)
            for i in range(self.n):
                if fin(H[i]): w[i] = -100 * (H[i] - self.c[i]) / (H[i] - L[i]) if H[i] > L[i] else -50.0
            return w
        return self.k(("wr", n), f)

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


def rma(a, n):
    o = np.full(len(a), NAN); s = 0.0; c = 0; p = NAN
    for i, x in enumerate(a):
        if not fin(x): continue
        if c < n:
            s += x; c += 1
            if c == n: p = s / n; o[i] = p
        else:
            p = (p * (n - 1) + x) / n; o[i] = p
    return o


def tr(x):
    o = np.zeros(x.n)
    for i in range(x.n):
        pc = x.c[i - 1] if i else x.c[i]
        o[i] = max(x.h[i] - x.l[i], abs(x.h[i] - pc), abs(x.l[i] - pc))
    return o


def wma(a, n):
    o = np.full(len(a), NAN); den = n * (n + 1) / 2; f = -1
    for i in range(len(a)):
        if not fin(a[i]): continue
        if f < 0: f = i
        if i - f + 1 >= n:
            s = 0.0
            for j in range(n): s += a[i - j] * (n - j)
            o[i] = s / den
    return o


def dmi(x, n):
    p = np.zeros(x.n); m = np.zeros(x.n)
    for i in range(1, x.n):
        u = x.h[i] - x.h[i - 1]; d = x.l[i - 1] - x.l[i]
        p[i] = u if (u > d and u > 0) else 0.0; m[i] = d if (d > u and d > 0) else 0.0
    t, rp, rm = rma(tr(x), n), rma(p, n), rma(m, n)
    P = np.full(x.n, NAN); M = np.full(x.n, NAN); dx = np.full(x.n, NAN)
    for i in range(x.n):
        if not fin(t[i]) or t[i] == 0: continue
        a = 100 * rp[i] / t[i]; b = 100 * rm[i] / t[i]; P[i] = a; M[i] = b
        dx[i] = 100 * abs(a - b) / (a + b) if a + b > 0 else 0.0
    return P, M, rma(dx, n)


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


# ---------------------------------------------------------------- research strategies
def s_orb5(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    D = x.days(); tf = x.tfm(); A = x.datr(14); k = -1; s = 0; done = 0; oo = NAN; stp = NAN
    for i in range(x.n):
        m = x.m[i]
        if D["di"][i] != k: k = D["di"][i]; s = 0; done = 0; oo = NAN
        if m < 570: o[i] = 0; continue
        if not fin(oo): oo = x.o[i]
        if s != 0:
            if m >= 955: s = 0
            elif s > 0 and x.l[i] <= stp: s = 0
            elif s < 0 and x.h[i] >= stp: s = 0
        elif not done and m + tf >= 570 + p["or"]:
            done = 1; c = x.c[i]; dr = 1 if c > oo else (-1 if c < oo else 0); a = A[k]
            if dr != 0 and fin(a) and (dr > 0 or sh) and m < 955: s = dr; stp = c - dr * p["atrk"] * a
        o[i] = s
    return o


def s_noise(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    D = x.days(); tf = x.tfm(); w = x.vwap(); hist = {}; k = -1; s = 0; st = 0; lb_ = int(p["lb"])
    def flush(a, e):
        for j in range(a, e): hist.setdefault(x.m[j], []).append(abs(x.c[j] / D["o"][D["di"][j]] - 1))
    for i in range(x.n):
        m = x.m[i]
        if D["di"][i] != k:
            if k >= 0: flush(st, i)
            k = D["di"][i]; st = i; s = 0
        if k < 1 or m >= 955: s = 0; o[i] = 0; continue
        if (m + tf) % 30 == 0:
            arr = hist.get(m)
            if arr and len(arr) >= math.ceil(p["lb"] / 2):
                L = arr[-lb_:]; sg = 0.0
                for v in L: sg += v
                sg = sg / len(L) * p["vm"]
                op, pc, c = D["o"][k], D["c"][k - 1], x.c[i]
                ub = max(op, pc) * (1 + sg); lb = min(op, pc) * (1 - sg)
                if s == 0:
                    if c > ub: s = 1
                    elif sh and c < lb: s = -1
                elif s == 1:
                    if c < max(ub, w[i]): s = -1 if (sh and c < lb) else 0
                elif c > min(lb, w[i]): s = 1 if c > ub else 0
        o[i] = s
    return o


def s_imom(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    D = x.days(); tf = x.tfm(); k = -1; r = NAN; s = 0
    for i in range(x.n):
        m = x.m[i]
        if D["di"][i] != k: k = D["di"][i]; r = NAN; s = 0
        if k >= 1 and m + tf == 600: r = x.c[i] / D["c"][k - 1] - 1
        if m + tf == 930 and fin(r) and abs(r) * 100 >= p["thr"]: s = 1 if r > 0 else (-1 if (r < 0 and sh) else 0)
        o[i] = s
    return o


def s_gapgo(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    D = x.days(); w = x.vwap(); k = -1; s = 0; g = 0.0; H = -math.inf; L = math.inf; done = 0
    for i in range(x.n):
        m, c = x.m[i], x.c[i]
        if D["di"][i] != k:
            k = D["di"][i]; s = 0; done = 0; H = -math.inf; L = math.inf
            g = (D["o"][k] / D["c"][k - 1] - 1) * 100 if k >= 1 else 0.0
        if m < 570 + p["or"]: H = max(H, x.h[i]); L = min(L, x.l[i]); s = 0
        elif m >= 955: s = 0
        elif s == 0 and not done:
            if g >= p["gap"] and c > H and c > w[i]: s = 1; done = 1
            elif sh and g <= -p["gap"] and c < L and c < w[i]: s = -1; done = 1
        elif s == 1 and c < w[i]: s = 0
        elif s == -1 and c > w[i]: s = 0
        o[i] = s
    return o


def s_gapfade(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    D = x.days(); k = -1; s = 0; dr = 0; first = -1
    for i in range(x.n):
        if D["di"][i] != k:
            k = D["di"][i]; s = 0; dr = 0; first = i
            if k >= 1:
                g = (D["o"][k] / D["c"][k - 1] - 1) * 100
                if p["gap"] <= abs(g) <= p["max"]: dr = 1 if g < 0 else (-1 if sh else 0)
        if k < 1 or x.m[i] >= 955: s = 0
        elif i == first:
            if dr > 0 and x.c[i] < D["c"][k - 1]: s = 1
            elif dr < 0 and x.c[i] > D["c"][k - 1]: s = -1
        elif s > 0 and x.h[i] >= D["c"][k - 1]: s = 0
        elif s < 0 and x.l[i] <= D["c"][k - 1]: s = 0
        o[i] = s
    return o


def s_dthrust(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    D = x.days(); k = -1; s = 0; up = dn = NAN; n = int(p["n"])
    for i in range(x.n):
        if D["di"][i] != k:
            k = D["di"][i]; s = 0; up = dn = NAN
            if k >= n:
                HH = max(D["h"][k - n:k]); LL = min(D["l"][k - n:k]); HC = max(D["c"][k - n:k]); LC = min(D["c"][k - n:k])
                R = max(HH - LC, HC - LL); up = D["o"][k] + p["k1"] * R; dn = D["o"][k] - p["k2"] * R
        if not fin(up) or x.m[i] >= 955: s = 0
        elif x.c[i] > up: s = 1
        elif x.c[i] < dn: s = -1 if sh else 0
        o[i] = s
    return o


def s_lwvb(x, p, sh):
    o = [0] * x.n
    if not _intra(x): return o
    D = x.days(); k = -1; s = 0; done = 0; up = dn = NAN
    for i in range(x.n):
        if D["di"][i] != k:
            k = D["di"][i]; s = 0; done = 0; up = dn = NAN
            if k >= 1:
                R = D["h"][k - 1] - D["l"][k - 1]; up = D["o"][k] + p["k"] * R; dn = D["o"][k] - p["k"] * R
        if x.m[i] >= 955: s = 0
        elif s == 0 and not done and fin(up):
            if x.c[i] > up: s = 1; done = 1
            elif sh and x.c[i] < dn: s = -1; done = 1
        o[i] = s
    return o


def s_connors(x, p, sh):
    T, E, r, c = x.sma(int(p["trend"])), x.sma(int(p["ex"])), x.rsi(2), x.c
    def step(i, s):
        if not fin(T[i]) or not fin(r[i]): return s
        if s == 0: return 1 if (c[i] > T[i] and r[i] < p["lo"]) else (-1 if (sh and c[i] < T[i] and r[i] > 100 - p["lo"]) else 0)
        if s == 1: return 0 if c[i] > E[i] else 1
        return 0 if c[i] < E[i] else -1
    return _machine(x.n, step)


def s_ibs(x, p, sh):
    h, l, c = x.h, x.l, x.c
    def step(i, s):
        if i < 1: return 0
        v = (c[i] - l[i]) / (h[i] - l[i]) if h[i] > l[i] else 0.5
        if s == 0: return 1 if v < p["lo"] else (-1 if (sh and v > p["hi"]) else 0)
        if s == 1: return 0 if (v > p["hi"] or c[i] > h[i - 1]) else 1
        return 0 if (v < p["lo"] or c[i] < l[i - 1]) else -1
    return _machine(x.n, step)


def s_willr(x, p, sh):
    w = x.willr(int(p["n"]))
    def step(i, s):
        if i < 1 or not fin(w[i - 1]): return s
        if s == 0: return 1 if (w[i - 1] < p["lo"] and w[i] >= p["lo"]) else (-1 if (sh and w[i - 1] > p["hi"] and w[i] <= p["hi"]) else 0)
        if s == 1: return 0 if w[i] > p["hi"] else 1
        return 0 if w[i] < p["lo"] else -1
    return _machine(x.n, step)


def s_vwaprev(x, p, sh):
    if not _intra(x): return [0] * x.n
    w, c = x.vwap(), x.c; d = c - w; sdv = sd(d, int(p["n"]))
    def step(i, s):
        m = x.m[i]
        if m >= 955: return 0
        if not fin(sdv[i]): return s
        if s == 0:
            if m < 585 or m >= 945: return 0
            return 1 if d[i] < -p["z"] * sdv[i] else (-1 if (sh and d[i] > p["z"] * sdv[i]) else 0)
        if s == 1: return 0 if c[i] >= w[i] else 1
        return 0 if c[i] <= w[i] else -1
    return _machine(x.n, step)


def s_keltner(x, p, sh):
    e, a, c = x.ema(int(p["n"])), x.atr(int(p["a"])), x.c
    def step(i, s):
        if not fin(e[i]) or not fin(a[i]): return s
        u = e[i] + p["m"] * a[i]; d = e[i] - p["m"] * a[i]
        if s == 0: return 1 if c[i] > u else (-1 if (sh and c[i] < d) else 0)
        if s == 1: return 0 if c[i] < e[i] else 1
        return 0 if c[i] > e[i] else -1
    return _machine(x.n, step)


def s_squeeze(x, p, sh):
    n = int(p["n"]); m, up, lo_ = x.bb(n, 2); a, H, L, c = x.atr(n), x.hh(n), x.ll(n), x.c
    on = [0] * x.n
    for i in range(x.n):
        if fin(up[i]) and fin(a[i]): on[i] = 1 if (up[i] < m[i] + p["kc"] * a[i] and lo_[i] > m[i] - p["kc"] * a[i]) else 0
    def step(i, s):
        if i < 1 or not fin(a[i]) or not fin(H[i]) or not fin(m[i]): return s
        v = c[i] - ((H[i] + L[i]) / 2 + m[i]) / 2
        if s == 0:
            if on[i - 1] and not on[i]: return 1 if v > 0 else (-1 if (sh and v < 0) else 0)
            return 0
        if s == 1: return 0 if v < 0 else 1
        return 0 if v > 0 else -1
    return _machine(x.n, step)


def s_cci(x, p, sh):
    v = x.cci(int(p["n"])); lv = p["lv"]
    def step(i, s):
        if i < 1 or not fin(v[i - 1]): return s
        if s == 0: return 1 if (v[i - 1] <= lv and v[i] > lv) else (-1 if (sh and v[i - 1] >= -lv and v[i] < -lv) else 0)
        if s == 1: return 0 if v[i] < 0 else 1
        return 0 if v[i] > 0 else -1
    return _machine(x.n, step)


def s_supertrend(x, p, sh):
    a = x.atr(int(p["n"])); h, l, c = x.h, x.l, x.c; o = [0] * x.n; fu = fl = NAN; dr = 0
    for i in range(x.n):
        if not fin(a[i]): continue
        mid = (h[i] + l[i]) / 2; bu = mid + p["m"] * a[i]; bl = mid - p["m"] * a[i]
        if dr == 0: fu, fl, dr = bu, bl, 1
        else:
            fu = bu if (bu < fu or c[i - 1] > fu) else fu
            fl = bl if (bl > fl or c[i - 1] < fl) else fl
            if dr == 1 and c[i] < fl: dr = -1
            elif dr == -1 and c[i] > fu: dr = 1
        o[i] = 1 if dr > 0 else (-1 if sh else 0)
    return o


def s_psar(x, p, sh):
    h, l, c = x.h, x.l, x.c; o = [0] * x.n
    if x.n < 3: return o
    up = c[1] >= c[0]; sar = l[0] if up else h[0]; ep = h[1] if up else l[1]; af = p["af"]
    o[1] = 1 if up else (-1 if sh else 0)
    for i in range(2, x.n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2])
            if l[i] < sar: up = False; sar = ep; ep = l[i]; af = p["af"]
            elif h[i] > ep: ep = h[i]; af = min(af + p["af"], p["mx"])
        else:
            sar = max(sar, h[i - 1], h[i - 2])
            if h[i] > sar: up = True; sar = ep; ep = h[i]; af = p["af"]
            elif l[i] < ep: ep = l[i]; af = min(af + p["af"], p["mx"])
        o[i] = 1 if up else (-1 if sh else 0)
    return o


def s_hma(x, p, sh):
    f, s = x.hma(int(p["f"])), x.hma(int(p["s"]))
    return _regime(x.n, lambda i: 1 if _gt(f[i], s[i]) else (-1 if _lt(f[i], s[i]) and sh else 0))


def s_heikin(x, p, sh):
    o_, h, l, c = x.o, x.h, x.l, x.c; out = [0] * x.n
    ho = (o_[0] + c[0]) / 2; hc = (o_[0] + h[0] + l[0] + c[0]) / 4; ru = rd = 0; s = 0
    for i in range(x.n):
        if i > 0: ho = (ho + hc) / 2; hc = (o_[i] + h[i] + l[i] + c[i]) / 4
        bull, bear = hc > ho, hc < ho
        ru = ru + 1 if bull else 0; rd = rd + 1 if bear else 0
        if s == 0: s = 1 if ru >= p["k"] else (-1 if (sh and rd >= p["k"]) else 0)
        elif s == 1:
            if not bull: s = 0
        elif not bear: s = 0
        out[i] = s
    return out


def s_adx(x, p, sh):
    P, M, A = x.dmi(int(p["n"]))
    def step(i, s):
        if not fin(A[i]): return s
        if s == 0: return 1 if (P[i] > M[i] and A[i] > p["th"]) else (-1 if (sh and M[i] > P[i] and A[i] > p["th"]) else 0)
        if s == 1: return 0 if P[i] < M[i] else 1
        return 0 if P[i] > M[i] else -1
    return _machine(x.n, step)


def s_ichimoku(x, p, sh):
    t_, k_, sb = int(p["t"]), int(p["k"]), int(p["sb"])
    ht, lt, hk, lk, hb, lb, c = x.hh(t_), x.ll(t_), x.hh(k_), x.ll(k_), x.hh(sb), x.ll(sb), x.c
    tk = lambda i: (ht[i] + lt[i]) / 2
    kj = lambda i: (hk[i] + lk[i]) / 2
    def step(i, s):
        if i < k_: return s
        j = i - k_; A = (tk(j) + kj(j)) / 2; B = (hb[j] + lb[j]) / 2; t = tk(i); k = kj(i)
        if not (fin(A) and fin(B) and fin(t) and fin(k)): return s
        top, bot = max(A, B), min(A, B)
        if s == 0: return 1 if (t > k and c[i] > top) else (-1 if (sh and t < k and c[i] < bot) else 0)
        if s == 1: return 0 if (t < k or c[i] < bot) else 1
        return 0 if (t > k or c[i] > top) else -1
    return _machine(x.n, step)


STRATS.update({
    "orb5": ("ORB stocks-in-play (Zarattini et al.)", s_orb5, {"or": 5, "atrk": 0.1}),
    "noise": ("Noise-area momentum (Zarattini et al.)", s_noise, {"lb": 14, "vm": 1}),
    "imom": ("Last half-hour momentum (Gao et al.)", s_imom, {"thr": 0}),
    "gapgo": ("Gap and go (VWAP hold)", s_gapgo, {"gap": 2, "or": 15}),
    "gapfade": ("Gap fade to prior close", s_gapfade, {"gap": 1, "max": 6}),
    "dthrust": ("Dual Thrust (Chalek)", s_dthrust, {"n": 4, "k1": 0.5, "k2": 0.5}),
    "lwvb": ("Volatility breakout (Larry Williams)", s_lwvb, {"k": 0.5}),
    "connors": ("RSI(2) pullback (Connors)", s_connors, {"trend": 200, "ex": 5, "lo": 10}),
    "ibs": ("Internal bar strength reversal", s_ibs, {"lo": 0.2, "hi": 0.8}),
    "willr": ("Williams %R reversal", s_willr, {"n": 14, "lo": -80, "hi": -20}),
    "vwaprev": ("VWAP reversion (z-score bands)", s_vwaprev, {"n": 20, "z": 2}),
    "keltner": ("Keltner channel breakout", s_keltner, {"n": 20, "a": 10, "m": 2}),
    "squeeze": ("Volatility squeeze release (TTM-style)", s_squeeze, {"n": 20, "kc": 1.5}),
    "cci": ("CCI momentum (Lambert)", s_cci, {"n": 20, "lv": 100}),
    "supertrend": ("Supertrend (Seban)", s_supertrend, {"n": 10, "m": 3}),
    "psar": ("Parabolic SAR (Wilder)", s_psar, {"af": 0.02, "mx": 0.2}),
    "hma": ("Hull moving average cross", s_hma, {"f": 9, "s": 21}),
    "heikin": ("Heikin-Ashi trend", s_heikin, {"k": 2}),
    "adx": ("ADX trend + DI cross (Wilder)", s_adx, {"n": 14, "th": 25}),
    "ichimoku": ("Ichimoku cloud (Hosoda)", s_ichimoku, {"t": 9, "k": 26, "sb": 52}),
})


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
