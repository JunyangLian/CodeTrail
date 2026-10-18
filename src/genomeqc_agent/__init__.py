"""GenomeQC Agent (AssemblyGenomics-Copilot V3).

Workflow-first, agent-assisted organelle genome QC.
Rules produce facts; the LLM only plans and explains; humans keep approval
over high-risk actions. This package never imports V2 code at runtime - it
uses the hash-anchored snapshot in adapters/v2_snapshot.
"""
__version__ = "0.1.0"
