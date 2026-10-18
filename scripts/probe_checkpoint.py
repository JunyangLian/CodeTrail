"""Probe: checkpoint deserialization structure (one-off)."""
import sqlite3

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

s = JsonPlusSerializer()
conn = sqlite3.connect(r"data\eval\g4_ds_chloro40\eval_checkpoint.sqlite3")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)
row = conn.execute("SELECT checkpoint FROM checkpoints LIMIT 1").fetchone()
d = s.loads(row[0])
print("type:", type(d).__name__)
if isinstance(d, dict):
    print("keys:", list(d.keys())[:10])
    cv = d.get("channel_values")
    print("cv type:", type(cv).__name__)
    if isinstance(cv, dict):
        print("cv keys:", list(cv.keys())[:10])
    elif isinstance(cv, tuple):
        print("cv tuple len:", len(cv), "first:", repr(cv[0])[:80])
