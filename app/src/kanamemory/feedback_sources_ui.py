"""Sources stay behind an explicit report action, separate from learning UI."""
import asyncio

from .feedback_sources import FeedbackSourceStore, SCOPE
from .public_excerpt_fetch import fetch, HOSTS
from .material_workflow import digest
from .diagnostic_events import record_error


def render_sources(app, u, parent, box):
    if not parent['request']['bindings']: return
    view = app._feedback_analysis_view; service = FeedbackSourceStore(app.feedback_analysis())
    panel = u.col(); status = u.label('', size=12, color=u.MUTED)

    def active(): return app.page == 'feedback_analysis' and app._feedback_analysis_view is view

    def expose(): app._feedback_report_exposure(parent['request'])

    def open_panel(widget=None):
        if not active(): return
        try: state = service.recover(parent['id'])
        except Exception: status.text = '资料记录暂不可用。'; return
        panel.clear()
        url = u.toga.TextInput(placeholder='公开资料页面的 HTTPS 地址', style=u.Pack(margin_bottom=6))
        pasted = u.toga.MultilineTextInput(placeholder='也可粘贴资料段落（保持待核）', style=u.Pack(height=110, margin_bottom=6))
        docs = {f"资料 {i + 1} · {'网页' if d['mode'] == 'retrieved' else '粘贴'}": d for i, d in enumerate(state['documents'])}
        document = u.selection(items=list(docs) or ['尚无资料'], style=u.Pack(margin_bottom=6))
        from .feedback_analysis_ui import FIELD_LABELS
        bindings = {f"{i + 1} · {FIELD_LABELS.get(b['field'].split('.')[-1], '内容')}": b for i, b in enumerate(parent['request']['bindings'])}
        field = u.selection(items=list(bindings), style=u.Pack(margin_bottom=6))
        quote = u.toga.MultilineTextInput(placeholder='原文摘录；必须与保存的资料逐字一致', style=u.Pack(height=100, margin_bottom=6))
        start = u.toga.TextInput(placeholder='若摘录重复出现，填写字符起点（从0开始）', style=u.Pack(margin_bottom=6))
        scopes = {key: u.toga.TextInput(placeholder=caption, style=u.Pack(margin_bottom=4)) for key, caption in zip(SCOPE,
            ('适用词形（不确定可留空）', '义项或用法', '时代', '地区', '语域'))}
        relationship = u.selection(items=['独立性不确定', '与原内容同源', '我已核对来源相互独立'], style=u.Pack(margin_bottom=6))
        reason = u.toga.TextInput(placeholder='判定来源独立的理由（仅人工说明）', style=u.Pack(margin_bottom=6))

        def paste(widget=None):
            if not active(): return
            try:
                expose(); service.paste(parent['id'], pasted.value or '')
            except ValueError as exc: status.text = str(exc); return
            except Exception as exc:
                record_error('feedback_sources', 'save', exc)
                status.text = '资料暂未保存。'
                return
            status.text = '段落已保存为待核资料。'
            try: open_panel()
            except Exception as exc:
                record_error('feedback_sources', 'refresh', exc)
                status.text = '段落已保存，但界面暂未刷新；请返回后重新打开。'

        def retrieve(widget=None):
            if not active() or view.get('source_busy'): return
            attempt = None
            try:
                attempt = service.begin_fetch(parent['id'], url.value or '')
                address = next(a['url'] for a in service.get(parent['id'])['attempts'] if a['id'] == attempt)
            except ValueError as exc: status.text = str(exc); return
            except Exception as exc:
                record_error('feedback_sources', 'save', exc)
                if attempt:
                    try: service.finish_fetch(parent['id'], attempt)
                    except Exception as failure:
                        record_error('feedback_sources', 'record_failure', failure)
                status.text = '资料请求已记录，但读取请求失败；请重新打开检查。' if attempt else '资料请求暂未建立。'
                return
            view['source_busy'] = True; status.text = '正在获取公开正文，可返回继续学习。'
            async def run():
                result, error = None, ''
                try:
                    try: result = await asyncio.to_thread(fetch, address)
                    except ValueError as exc: error = str(exc)
                    except Exception as exc:
                        record_error('feedback_sources', 'request', exc)
                        error = '资料获取失败或超时，已保留请求记录。'
                    try:
                        service.finish_fetch(parent['id'], attempt, result)
                    except ValueError as exc:
                        record_error('feedback_sources', 'save', exc)
                        try: service.finish_fetch(parent['id'], attempt)
                        except Exception as failure:
                            record_error('feedback_sources', 'record_failure', failure)
                        error = '资料未通过完整性检查，仍保留待核。'
                    except Exception as exc:
                        record_error('feedback_sources', 'save', exc)
                        try: service.finish_fetch(parent['id'], attempt)
                        except Exception as failure:
                            record_error('feedback_sources', 'record_failure', failure)
                        error = '资料保存未完成，原有资料仍保留；请重新打开检查。'
                    if active():
                        status.text = error or '资料已保存；选择准确摘录后才能交给模型复核。'
                        try: open_panel()
                        except Exception as exc:
                            record_error('feedback_sources', 'refresh', exc)
                            status.text = error or '资料已保存，但界面未刷新；请重新打开检查。'
                except asyncio.CancelledError as exc:
                    record_error('feedback_sources', 'cancel', exc)
                    try: service.finish_fetch(parent['id'], attempt)
                    except Exception as failure:
                        record_error('feedback_sources', 'record_failure', failure)
                    if active(): status.text = '读取已中断，请重新打开检查请求记录；不会自动重试。'
                    raise
                finally: view['source_busy'] = False
            app.loop.create_task(run())

        def add(widget=None):
            if not active(): return
            try:
                expose(); selected_doc = docs.get(document.value)
                if not selected_doc: raise ValueError('请先保存或选择资料。')
                offset = int(start.value) if (start.value or '').strip() else None
                rel = {'独立性不确定': 'unknown', '与原内容同源': 'same_lineage', '我已核对来源相互独立': 'independent_user_asserted'}[relationship.value]
                service.add_excerpt(parent['id'], selected_doc['id'], bindings[field.value]['id'], quote.value or '',
                    {k: v.value or '' for k, v in scopes.items()}, start=offset, relationship=rel, reason=reason.value or '')
            except ValueError as exc: status.text = str(exc); return
            except Exception as exc:
                record_error('feedback_sources', 'save', exc)
                status.text = '摘录暂未保存。'
                return
            status.text = '摘录已关联；获取成功或人工独立性说明均不自动授予评分资格。'
            try: open_panel()
            except Exception as exc:
                record_error('feedback_sources', 'refresh', exc)
                status.text = '摘录已保存，但界面暂未刷新；请返回后重新打开。'

        def show_text(widget=None):
            if not active(): return
            try:
                expose(); d = docs.get(document.value)
                if not d: return
                body = u.col(); offset = [0]
                def more(widget=None):
                    if not active(): return
                    end = min(len(d['text']), offset[0] + 1500)
                    body.add(u.label(f"字符 {offset[0]}—{end}\n" + d['text'][offset[0]:end], size=13))
                    offset[0] = end; button.enabled = end < len(d['text'])
                button = u.button('继续查看正文', more, compact=True); more()
                panel.add(body, button)
            except Exception as exc:
                record_error('feedback_sources', 'reveal', exc)
                status.text = '正文显示未完成，请重试；已记录的内容查看仍会保留。'

        caption = u.label('资料仅供核据，不能因来自某站点或与模型一致而自动认定正确。', size=12, color=u.MUTED)
        caption._feedback_fragment = {'kind': 'feedback_sources', 'parent_id': parent['id'], 'state_sha256': digest(state),
            'documents': [{k: v for k, v in d.items() if k != 'text'} for d in state['documents']],
            'excerpts': state['excerpts'], 'selected': state['selected']}
        panel.add(caption,
            url, u.button('获取网页正文', retrieve, compact=True),
            u.label('支持站点：' + '、'.join(sorted(HOSTS)), size=11, color=u.MUTED),
            pasted, u.button('保存粘贴资料', paste, compact=True), document,
            u.button('查看已保存正文（可能含答案）', show_text, compact=True), field, quote, start,
            *scopes.values(), relationship, reason, u.button('关联这段摘录', add, compact=True))
        chosen = set(state['selected'])
        for i, e in enumerate(state['excerpts']):
            toggle = u.switch(f'复核时使用摘录 {i + 1}', value=e['id'] in chosen)
            def select(widget, identity=e['id'], **kw):
                if not active(): return
                if widget.value: chosen.add(identity)
                else: chosen.discard(identity)
                try: service.select(parent['id'], sorted(chosen))
                except ValueError as exc: status.text = str(exc)
            toggle.on_change = select; panel.add(toggle)
        panel.add(u.label('第二次复核发送前可以选资料；已经发送的请求不会追加资料或重发。每份报告最多获取3次。', size=12, color=u.MUTED))

    box.add(u.button('补充核据资料', open_panel, compact=True), status, panel)
