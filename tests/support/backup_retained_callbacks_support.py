"""Shared backup retained callbacks fixtures and assertions; no collected scenarios."""
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from kanamemory.navigation import PageState
from support.fake_ui import View
from support.fake_ui import descendants


def controls(app):
    return {node.text: node for node in descendants(app.body) if node.on_press}


def wire(app):
    for name in ('export_learning_backup', 'export_previous_backup', 'import_learning_backup',
                 'convert_legacy_learning_records'):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    for name in ('show_learning_records', 'show_progress', 'delete_learning_data', 'show_free_ink',
                 'install_tts_data', 'refresh_services', 'open_tts_settings', 'speak_chinese',
                 'show_notices', 'show_learning_settings', 'show_study_path_add', 'show_practice_setup'):
        if not hasattr(app, name):
            setattr(app, name, Mock())
    app.service_message = 'ready'
    app.mixed_label = lambda *args, **kwargs: View(text=str(args))
    app.speak_text = Mock()
    app.platform.chinese_voice_status = lambda lang: {'detail': 'available'}
    app._navigating, app._view_key = False, None
    app._capture_page = lambda: PageState((app.page, None), app.page, '', app.body)
    app._sync_back_handler = Mock()
