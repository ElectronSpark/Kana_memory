"""Back-stack behavior with retained Python views; no Toga/native dependency."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
from kanamemory.navigation import BackStack,PageState,page_view


def page(name,key=None,**bindings):
    return PageState(key or (name,None),name,name,object(),bindings)


class FakeApp:
    """A builder replaces widgets just as a native form does during navigation."""
    def __init__(self,limit=24):
        self.back_stack=BackStack(limit)
        self._navigating=False
        self._page_token=0
        self._view_key=None
        self.page=None
        self.title=""
        self.content=None
        self.scroll=None
        self.capture_log=[]
        self.sync_log=[]
        self.word_query=SimpleNamespace(value="")
        self.word_filter=SimpleNamespace(value="全部词汇")
        self.word_offset=0

    def _capture_page(self):
        if self._view_key is None:
            self.capture_log.append(None)
            return None
        bindings={}
        if self.page=="words":
            bindings={"word_query":self.word_query,"word_filter":self.word_filter,"word_offset":self.word_offset}
        state=PageState(self._view_key,self.page,self.title,self.content,bindings,self.scroll,
                        tuple(self.scroll.position) if self.scroll else (0,0))
        self.capture_log.append(state)
        return state

    def _sync_back_handler(self):
        self.sync_log.append(bool(self.back_stack.pages))

    def screen(self,name,title=None):
        self.page=name
        self.title=title or name
        self.content=object()
        self.scroll=SimpleNamespace(position=(0,0))
        self._page_token+=1

    def back(self):
        state=self.back_stack.pop()
        if state is None:
            return False
        self.page,self.title,self.content=state.name,state.title,state.content
        self._view_key=state.key
        self.scroll=state.scroll
        self._page_token+=1
        for key,value in state.bindings.items():
            setattr(self,key,value)
        if self.scroll:
            self.scroll.position=state.position
        self._sync_back_handler()
        return True

    @page_view()
    def show_home(self):
        self.screen("home")

    @page_view()
    def show_words(self):
        self.word_query=SimpleNamespace(value="")
        self.word_filter=SimpleNamespace(value="全部词汇")
        self.word_offset=0
        self.screen("words")

    @page_view(detail=True)
    def show_detail(self,record_id):
        self.screen("detail",record_id)

    @page_view(detail=True)
    def show_source(self,source):
        self.screen("source",source["title"])

    @page_view()
    def show_question(self):
        self.screen("question")

    @page_view()
    def show_feedback(self):
        self.screen("feedback")

    @page_view(replace=True)
    def advance_question(self):
        self.screen("question","next question")

    @page_view()
    def outer_builder(self):
        self.show_detail("nested-record")
        return "built once"

    @page_view()
    def no_render(self):
        return "validation declined"

    @page_view()
    def failed_builder(self):
        raise RuntimeError("data unavailable before rendering")


def test_empty_stack_replacement_same_key_and_minimum_bound():
    with pytest.raises(ValueError):
        BackStack(limit=1)
    stack=BackStack()
    assert stack.pop() is None
    root=page("home")
    stack.remember(None,("words",None))
    stack.remember(root,root.key)
    stack.remember(root,("words",None),replace=True)
    assert stack.pages==[]
    stack.remember(root,("words",None))
    assert stack.pop() is root
    assert stack.pop() is None


def test_bound_retains_root_and_latest_history_in_order():
    stack=BackStack(limit=3)
    states=[page("home")]+[page(f"detail-{i}") for i in range(12)]
    for i,state in enumerate(states):
        stack.remember(state,(f"next-{i}",None))
        assert len(stack.pages)<=3
    assert stack.pop() is states[-1]
    assert stack.pop() is states[-2]
    assert stack.pop() is states[0]
    assert stack.pop() is None


def test_detail_record_ids_distinguish_pages_but_same_record_does_not_duplicate():
    app=FakeApp()
    app.show_home()
    app.show_detail("kana-3042")
    app.show_detail("kana-3044")
    assert app._view_key==("show_detail","kana-3044")
    assert [s.key for s in app.back_stack.pages]==[("show_home",None),("show_detail","kana-3042")]
    app.show_detail("kana-3044")
    assert len(app.back_stack.pages)==2
    assert app.back()
    assert app._view_key==("show_detail","kana-3042")


def test_dictionary_detail_identity_uses_source_id_before_title():
    app=FakeApp()
    app.show_home()
    app.show_source({"id":"source-a","title":"Shared title"})
    app.show_source({"id":"source-b","title":"Shared title"})
    assert app._view_key==("show_source","source-b")
    app.show_source({"id":"source-b","title":"Updated title"})
    assert len(app.back_stack.pages)==2
    assert app.back()
    assert app._view_key==("show_source","source-a")


def test_keyword_detail_argument_preserves_the_same_navigation_identity():
    app=FakeApp()
    app.show_home()
    app.show_detail(record_id="kana-3042")
    assert app._view_key==("show_detail","kana-3042")
    app.show_detail("kana-3042")
    assert len(app.back_stack.pages)==1


def test_self_detail_keeps_existing_view_and_scroll_without_rerendering():
    app=FakeApp()
    app.show_home()
    app.show_detail("kana-3042")
    original, token = app.content, app._page_token
    app.scroll.position = (0, 300)
    app.show_detail("kana-3042")
    assert app.content is original and app._page_token == token
    assert app.scroll.position == (0, 300)
    assert len(app.back_stack.pages) == 1


def test_mutable_detail_refreshes_without_adding_old_workflow_states_to_back():
    class Workflow(FakeApp):
        @page_view(detail=True, refresh=True)
        def show_material(self, identity, *, offset=0):
            self.screen('material', str(offset))
    app=Workflow();app.show_home();app.show_material('one')
    old=app.content;app.show_material('one',offset=12)
    assert app.content is not old and app.title=='12'
    assert len(app.back_stack.pages)==1
    assert app.back() and app.page=='home'


def test_pending_render_identity_exists_during_builder_and_clears_on_failure():
    class Builder(FakeApp):
        @page_view(detail=True)
        def inspect_detail(self, record_id):
            assert self._render_key == ("inspect_detail", record_id)
            assert self._view_key == ("show_home", None)
            raise RuntimeError("Cannot load")
    app=Builder()
    app.show_home()
    with pytest.raises(RuntimeError, match="Cannot load"):
        app.inspect_detail("kana-3042")
    assert app._render_key is None and not app._navigating
    assert app._view_key == ("show_home", None)
    assert not app.back_stack.pages


def test_captures_form_bindings_before_builder_overwrites_them():
    app=FakeApp()
    app.show_home()
    app.show_words()
    query,filters,view=app.word_query,app.word_filter,app.content
    query.value="ティ"
    filters.value="英语借词候选"
    app.word_offset=60
    app.scroll.position=(0,480)
    app.show_detail("jmdict:1134480")
    old_words=app.back_stack.pages[-1]
    assert old_words.bindings["word_query"] is query
    assert old_words.bindings["word_filter"] is filters
    assert old_words.bindings["word_offset"]==60
    assert old_words.content is view
    assert old_words.position==(0,480)
    # A separate word list creates new bindings. Returning through the detail
    # page must still recover the original list's edited widgets by identity.
    app.show_words()
    assert app.word_query is not query
    app.word_query.value="別の検索"
    assert app.back() and app.page=="detail"
    assert app.back() and app.page=="words"
    assert app.word_query is query and app.word_query.value=="ティ"
    assert app.word_filter is filters and app.word_filter.value=="英语借词候选"
    assert app.word_offset==60
    assert app.content is view and app.scroll.position==(0,480)


def test_rebuilding_same_page_captures_old_bindings_but_does_not_push_duplicate():
    app=FakeApp()
    app.show_home()
    app.show_words()
    old_widget=app.word_query
    old_widget.value="ねこ"
    size=len(app.back_stack.pages)
    app.show_words()
    assert app.capture_log[-1].bindings["word_query"] is old_widget
    assert app.word_query is not old_widget
    assert len(app.back_stack.pages)==size


def test_nested_builder_is_one_transition_and_retains_outer_identity():
    app=FakeApp()
    app.show_home()
    captured=len(app.capture_log)
    token=app._page_token
    assert app.outer_builder()=="built once"
    assert len(app.capture_log)==captured+1
    assert app._page_token==token+1
    assert app._view_key==("outer_builder",None)
    assert app.page=="detail"
    assert len(app.back_stack.pages)==1
    assert not app._navigating


def test_no_render_does_not_record_a_navigation():
    app=FakeApp()
    app.show_home()
    token,key=app._page_token,app._view_key
    assert app.no_render()=="validation declined"
    assert (app._page_token,app._view_key)==(token,key)
    assert app.back_stack.pages==[]
    assert not app._navigating


def test_failed_builder_does_not_leak_nested_guard_or_add_phantom_history():
    app=FakeApp()
    app.show_home()
    key=app._view_key
    with pytest.raises(RuntimeError,match="data unavailable"):
        app.failed_builder()
    assert not app._navigating
    assert app._view_key==key
    assert app.back_stack.pages==[]
    app.show_detail("after-error")
    assert app.back_stack.pages[0].key==key
    assert app._view_key==("show_detail","after-error")


def test_discard_retires_review_views_without_losing_nonpractice_history():
    stack=BackStack()
    names=["home","words","question","feedback","settings","ink_question","word_question","session_done"]
    original=[page(name) for name in names]
    for i,state in enumerate(original):
        stack.remember(state,(f"next-{i}",None))
    stack.discard({"question","word_question","ink_question","feedback","session_done"})
    assert [s.name for s in stack.pages]==["home","words","settings"]
    assert stack.pop() is original[4]
    assert stack.pop() is original[1]
    assert stack.pop() is original[0]
    assert stack.pop() is None


def test_review_advance_replacement_never_reintroduces_the_retired_feedback():
    app=FakeApp()
    app.show_home()
    app.show_words()
    app.show_question()
    app.show_feedback()
    assert app.page=="feedback"
    app.back_stack.discard({"question","feedback"})
    app.advance_question()
    assert all(s.name not in {"question","feedback"} for s in app.back_stack.pages)
    assert app.back() and app.page=="words"
    assert app.back() and app.page=="home"
    assert not app.back()


def test_back_handler_sync_sees_committed_history_only():
    app=FakeApp()
    app.show_home()
    assert app.sync_log==[False]
    app.show_words()
    assert app.sync_log[-1] is True
    app.no_render()
    assert app.sync_log==[False,True]
    assert app.back()
    assert app.sync_log[-1] is False
