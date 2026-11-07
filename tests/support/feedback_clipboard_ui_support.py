"""Shared feedback clipboard ui fixtures and assertions; no collected scenarios."""



def clipboard(app):
    control = app._feedback_export_controls["format"]
    control.value = "复制到剪贴板"
    control.on_change(control)
