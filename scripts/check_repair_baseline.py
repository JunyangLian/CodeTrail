"""Check original v1 gold and archived runtime without claiming current code is v1."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pico.evaluation.baseline_assets import verify_baseline_assets
if __name__=='__main__':print(json.dumps(verify_baseline_assets(),ensure_ascii=False))
