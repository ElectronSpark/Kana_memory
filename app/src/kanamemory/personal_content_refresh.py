"""Rebuild only read-only teaching views after personal content changes."""
from .personal_content_versions import GENERATION


def generation(app):
    getter = getattr(getattr(app, 'store', None), 'get_setting', None)
    return getter(GENERATION, 0) if callable(getter) else 0


def refresh_return(app, previous):
    if generation(app) == previous.content_generation: return False
    name = previous.name; identity = previous.key[1] if len(previous.key) > 1 else None
    callback = None
    if name == 'example_detail':
        state = previous.bindings.get('_example_detail_state', (identity, None))
        callback = lambda: app._render_example_detail(*state)
    elif name == 'lexical_example_detail' and identity:
        callback = lambda: app.show_lexical_example_detail(identity)
    elif name == 'word_detail' and identity:
        callback = lambda: app.show_word_detail(identity)
    elif name == 'learning_detail' and identity:
        item = next((r for r in app.learning_items_available() if r['id'] == identity), None)
        if item: callback = lambda: app.show_learning_detail(item)
    elif name in {'item_examples', 'example_library', 'lexical_example_library'}:
        field, method = {'item_examples': ('_item_examples_state', '_render_item_examples'),
            'example_library': ('_example_library_state', '_render_example_library'),
            'lexical_example_library': ('_lexical_example_library_state', '_render_lexical_example_library')}[name]
        state = previous.bindings.get(field)
        if state is not None: callback = lambda: getattr(app, method)(*state)
    if callback is None: return False
    navigating = app._navigating
    app._navigating = True
    try: callback()
    finally: app._navigating = navigating
    app._view_key = previous.key
    app._sync_back_handler()
    scroll = getattr(app, '_page_scroll', None); page_token = app._page_token
    if scroll:
        def position():
            if app._page_token == page_token: scroll.position = previous.position
        app.loop.call_soon(position)
    return True
