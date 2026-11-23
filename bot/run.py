"""TradeLab daily bot: screen gappers, pick the best-fitting strategy per stock,
and paper-trade them through the trading day on Alpaca.

PAPER TRADING ONLY. The trading URL is hard-wired to paper-api.alpaca.markets.

Run:  python bot/run.py            (auto: screen + trade, resumes if a session already ran)
      python bot/run.py --test     (screen the most recent session, no orders; works any time)
Env:  APCA_API_KEY_ID, APCA_API_SECRET_KEY
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from engine import ET, STRATS, Bars, backtest, signals  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TF_MS = {"1Min": 60_000, "5Min": 300_000, "15Min": 900_000}
SYM_RE = re.compile(r"^[A-Z]{1,5}$")
MAX_RUN_MIN = 340  # GitHub job limit is 360


def clean(o):
    """Make values JSON-safe (NaN/inf -> None, numpy -> float)."""
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if hasattr(o, "item"): o = o.item()
    if isinstance(o, float) and not math.isfinite(o): return None
    return o


def et_minute(t_ms):
    d = datetime.fromtimestamp(t_ms / 1000, ET)
    return d.hour * 60 + d.minute


# ---------------------------------------------------------------- Alpaca client
class Alpaca:
    DATA = "https://data.alpaca.markets"
    TRADE = "https://paper-api.alpaca.markets"  # paper only, on purpose

    def __init__(self, key, secret):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _req(self, method, base, path, params=None, body=None, ok404=False):
        for a in range(6):
            try:
                r = self.s.request(method, base + path, params=params, json=body, timeout=30)
            except Exception as e:  # network blip
                if a == 5: raise
                time.sleep(2 * (a + 1)); continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(3 * (a + 1)); continue
            if ok404 and r.status_code == 404: return None
            if r.status_code >= 400:
                raise RuntimeError(f"Alpaca {method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.text else {}
        raise RuntimeError(f"Alpaca {method} {path}: gave up after retries")

    # trading
    def clock(self): return self._req("GET", self.TRADE, "/v2/clock")
    def calendar(self, start, end): return self._req("GET", self.TRADE, "/v2/calendar", {"start": start, "end": end})
    def account(self): return self._req("GET", self.TRADE, "/v2/account")
    def positions(self): return {p["symbol"]: p for p in self._req("GET", self.TRADE, "/v2/positions")}
    def open_orders(self): return self._req("GET", self.TRADE, "/v2/orders", {"status": "open", "limit": 500})
    def cancel_order(self, oid): return self._req("DELETE", self.TRADE, f"/v2/orders/{oid}", ok404=True)
    def close_position(self, sym): return self._req("DELETE", self.TRADE, f"/v2/positions/{sym}", ok404=True)

    def submit(self, sym, qty, side, cid):
        return self._req("POST", self.TRADE, "/v2/orders", body={
            "symbol": sym, "qty": str(qty), "side": side, "type": "market", "time_in_force": "day", "client_order_id": cid})

    def fills(self, day):
        return self._req("GET", self.TRADE, "/v2/account/activities/FILL", {"date": day, "page_size": 100})

    # market data
    def most_actives(self, top=100):
        j = self._req("GET", self.DATA, "/v1beta1/screener/stocks/most-actives", {"by": "volume", "top": top})
        return [x["symbol"] for x in j.get("most_actives", [])]

    def movers(self, top=50):
        j = self._req("GET", self.DATA, "/v1beta1/screener/stocks/movers", {"top": top})
        return [x["symbol"] for x in j.get("gainers", []) + j.get("losers", [])]

    def snapshots(self, syms, feed="iex"):
        out = {}
        for i in range(0, len(syms), 100):
            out.update(self._req("GET", self.DATA, "/v2/stocks/snapshots",
                                 {"symbols": ",".join(syms[i:i + 100]), "feed": feed}) or {})
        return out

    def bars(self, syms, tf, start, end, feed):
        out = {s: [] for s in syms}
        for i in range(0, len(syms), 50):
            tok = None
            while True:
                p = {"symbols": ",".join(syms[i:i + 50]), "timeframe": tf, "start": start.isoformat(),
                     "end": end.isoformat(), "limit": 10000, "feed": feed, "adjustment": "split"}
                if tok: p["page_token"] = tok
                j = self._req("GET", self.DATA, "/v2/stocks/bars", p)
                for s, rows in (j.get("bars") or {}).items():
                    out.setdefault(s, []).extend(
                        [int(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp() * 1000),
                         b["o"], b["h"], b["l"], b["c"], b["v"]] for b in rows)
                tok = j.get("next_page_token")
                if not tok: break
        return out


class RealClock:
    def now(self): return datetime.now(ET)
    def sleep(self, s): time.sleep(max(0, s))


# ---------------------------------------------------------------- bot
class Bot:
    def __init__(self, api, clock, cfg, root=ROOT, commit=False, log=print):
        self.api, self.clock, self.cfg, self.commit_on, self.log_ = api, clock, cfg, commit, log
        self.data = Path(root) / "data"
        (self.data / "days").mkdir(parents=True, exist_ok=True)
        self.tf = cfg["timeframe"]; self.tfms = TF_MS[self.tf]
        self.started = clock.now()
        self.last_commit = 0.0

    def log(self, msg): self.log_(f"[{self.clock.now():%H:%M:%S}] {msg}")

    def at(self, hhmm, day=None):
        h, m = map(int, hhmm.split(":"))
        d = day or self.day_dt
        return d.replace(hour=h, minute=m, second=0, microsecond=0)

    # ---------- persistence
    def day_path(self): return self.data / "days" / f"{self.day}{'-test' if self.test else ''}.json"

    def load_day(self):
        p = self.day_path()
        return json.loads(p.read_text()) if p.exists() else {"date": self.day, "mode": "test" if self.test else "paper", "events": [], "blocked": {}}

    def save(self, commit_msg=None, force=False):
        self.D["updated"] = self.clock.now().isoformat(timespec="seconds")
        txt = json.dumps(clean(self.D), indent=1)
        self.day_path().write_text(txt)
        (self.data / "latest.json").write_text(txt)
        if commit_msg and self.commit_on and (force or time.time() - self.last_commit > 1500):
            self.git_commit(commit_msg)

    def git_commit(self, msg):
        try:
            subprocess.run(["git", "add", "data"], cwd=self.data.parent, check=True)
            if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.data.parent).returncode == 0: return
            subprocess.run(["git", "commit", "-q", "-m", msg], cwd=self.data.parent, check=True)
            for _ in range(3):
                subprocess.run(["git", "pull", "-q", "--rebase"], cwd=self.data.parent)
                if subprocess.run(["git", "push", "-q"], cwd=self.data.parent).returncode == 0: break
                time.sleep(5)
            self.last_commit = time.time()
        except Exception as e:
            self.log(f"commit failed: {e}")

    def event(self, kind, **kw):
        e = {"time": self.clock.now().strftime("%H:%M"), "kind": kind, **kw}
        self.D["events"].append(e); self.log(f"{kind} {kw}")

    # ---------- session setup
    def setup(self, test):
        self.test = test
        now = self.clock.now()
        cal = self.api.calendar((now - timedelta(days=10)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
        today = now.strftime("%Y-%m-%d")
        sess = [c for c in cal if c["date"] <= today]
        if not test:
            if not sess or sess[-1]["date"] != today:
                return "Market closed today"
            c = sess[-1]
        else:  # most recent session that has opened
            c = next((c for c in reversed(sess) if c["date"] < today or now >= self.at(c["open"], now)), sess[-1])
        self.day = c["date"]
        self.day_dt = datetime.strptime(c["date"], "%Y-%m-%d").replace(tzinfo=ET)
        self.open_t, self.close_t = self.at(c["open"]), self.at(c["close"])
        self.flatten_t = self.close_t - timedelta(minutes=self.cfg["flatten_minutes_before_close"])
        self.D = self.load_day()
        return None

    # ---------- screen
    def screen(self):
        cfg, api = self.cfg, self.api
        now = self.clock.now()
        self.D["status"] = "screening"; self.save()
        syms = set(cfg["fallback_universe"])
        src = []
        for name, f in (("most-actives", lambda: api.most_actives(100)), ("movers", lambda: api.movers(50))):
            try:
                got = f(); syms |= set(got); src.append(f"{name}:{len(got)}")
            except Exception as e:
                self.log(f"screener {name} unavailable: {e}")
        syms = sorted(s for s in syms if SYM_RE.match(s))
        snaps = api.snapshots(syms, "iex")
        elapsed = 1.0 if (self.test and now >= self.close_t) else max((min(now, self.close_t) - self.open_t).total_seconds() / 60, 5) / 390
        rows = []
        for s in syms:
            sn = snaps.get(s) or {}
            db, pdb, lt = sn.get("dailyBar"), sn.get("prevDailyBar"), sn.get("latestTrade")
            if not (db and pdb and lt) or not db.get("t", "").startswith(self.day): continue
            if not pdb.get("c") or not pdb.get("v"): continue
            px = lt["p"]; gap = db["o"] / pdb["c"] - 1
            rvol = db["v"] / (pdb["v"] * elapsed)
            rows.append({"sym": s, "price": px, "gap": gap, "rvol": rvol, "chg": px / pdb["c"] - 1})
        # 20-day average dollar volume from SIP daily bars (up to yesterday)
        if rows:
            daily = api.bars([r["sym"] for r in rows], "1Day", self.day_dt - timedelta(days=45), self.open_t - timedelta(hours=9), "sip")
            for r in rows:
                d = daily.get(r["sym"], [])[-20:]
                r["adv"] = sum(b[4] * b[5] for b in d) / len(d) if d else 0
        ok = [r for r in rows if cfg["min_price"] <= r["price"] <= cfg["max_price"] and r["adv"] >= cfg["min_avg_dollar_volume"]
              and abs(r["gap"]) * 100 >= cfg["min_abs_gap_pct"] and r["rvol"] >= cfg["min_rvol"]]
        for r in ok: r["score"] = abs(r["gap"]) * 100 * min(r["rvol"], 10)
        ok.sort(key=lambda r: -r["score"])
        cands = ok[: cfg["candidates_to_test"]]
        self.log(f"universe {len(syms)} ({', '.join(src) or 'fallback list'}) -> snapshots {len(rows)} -> passed filters {len(ok)}")

        # backtest every strategy on each candidate's recent history (prior days only)
        hist = self.history([r["sym"] for r in cands])
        o = self.bt_opts()
        for r in cands:
            x = Bars(hist.get(r["sym"], []))
            r["bars"] = x.n; res = []
            if x.n < 200: r["best"] = None; r["tests"] = []; continue
            for sid in cfg["strategies"]:
                m = backtest(x, signals(x, sid, None, cfg["allow_shorts"]), o)["m"]
                pf = m["pf"]; pf = 99 if pf == math.inf else (pf if math.isfinite(pf) else 0)
                elig = (m["n"] >= cfg["min_trades"] and m["tot"] > 0 and m["sharpe"] >= cfg["min_sharpe"] and pf >= cfg["min_profit_factor"])
                res.append({"id": sid, "name": STRATS[sid][0], "ok": elig, **{k: m[k] for k in ("tot", "sharpe", "dd", "n", "win", "pf")}})
            res.sort(key=lambda t: -t["sharpe"])
            r["tests"] = res
            best = next((t for t in res if t["ok"]), None)
            r["best"] = best
        picks = sorted([r for r in cands if r.get("best")], key=lambda r: -r["best"]["sharpe"])[: cfg["max_picks"]]
        self.D["screen"] = {"time": now.strftime("%H:%M"), "universe": len(syms), "passed": len(ok), "candidates": cands,
                            "settings": {k: cfg[k] for k in ("min_abs_gap_pct", "min_rvol", "min_price", "min_avg_dollar_volume", "lookback_days", "allow_shorts", "stop_pct")}}
        self.D["picks"] = [{"sym": r["sym"], "strategy": r["best"]["id"], "strategy_name": r["best"]["name"], "params": STRATS[r["best"]["id"]][2],
                            "gap": r["gap"], "rvol": r["rvol"], "bt": r["best"]} for r in picks]
        self.D["status"] = "screened"
        self.event("screen", passed=len(ok), tested=len(cands), picks=[p["sym"] for p in self.D["picks"]])
        self.save(f"bot: screen {self.day}", force=True)

    def bt_opts(self):
        c = self.cfg
        return {"cap": 25000, "size": 1, "comm": 0, "slip": c["slippage_bps"], "stop": c["stop_pct"] / 100, "tp": 0, "eod": True}

    def history(self, syms):
        if not syms: return {}
        start = self.day_dt - timedelta(days=int(self.cfg["lookback_days"] * 1.6) + 3)
        raw = self.api.bars(syms, self.tf, start, self.open_t, "sip")
        return {s: [b for b in rows if 570 <= et_minute(b[0]) < 960] for s, rows in raw.items()}

    # ---------- trading
    def trade(self):
        picks = self.D.get("picks") or []
        if not picks:
            self.D["status"] = "no picks"; self.event("idle", reason="no stock passed the screen and strategy tests")
            return self.eod()
        syms = [p["sym"] for p in picks]
        hist = self.history(syms)
        acct = self.api.account()
        base = float(acct.get("last_equity") or acct["equity"])
        alloc = base * self.cfg["capital_fraction"] / len(picks)
        self.D["status"] = "trading"; self.D["alloc_per_pick"] = alloc; self.save("bot: trading started", force=True)
        stop = self.cfg["stop_pct"] / 100
        deadline = min(self.flatten_t, self.started + timedelta(minutes=MAX_RUN_MIN))
        no_new = self.flatten_t - timedelta(minutes=10)
        while True:
            now = self.clock.now()
            nxt = datetime.fromtimestamp((math.floor(now.timestamp() * 1000 / self.tfms) + 1) * self.tfms / 1000, ET) + timedelta(seconds=20)
            if nxt >= deadline:
                self.clock.sleep((deadline - now).total_seconds()); break
            self.clock.sleep((nxt - now).total_seconds())
            try:
                self.tick(picks, hist, alloc, stop, no_new)
            except Exception as e:
                self.event("error", msg=str(e)[:300])
            self.save("bot: trading update")
        if self.clock.now() >= self.flatten_t - timedelta(seconds=30):
            self.eod()
        else:
            self.D["status"] = "handover"; self.event("handover", reason="job time limit; next scheduled run resumes")
            self.save("bot: handover", force=True)

    def tick(self, picks, hist, alloc, stop, no_new):
        now = self.clock.now(); nowms = now.timestamp() * 1000
        syms = [p["sym"] for p in picks]
        today = self.api.bars(syms, self.tf, self.open_t, now, "iex")
        pos = self.api.positions()
        pending = {o["symbol"] for o in self.api.open_orders()}
        blocked = self.D.setdefault("blocked", {})
        snap = []
        for p in picks:
            s = p["sym"]
            tb = [b for b in today.get(s, []) if b[0] + self.tfms <= nowms]
            cur = pos.get(s); q = int(float(cur["qty"])) if cur else 0
            if cur: snap.append({"sym": s, "qty": q, "entry": float(cur["avg_entry_price"]), "price": float(cur["current_price"]), "pl_pct": float(cur["unrealized_plpc"])})
            if s in pending or not tb: continue
            x = Bars(hist.get(s, []) + tb)
            tg = signals(x, p["strategy"], p["params"], self.cfg["allow_shorts"])[-1]
            if s in blocked:
                if tg != blocked[s]: del blocked[s]
                else: tg = 0
            side = (q > 0) - (q < 0)
            if q and stop > 0 and float(cur["unrealized_plpc"]) <= -stop:
                self.api.close_position(s); blocked[s] = side
                self.event("exit", sym=s, qty=q, reason="stop", price=float(cur["current_price"]), pl_pct=float(cur["unrealized_plpc"])); continue
            if tg == side: continue
            if q:
                self.api.close_position(s)
                self.event("exit", sym=s, qty=q, reason="signal", price=float(cur["current_price"]), pl_pct=float(cur["unrealized_plpc"]))
            elif tg != 0 and now < no_new:
                px = tb[-1][4]; n = math.floor(alloc / px)
                if n >= 1:
                    self.api.submit(s, n, "buy" if tg > 0 else "sell", f"tl-{self.day}-{s}-{int(nowms) // 1000}")
                    self.event("entry", sym=s, qty=n * tg, price=px, strategy=p["strategy"])
        self.D["positions"] = snap

    def eod(self):
        syms = [p["sym"] for p in self.D.get("picks") or []]
        if syms:
            for o in self.api.open_orders():
                if o["symbol"] in syms: self.api.cancel_order(o["id"])
            pos = self.api.positions()
            for s in syms:
                if s in pos:
                    self.api.close_position(s); self.event("exit", sym=s, qty=int(float(pos[s]["qty"])), reason="end of day",
                                                           price=float(pos[s]["current_price"]), pl_pct=float(pos[s]["unrealized_plpc"]))
            self.clock.sleep(30)
        acct = self.api.account()
        eq, le = float(acct["equity"]), float(acct.get("last_equity") or acct["equity"])
        pnl = {}
        try:
            for f in self.api.fills(self.day) or []:
                s = f["symbol"]
                if s not in syms: continue
                amt = float(f["qty"]) * float(f["price"])
                pnl[s] = pnl.get(s, 0) + (amt if f["side"].startswith("sell") else -amt)
        except Exception as e:
            self.log(f"fills unavailable: {e}")
        self.D["positions"] = []
        self.D["eod"] = {"equity": eq, "prev_equity": le, "day_pl": eq - le, "day_pl_pct": eq / le - 1 if le else 0, "pl_by_symbol": pnl}
        self.D["status"] = "closed"
        self.event("close", equity=round(eq, 2), day_pl=round(eq - le, 2))
        f = self.data / "equity.csv"
        lines = f.read_text().splitlines() if f.exists() else ["date,equity,day_pl,picks"]
        lines = [l for l in lines if not l.startswith(self.day + ",")] + [f"{self.day},{eq:.2f},{eq - le:.2f},{' '.join(syms)}"]
        f.write_text("\n".join(lines) + "\n")
        self.save(f"bot: close {self.day}", force=True)

    # ---------- entry point
    def run(self, test=False):
        why = self.setup(test)
        if why:
            self.log(why); return why
        if test:
            self.D = {"date": self.day, "mode": "test", "events": [], "blocked": {}}
            self.screen(); self.D["status"] = "test complete"; self.save("bot: test screen", force=True)
            return "test done"
        now = self.clock.now()
        if self.D.get("eod"):
            self.log("Already closed for today"); return "already closed"
        if now < self.at("09:00") or now >= self.flatten_t:
            self.log("Outside trading window"); return "outside window"
        if not self.D.get("screen"):
            st = self.at(self.cfg["screen_time_et"])
            if now < st: self.clock.sleep((st - now).total_seconds())
            self.screen()
        else:
            self.event("resume", picks=[p["sym"] for p in self.D.get("picks") or []])
        self.trade()
        return self.D.get("status")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="screen the latest session; no orders")
    a = ap.parse_args()
    key, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not sec:
        sys.exit("Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY (add them as GitHub repo secrets)")
    cfg = json.loads((ROOT / "bot" / "config.json").read_text())
    bot = Bot(Alpaca(key, sec), RealClock(), cfg, commit=bool(os.environ.get("GITHUB_ACTIONS")))
    print(bot.run(test=a.test))


if __name__ == "__main__":
    main()
