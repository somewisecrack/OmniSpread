import sys
from backend.engine import OmniSpreadEngine
from backend.main import PRESETS

tickers = PRESETS["nifty_fno"]
engine = OmniSpreadEngine(tickers=tickers, period="1y", interval="1d", top_n=50)

results = engine.run_scan()
print(f"Found {len(results)} pairs")
for r in results:
    print(r['pair'], r['prob_profit'])

