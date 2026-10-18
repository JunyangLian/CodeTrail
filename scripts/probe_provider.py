"""Diagnostic: inspect raw provider payload shape (G4 one-off probe)."""

import httpx

from genomeqc_agent import config

config.load_dotenv()
env = config.model_env()
c = httpx.Client(headers={"Authorization": f"Bearer {env['api_key']}"}, timeout=120)
r = c.post(f"{env['api_base']}/chat/completions", json={
    "model": env["model_id"],
    "messages": [
        {"role": "system",
         "content": 'Respond with STRICT JSON only: {"kind": "tool"|"synthesize",'
                    ' "tool_name": "...", "arguments": {}, "reason": "..."}'},
        {"role": "user",
         "content": "Facts show coordinate_violations=[] and all statistics equal. "
                    "verify_evidence_pointer already ran. What is the next action?"},
    ],
    "temperature": 0, "max_tokens": 512, "enable_thinking": False})
d = r.json()
choice = d["choices"][0]
msg = choice["message"]
print("finish_reason:", choice.get("finish_reason"))
print("content head:", repr(msg.get("content"))[:200])
print("reasoning head:", repr(msg.get("reasoning_content"))[:120])
print("usage:", d.get("usage"))
