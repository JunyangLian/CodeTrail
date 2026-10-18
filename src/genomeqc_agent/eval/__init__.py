"""Evaluation layer (G5): report-to-contract mapping and eval plumbing.

The mapping here is pure data reshaping. It NEVER imports the V2 oracle or
any private gold - grading happens only in the isolated worker process
(scripts/eval_grader_worker.py, run under V2's own interpreter).
"""
