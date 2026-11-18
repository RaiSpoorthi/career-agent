"""Started by Windows Task Scheduler (README: "Run it automatically"): one agent run with no window.

The browser stays hidden and everything the run prints goes to data/scheduled.log.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "data" / "scheduled.log"
MAX_LOG_BYTES = 1_000_000

os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CAREER_AGENT_HEADLESS", "1")
LOG.parent.mkdir(exist_ok=True)
if LOG.exists() and LOG.stat().st_size > MAX_LOG_BYTES:  # keep only the recent half
    LOG.write_bytes(LOG.read_bytes()[-MAX_LOG_BYTES // 2:])

with open(LOG, "a", encoding="utf-8", buffering=1) as log:
    sys.stdout = sys.stderr = log
    print(f"\n===== {datetime.now():%Y-%m-%d %H:%M} scheduled run =====")
    try:
        import jobs

        # With cloud runs set up (.state/), the cloud applies; this computer only syncs, so the two never
        # apply to the same job twice.
        default = ["sync"] if (ROOT / ".state" / ".git").exists() else ["run"]
        code = jobs.main(sys.argv[1:] or default)  # a command can be given for testing, e.g. digest
    except BaseException as exc:  # never fail silently: the reason is in the log
        print(f"Run failed: {type(exc).__name__}: {exc}")
        code = 1
    print(f"===== finished {datetime.now():%H:%M} (exit {code}) =====")
sys.exit(code)
