"""Words and grammar alternate on the core main route (design 11 section 16, M1-M10).

Layers: params, readiness, sections, placement and plan are pure rules (no Toga,
database, clock or network); sources and store are the adapters that read the
teaching content and keep the plan file; service holds one app's MainRouteMix.
"""
