"""Run several simulated trading days in a row to exercise the bot's live-learning loop."""
import json, sys, tempfile, shutil
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import mock_day as M
import run as R
from engine import ET

root = Path(tempfile.mkdtemp())
cfg = json.loads((Path(__file__).resolve().parent.parent / "bot" / "config.json").read_text())
cfg["fallback_universe"] = []
cfg["learn_drop_after"] = 6
for day in ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"]:
    M.DAY = day
    clock = M.FakeClock(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ET, hour=9, minute=35))
    api = M.FakeAlpaca(clock)
    R.Bot(api, clock, cfg, root=root, log=lambda m: None).run()
    R.Bot(api, clock, cfg, root=root, log=lambda m: None).run()
    D = json.loads((root / "data" / "latest.json").read_text())
    L = D.get("learning", {})
    print(day, D["status"], "picks", [(p["sym"], p["strategy"]) for p in D["picks"]], "| live trades so far", L.get("trades"), "| dropped", L.get("dropped"), "| logged today", D.get("trades_logged"))
print((root / "data" / "trades.csv").read_text().splitlines()[:4])
print("rows", len((root / "data" / "trades.csv").read_text().splitlines()) - 1)
shutil.copytree(root / "data", "/home/claude/site/data", dirs_exist_ok=True)
