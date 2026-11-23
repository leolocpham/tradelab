"""Simulate a full trading day against a fake Alpaca (fake clock, fake data,
fake broker) to exercise the bot end to end without network access."""
import json, math, random, shutil, sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
from engine import ET
import run as R

DAY = "2026-11-24"


class FakeClock:
    def __init__(self, t): self.t = t
    def now(self): return self.t
    def sleep(self, s): self.t += timedelta(seconds=max(0, s))


def gen_symbol(rng, sym, days, gap):
    """5-min RTH bars for prior days plus today; returns rows and prev-day stats."""
    rows = []; p = rng.uniform(20, 300); drift = 0
    for di, d in enumerate(days):
        base = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=ET, hour=9, minute=30)
        if d == DAY: p *= 1 + gap
        else: p *= 1 + rng.gauss(0, 0.01)
        if rng.random() < 0.2: drift = rng.gauss(0, 0.003)
        for k in range(78):
            t = int((base + timedelta(minutes=5 * k)).timestamp() * 1000)
            o = p; c = o * math.exp(drift / 78 + rng.gauss(0, 0.004)); h = max(o, c) * (1 + abs(rng.gauss(0, 0.001))); l = min(o, c) * (1 - abs(rng.gauss(0, 0.001)))
            rows.append([t, o, h, l, c, rng.randint(20000, 90000)]); p = c
    return rows


class FakeAlpaca:
    def __init__(self, clock):
        self.clock = clock; rng = random.Random(7)
        d = datetime.strptime(DAY, "%Y-%m-%d"); days = []
        while len(days) < 45:
            if d.weekday() < 5: days.insert(0, d.strftime("%Y-%m-%d"))
            d -= timedelta(days=1)
        self.days = days
        self.syms = [f"S{chr(65+i)}{chr(65+j)}" for i in range(4) for j in range(10)]
        self.rows = {s: gen_symbol(rng, s, days, rng.choice([0, 0, 0.04, -0.05, 0.06, 0.03])) for s in self.syms}
        self.pos = {}; self.orders = []; self.cash = 100000.0; self.fill_log = []; self.calls = 0

    def _upto(self, s, end_ms): return [b for b in self.rows[s] if b[0] <= end_ms]
    def _px(self, s):
        nowms = self.clock.now().timestamp() * 1000
        r = [b for b in self.rows[s] if b[0] + 300000 <= nowms]
        return r[-1][4]

    def clock(self): return {"is_open": True}
    def calendar(self, a, b): return [{"date": d, "open": "09:30", "close": "16:00"} for d in self.days if a <= d <= b]
    def account(self):
        eq = self.cash + sum(q * self._px(s) for s, q in self.pos.items())
        return {"equity": str(eq), "last_equity": "100000"}
    def positions(self):
        out = {}
        for s, (q) in self.pos.items():
            if q == 0: continue
            px = self._px(s); e = self.entry[s]
            out[s] = {"symbol": s, "qty": str(q), "avg_entry_price": str(e), "current_price": str(px), "unrealized_plpc": str((px / e - 1) * (1 if q > 0 else -1))}
        return out
    def open_orders(self): return []
    def cancel_order(self, oid): pass
    def close_position(self, s):
        q = self.pos.get(s, 0); px = self._px(s); self.cash += q * px
        self.fill_log.append({"symbol": s, "qty": str(abs(q)), "price": str(px), "side": "sell" if q > 0 else "buy"}); self.pos[s] = 0
    def submit(self, s, qty, side, cid):
        px = self._px(s); q = qty if side == "buy" else -qty
        self.pos[s] = self.pos.get(s, 0) + q; self.cash -= q * px
        self.entry = getattr(self, "entry", {}); self.entry[s] = px
        self.fill_log.append({"symbol": s, "qty": str(qty), "price": str(px), "side": side})
    def fills(self, day): return self.fill_log
    def assets(self): return [{"symbol": s, "name": f"{s} Holdings Inc", "tradable": True} for s in self.syms[:-2]] + [{"symbol": s, "name": f"Tradr 2X Long {s} Daily ETF", "tradable": True} for s in self.syms[-2:]]
    def most_actives(self, top=100): return self.syms[:20]
    def movers(self, top=50): raise RuntimeError("403 not in plan (simulated)")
    def snapshots(self, syms, feed="iex"):
        nowms = self.clock.now().timestamp() * 1000; out = {}
        for s in syms:
            if s not in self.rows: continue
            r = self.rows[s]; td = [b for b in r if datetime.fromtimestamp(b[0]/1000, ET).strftime("%Y-%m-%d") == DAY and b[0] <= nowms]
            pd = [b for b in r if datetime.fromtimestamp(b[0]/1000, ET).strftime("%Y-%m-%d") == self.days[-2]]
            if not td: continue
            out[s] = {"latestTrade": {"p": td[-1][4]}, "dailyBar": {"t": DAY + "T05:00:00Z", "o": td[0][1], "v": sum(b[5] for b in td) * 3},
                      "prevDailyBar": {"c": pd[-1][4], "v": sum(b[5] for b in pd)}}
        return out
    def bars(self, syms, tf, start, end, feed):
        self.calls += 1
        if tf == "1Day":
            out = {}
            for s in syms:
                by = {}
                for b in self.rows[s]:
                    d = datetime.fromtimestamp(b[0]/1000, ET).strftime("%Y-%m-%d")
                    if d < DAY: by.setdefault(d, []).append(b)
                out[s] = [[v[0][0], v[0][1], 0, 0, v[-1][4], sum(x[5] for x in v)] for v in by.values()]
            return out
        a, b = start.timestamp() * 1000, end.timestamp() * 1000
        return {s: [r for r in self.rows[s] if a <= r[0] < b] for s in syms if s in self.rows}


def main():
    root = Path(tempfile.mkdtemp())
    cfg = json.loads((Path(__file__).resolve().parent.parent / "bot" / "config.json").read_text())
    cfg["fallback_universe"] = []
    clock = FakeClock(datetime.strptime(DAY, "%Y-%m-%d").replace(tzinfo=ET, hour=9, minute=35))
    api = FakeAlpaca(clock)
    logs = []
    bot = R.Bot(api, clock, cfg, root=root, log=logs.append)
    print("session A:", bot.run())
    # second scheduled run later in the day must resume and close
    bot2 = R.Bot(api, clock, cfg, root=root, log=logs.append)
    print("session B:", bot2.run())
    bot3 = R.Bot(api, clock, cfg, root=root, log=logs.append)
    print("late run:", bot3.run())
    D = json.loads((root / "data" / "latest.json").read_text())
    print("status", D["status"], "| picks", [(p["sym"], p["strategy"], round(p["bt"]["sharpe"], 2)) for p in D["picks"]])
    kinds = {}
    for e in D["events"]: kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print("events", kinds)
    print("eod", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in D["eod"].items() if k != "pl_by_symbol"})
    print("open positions after close:", {s: q for s, q in api.pos.items() if q})
    print("equity.csv:", (root / "data" / "equity.csv").read_text().strip())
    print("clock end", clock.now().strftime("%H:%M"), "| log tail:", logs[-3:])
    # test mode
    clock.t = clock.t.replace(hour=18)
    bt = R.Bot(api, clock, cfg, root=root, log=logs.append)
    print("test mode:", bt.run(test=True), json.loads((root / "data" / "latest.json").read_text())["mode"])
    shutil.copy(root / "data" / "days" / f"{DAY}.json", "/home/claude/mock_day.json")
    shutil.copy(root / "data" / "equity.csv", "/home/claude/mock_equity.csv")

main()
