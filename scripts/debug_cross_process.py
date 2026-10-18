"""Debug: cross-process resume failure reason (one-off)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(__file__).resolve().parents[1] / "data" / "debug_cp"
TMP.mkdir(parents=True, exist_ok=True)
DB = TMP / "cp.sqlite3"
RUN_ID = "11111111-2222-4333-8444-555555555555"
PACKET = str(ROOT / "examples" / "public_packets" / "chloro_C1_NC_013991_2_CHLORO-CDS-03")

CHILD_A = (
    "import sys, json; "
    f"sys.path.insert(0, r'{ROOT / 'src'}'); "
    "from genomeqc_agent.runtime.graph import QCGraph; "
    f"g = QCGraph(db_path=r'{DB}', interrupt_before=['plan_next_action']); "
    f"state = g.run('{RUN_ID}', 'chloro_C1_NC_013991_2_CHLORO-CDS-03', r'{PACKET}'); "
    "g.close(); "
    "print(json.dumps({'facts_ok': state.get('facts') is not None}))"
)

CHILD_B = (
    "import sys, json; "
    f"sys.path.insert(0, r'{ROOT / 'src'}'); "
    "from genomeqc_agent.runtime.graph import QCGraph; "
    f"g = QCGraph(db_path=r'{DB}', interrupt_before=['plan_next_action']); "
    "reg_err = ''; "
    "try: "
    f"    g.registry.register_packet('chloro_C1_NC_013991_2_CHLORO-CDS-03', r'{PACKET}'); "
    "except Exception as e: "
    "    reg_err = type(e).__name__ + ': ' + str(e); "
    f"state = g.resume('{RUN_ID}'); "
    "g.close(); "
    "print(json.dumps({'reg_err': reg_err[:200], "
    "'status': state['runtime']['status'], "
    "'error': (state['runtime'].get('last_error') or '')[:200]}))"
)

if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "A"
    code = CHILD_A if phase == "A" else CHILD_B
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=180, check=False)
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    print("rc:", proc.returncode)
    print("out:", lines[-1] if lines else proc.stderr[-500:])
