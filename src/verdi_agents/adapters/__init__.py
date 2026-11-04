"""Optional third-party simulation-framework adapters.

These are OFF by default and lazily imported: importing this package never pulls
in an optional dependency (e.g. ``gdm-concordia``) and never affects Verdi's
deterministic engine. See :mod:`verdi_agents.adapters.concordia` for the Concordia
bridge and ``docs/concordia_eval.md`` for the integration assessment.
"""
