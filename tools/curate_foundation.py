"""Compatibility entry point for private, separately versioned content authoring.

This is a local maintenance command, not part of the application content pack.
Its implementation remains outside the clean source distribution.
"""
from pathlib import Path as _AuthoringPath

_authoring_source = (_AuthoringPath(__file__).resolve().parents[1]
                     / "data/content_authoring/tools/curate_foundation.py")
if not _authoring_source.is_file():
    raise FileNotFoundError("This command requires the separate private content-authoring package.")
# Keep __file__ at this compatibility entry point so the original repository
# relative inputs, output locations, and imported helper modules stay stable.
exec(compile(_authoring_source.read_text(encoding="utf-8-sig"),
             str(_authoring_source), "exec"), globals())
