"""Probe: enumerate DeepSeek model ids available to this key (G4 one-off)."""
import httpx

from genomeqc_agent import config

config.load_dotenv()
env = config.model_env()
c = httpx.Client(headers={"Authorization": f"Bearer {env['api_key']}"}, timeout=30)
r = c.get(f"{env['api_base']}/models")
models = [m["id"] for m in r.json().get("data", [])]
ds = [m for m in models if "deepseek" in m.lower()]
print("deepseek models:", ds)
