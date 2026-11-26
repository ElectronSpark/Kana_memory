"""Settings → 内容库: list, add (precheck then confirm), enable/disable, remove.

The page reads and writes only through ContentLibraryService; no SQL here.
File selection reuses the backup import channel of the platform layer, and
changes are refused during an exclusive backup stage or a practice round
(design 08 section 5.3). Installed content takes effect after a restart.

Phase 3/4: the precheck page also lists, category by category, conflicting
ids (rule b, overrides declared by the pack), package errors (rule c: same id
and revision, different content; not installable), missing library
dependencies and, for a new version of an installed library, the upgrade
differences of section 5.5 (fingerprint changes, removed, retired, aliases).

Phase 5: the precheck page and every library row show the source (file name,
time, the first 12 hex digits of the file sha256, signature status and public
key fingerprint). Unsigned packs and untrusted keys carry warning text but
stay installable; an invalid signature shows its refusal reason and offers no
install button. Trusting a first-seen key is offered only when the service
provides trust_signing_key (the registry write belongs to the service).

Wiring (section 16.5): library rows carry their own source fields
(installed_at_utc, source_sha256, key_fingerprint), the precheck lists the
service's rule-c result together with the page's own comparison, and a pack
declaring overrides of conflicting ids offers 安装并接受替代
(install(accept_overrides=True)).

Study routes (design 10 sections 9/10.4, WP-R4a): a library row with routes
shows 本库路线 N 条; the precheck page shows the pack's route count, entries
per route, identities outside the pack (required/optional/undeclared) and
routes refused by the shape pre-check. Nothing is shown without routes.

Section 16.10: a pack that is never installed (PackPrecheck.refusals, an invalid signature, a package error)
gets a page with what it is (library, version, file name) and why, and only 返回; an installable pack keeps
its buttons directly under the summary (library, version, file name, counts, signature) with the details
below. The file is received into the service's staging block by block (the platform's read gets the sink).
WP-R4b: the precheck's route_errors also hold routes failing the full route
check; a library row lists its ignored routes (routes_ignored); the service
gets the kana table's identities for that check.
"""
from __future__ import annotations

import asyncio

from . import content_library_rules as rules
from . import content_library_signing as signing
from . import library_activity
from . import library_file_open
from . import ui_components as u
from .app_clock import utc_now
from .content_availability import LIBRARY_CHANGE_NOTE
from .content_library_service import ContentLibraryService
from .navigation import page_view

CONTENT_LIBRARY_PAGES = frozenset({"content_libraries", "content_library_precheck"})
# The process keeps the content it started with (content_pack). On Android leaving the app and coming
# back does not end that process (the system keeps it; a new window reuses it), so say what a restart is.
RESTART_HOW = "重启指完全关闭应用再打开：电脑上关闭窗口；手机上在最近任务中划掉本应用。只切到后台再回来不算重启。"
# Where the platform can restart the process itself (process_restart): the libraries page offers it.
RESTART_NOW = "立即重启"
RESTART_NOW_HINT = "可在内容库页点「立即重启」。"
PENDING_NOTE = "内容库有变更，重启应用后生效；重启前仍按当前内容出题。"
# Design 08 section 5.7: shown until the next library change.
BUNDLED_REPLACED_NOTE = ("应用更新后自带的内容（版本 {bundled_version}）不比导入的核心库（版本 {replaced_version}）旧，"
                         "已改用内置内容；导入的核心库已移除，进度记录保留。")
# After a saved change that needs a restart, the app restarts at once through 立即重启's own path; the
# notice says library_activity.RESTARTING meanwhile. One sentence after today's note when that automatic
# restart did not happen, by why.
NO_AUTO_RESTART = {"unsupported": "应用没有自动重启：这台设备上应用不能自行重启。",
                   "blocked": "应用没有自动重启：备份、恢复或练习正在进行。",
                   "refused": "应用没有自动重启：还有未保存的内容或进行中的操作，应用未能正常关闭。",
                   "failed": "应用没有自动重启：重启时出错。"}
FAMILY_TITLES = {"word": "词汇", "grammar": "语法", "pronunciation": "读音规则", "kana": "假名", "other": "其他"}
LISTED = 10
REGISTRY_SIGNATURE_TEXT = {"unsigned": "未签名", "unverified": "签名未验证", "verified": "签名已验证",
                           "valid": "签名有效（公钥已信任）", "untrusted_key": "签名有效但公钥未信任"}
SOURCE_WARNINGS = {"unsigned": "警告：未签名，无法确认作者和内容是否被改动。",
                   "unverified": "警告：安装时未验证签名，来源未确认。",
                   "untrusted_key": "警告：签名公钥未信任，来源未确认。"}


def _message(error):
    text = str(error).splitlines()[0] if str(error) else "操作未能完成，请重试。"
    return text[:180]


def _short_sha(value):
    return value[:12] if value else "无"


def _time_text(value):
    return value.replace("T", " ").replace("+00:00", " UTC") if value else "无"


def _source_lines(row):
    """Source rows of one ContentLibraryService.libraries() row (section 4.5)."""
    if row["source_kind"] == "bundled":
        version = f" · 内置内容版本 {row['bundled_version']}" if row.get("bundled_version") else ""
        return [u.label("来源：内置（随应用安装与校验）" + version, size=12, color=u.MUTED)]
    status = row["signature_status"]
    rows = [u.label(f"来源：导入文件 {row['source_name'] or '（无文件名）'} · 导入时间 {_time_text(row['installed_at_utc'])}"
                    f" · sha256 {_short_sha(row['source_sha256'])}", size=12, color=u.MUTED),
            u.label("签名：" + REGISTRY_SIGNATURE_TEXT[status]
                    + (f" · 公钥指纹 {signing.display_fingerprint(row['key_fingerprint'])}"
                       f"（{row.get('public_key_id') or ''}）" if row.get("key_fingerprint") else ""),
                    size=12, color=u.MUTED)]
    if status in SOURCE_WARNINGS:
        rows.append(u.label(SOURCE_WARNINGS[status], size=12))
    return rows


def _route_lines(check):
    """Precheck rows for the pack's study routes; [] when it carries none."""
    if not check.route_count and not check.route_errors:
        return []
    outside = (len(check.route_external_required) + len(check.route_external_optional)
               + len(check.route_external_undeclared))
    rows = [u.label(f"本库路线 {check.route_count} 条", size=14)]
    for route_id, count in check.route_entry_counts[:LISTED]:
        rows.append(u.label(f"路线 {route_id}：{count} 个条目", size=12, color=u.MUTED))
    if outside:
        rows.append(u.label(f"路线引用本库以外的身份 {outside} 项（必需 {len(check.route_external_required)} · "
                            f"可选 {len(check.route_external_optional)} · 未声明 {len(check.route_external_undeclared)}）",
                            size=14))
    for route_id, reason in check.route_errors[:LISTED]:
        rows.append(u.label(f"路线规格有误（将被忽略）：{route_id} · {reason}", size=12, color=u.MUTED))
    return rows


def _family_line(families):
    parts = [f"{FAMILY_TITLES.get(name, name)} {count}" for name, count in families.items() if count]
    return "内容：" + ("、".join(parts) if parts else "无")


class ContentLibraryUIMixin:
    def content_library_service(self):
        key = id(self.store)
        cached = getattr(self, "_content_library_service", None)
        if cached is None or cached[0] != key:
            repo = getattr(self, "repo", None)
            lookup = (lambda word_id: repo.get_word(word_id)) if repo is not None and hasattr(repo, "get_word") else None
            kana = ((lambda: rules.kana_identities_of(repo.kana_list()))
                    if repo is not None and hasattr(repo, "kana_list") else None)
            learning = getattr(self, "learning_store", None)
            # Review B-11: library rows count from the home page's AvailabilityView.
            availability = learning.availability if callable(getattr(learning, "availability", None)) else None
            self._content_library_service = (key, ContentLibraryService(self.store, word_exists=lookup,
                                                                        kana_identities=kana,
                                                                        availability=availability))
        return self._content_library_service[1]

    # ------------------------------------------------------------ restart (process_restart)

    def _can_restart(self):
        return getattr(getattr(self, "platform", None), "can_restart", False) is True

    def _restart_hint(self):
        """What an alert adds after 重启应用后生效: the page's 立即重启, or how to restart by hand."""
        return RESTART_NOW_HINT if self._can_restart() else RESTART_HOW

    def _close_for_restart(self):
        """The App's normal close (KanaMemory.on_exit): True when everything is saved and closed."""
        close = getattr(type(self), "on_exit", None)
        return callable(close) and close(self) is True

    def _restart_process(self):
        """The platform's restart; returns only when it did not happen. Tried at most once per process."""
        from . import process_restart
        process_restart.mark_attempt()
        try:
            return bool(self.platform.restart_app())
        except Exception as error:  # noqa: BLE001 - a refused restart leaves the app running
            from .diagnostic_events import record_error
            record_error("content_library", "restart", error)
            return False

    async def restart_for_library_change(self, widget=None, **kwargs):
        """立即重启: close as on a normal exit, then a new process loads the saved libraries."""
        outcome, reason = await self._restart_through_close()
        if outcome == "blocked":
            self.alert(reason)
        elif outcome == "unsupported":
            self.alert(RESTART_HOW)
        # "refused": the close said why (an unsaved draft, a running backup, ...); nothing was closed

    async def _restart_through_close(self):
        """The one restart path (立即重启 and the automatic restart): the App's normal close, then the
        platform's restart. Returns (why it did not restart, the blocking reason): "blocked" (backup,
        restore or a practice page shown), "unsupported" (no restart on this platform), "refused" (the close
        refused; nothing was closed). A platform that refuses after the close ends the app as before."""
        reason = self._content_library_blocked()
        if reason:
            return "blocked", reason
        if not self._can_restart():
            return "unsupported", None
        if not self._close_for_restart():
            return "refused", None
        if self._restart_process():
            return "restarted", None  # a real restart never returns
        # The platform refused: the stores are closed, so the app ends here.
        try:
            await self.main_window.dialog(u.toga.InfoDialog("未能重启", "未能自动重启。" + RESTART_HOW))
        finally:
            self.exit()
        return "ended", None

    # ------------------------------------------------------------ automatic restart (library_activity)

    def _begin_library_restart(self):
        """After a saved change that needs a restart, on the libraries page just built for it: the one place
        that decides. True: restart now (the caller then runs _automatic_library_restart; the notice, or a
        status block, says RESTARTING). False: the page's note says why the app will not restart by itself
        and the caller says what it said before."""
        state = library_activity.of(self)
        state.note = None
        if self.page != "content_libraries":
            return False  # the page was not built (a backup holds the screen)
        if not self._can_restart():
            state.note = NO_AUTO_RESTART["unsupported"]
            if state.slot is not None:
                self._render_restart_notice(state.slot)
            return False
        if state.working:
            state.advance(library_activity.RESTARTING)
        elif state.slot is not None:
            state.slot.clear()
            state.slot.add(self.note(library_activity.RESTARTING))
        else:
            return False  # nothing pending (a library removed before it was ever loaded): no restart
        return True

    async def _automatic_library_restart(self):
        """立即重启's path at once; when it does not restart, the page keeps today's note and 立即重启 and says why."""
        await asyncio.sleep(library_activity.PAINT_PAUSE)  # the notice is drawn before the app closes
        try:
            outcome, _ = await self._restart_through_close()
        except Exception as error:  # noqa: BLE001 - the page keeps today's note and button
            from .diagnostic_events import record_error
            record_error("content_library", "restart", error)
            outcome = "failed"
        state = library_activity.of(self)
        if outcome not in NO_AUTO_RESTART:
            # A real restart never returns; a platform that returns before its process ends ("restarted"), or
            # an app that ends, keeps the status: until then no page offers 添加内容库 or any other action.
            return
        state.note = NO_AUTO_RESTART[outcome]
        if getattr(self, "_backup_closing", False):
            return
        if state.end() and self.page == "content_libraries":
            self.show_content_libraries()  # the status block goes; the notice says why
        elif state.slot is not None:
            self._render_restart_notice(state.slot)

    def _render_restart_notice(self, slot):
        """The pending-change note: 立即重启 where the platform can restart, else how to restart by hand, with
        the sentence on why the app did not restart by itself."""
        state = library_activity.of(self)
        slot.clear()
        note = state.note or ""
        if self._can_restart():
            slot.add(self.note(PENDING_NOTE + note),
                     u.button(RESTART_NOW, lambda w: self.loop.create_task(self.restart_for_library_change()),
                              primary=True))
        else:
            slot.add(self.note(PENDING_NOTE + note + RESTART_HOW))

    def restart_for_pending_library_change(self):
        """At start-up, before any page: the App started again in this process (Android re-created
        its window) while a library change is pending, so this process still serves the old content.
        Restart once; when that returns, go on as before (the libraries page notes the change)."""
        from . import process_restart
        started_before = process_restart.note_start()
        if not started_before:
            return False
        try:
            pending = self.content_library_service().pending_restart()
        except Exception:  # noqa: BLE001 - no registry: nothing is pending
            pending = False
        action = process_restart.decide(started_before=started_before, change_pending=pending,
                                        can_restart=self._can_restart(),
                                        already_attempted=process_restart.attempted())
        if action != process_restart.RESTART:
            return False
        # Nothing to save yet: this start has written only committed rows (registry sync, round archive).
        return self._restart_process()

    def _content_library_blocked(self):
        # A sealed round the learner has left does not block (design 08 section 5.3): only a practice page
        # does; the restart's ordinary exit and start archive that round.
        return rules.change_blocked_reason(backup_busy=getattr(self, "_backup_busy", False),
                                           focused=getattr(self, "_practice_context", False))

    def _discard_content_library_inspection(self):
        inspection = getattr(self, "_content_library_inspection", None)
        self._content_library_differences = None
        if inspection is not None:
            self._content_library_inspection = None
            self.content_library_service().discard(inspection)

    def content_library_installed_revisions(self, content_ids):
        """Revisions of installed core catalogue items (conflict rule c); read-only."""
        from .learning_content import get_learning_item
        result = {}
        for content_id in content_ids:
            item = get_learning_item(content_id)
            if item is not None and item.get("revision") is not None:
                result[content_id] = str(item["revision"])
        return result

    def content_library_differences(self, inspection):
        """Phase 3/4 differences for the precheck page, from service reads only."""
        service = self.content_library_service()
        manifest, check = inspection.manifest, inspection.precheck
        registry = service.registry()
        learned = service.learned_ids()
        existing = rules.registry_entry(registry, manifest["library_id"])
        upgrade = None
        if existing is not None:
            if existing["source"]["kind"] == "file":
                old = service.installed_manifest(existing)
                previous = {cid: "" for cid in (old["provides"]["items"] if old else ())}
                previous.update(old["fingerprints"] if old else {})
            else:
                previous = service.catalogue_fingerprints()
            upgrade = rules.upgrade_precheck(manifest, previous_fingerprints=previous, learned_ids=learned,
                                             previous_version=existing["library_version"])
        conflicts = set(check.conflicting_ids)
        installed = {}
        for entry in rules.effective_entries(registry):
            if entry["library_id"] == manifest["library_id"] or entry["source"]["kind"] != "file":
                continue
            other = service.installed_manifest(entry)
            if other:
                for cid, revision in other.get("revisions", {}).items():
                    installed.setdefault(cid, revision)
        if manifest["profile"] != "core" and conflicts - set(installed):
            for cid, revision in self.content_library_installed_revisions(sorted(conflicts - set(installed))).items():
                installed.setdefault(cid, revision)
        revision_errors = tuple(sorted(set(rules.revision_errors_of(manifest, installed, conflicts))
                                       | set(check.revision_errors)))
        return {"upgrade": upgrade, "revision_errors": revision_errors,
                "stays_disabled": existing is not None and not existing["enabled"],
                "overrides": tuple(row["id"] for row in manifest["overrides"]),
                "signature": rules.pack_signature(inspection.report, registry),
                "checked_at": rules.now_utc_text(utc_now())}

    @page_view()
    def show_content_libraries(self, widget=None, **kwargs):
        # The sentence on why the app did not restart by itself stays while this page is rebuilt; coming
        # here from any other page drops it. While a library file is read, checked or installed the page
        # shows the status block at its top and offers no action that starts another library operation.
        state = library_activity.of(self)
        rebuilt = getattr(self, "page", None) == "content_libraries"
        if not rebuilt:
            state.note = None
        state.slot = None
        working = state.working
        self._discard_content_library_inspection()
        service = self.content_library_service()
        children = self._library_status_rows()
        try:
            rows = service.libraries()
            pending = service.pending_restart()
            mismatch = service.root_mismatch()
            replaced = service.bundled_notice()
        except Exception as error:
            self.screen("content_libraries", "内容库", *children,
                        u.label("无法读取内容库设置：" + _message(error), size=14), u.button("返回", self.go_back))
            return
        slot = None
        if pending and not working:
            slot = u.col()
            self._render_restart_notice(slot)
            children.append(slot)
        if mismatch:
            children.append(self.note("内容库目录与本次启动载入的目录不一致，导入的库暂未载入；请重启应用。"))
        if replaced:
            children.append(self.note(BUNDLED_REPLACED_NOTE.format(**replaced)))
        imported = [row for row in rows if row["source_kind"] == "file"]
        add = [] if working else [u.button("添加内容库", self.add_content_library, primary=True)]
        if not imported:
            children.append(self.entry_card(u.label("还没有内容库", size=18, bold=True),
                                            u.label("导入" + library_file_open.FILE_CHOICE + "后，其课程会与内置内容合并；进度与内容分开保存。", size=13, color=u.MUTED),
                                            *add))
        else:
            children.extend(add)
        self.content_library_switches = {}
        # Design 12 UI6: 设为重点 per library (none for one that is disabled or unusable, unless it is the focus).
        from .arrangement.model import FocusScope
        from .study_path_ui import focus_controls, focus_models
        focus_buttons = () if working else focus_models(self, *(FocusScope("library", row["library_id"]) for row in rows))
        for index, row in enumerate(rows):
            lines = [u.label(row["title"], size=17, bold=True),
                     u.label(f"{row['library_id']} · 版本 {row['version']} · {row['check']}", size=12, color=u.MUTED),
                     *_source_lines(row),
                     u.label(_family_line(row["families"]), size=13),
                     *([u.label(f"本库路线 {row['route_count']} 条", size=13)] if row.get("route_count") else []),
                     *([u.label(f"已忽略路线 {row['routes_ignored']} 条", size=12, color=u.MUTED)]
                       if row.get("routes_ignored") else []),
                     *[u.label(f"已忽略：{route_id} · {reason}", size=12, color=u.MUTED)
                       for route_id, reason in row.get("routes_ignored_reasons", ())[:LISTED]],
                     u.label(f"已学 {row['learned']} 项 · 暂不可用的已学项 {row['orphaned']} 项"
                             + (f" · {row['status_text']}" if row.get("status_text") else ""), size=13)]
            if row["is_core"]:
                lines.append(u.label("核心库始终启用" + ("，不可移除" if not row["removable"] else ""), size=12, color=u.MUTED))
            elif not working:
                switch = u.switch("启用", value=row["enabled"],
                                  on_change=lambda w, library_id=row["library_id"]: self.toggle_content_library(library_id, w))
                self.content_library_switches[row["library_id"]] = switch
                lines.append(switch)
            if not working:
                lines += focus_controls(self, focus_buttons[index], self.show_content_libraries)
            if row["removable"] and not working:
                lines.append(u.button("移除", lambda w, library_id=row["library_id"]: self.loop.create_task(
                    self.remove_content_library(library_id)), compact=True))
            children.append(self.entry_card(*lines, index=index))
        children.append(self.note("库文件只含数据，不含代码；安装与移除在重启后生效，" + LIBRARY_CHANGE_NOTE
                                  + "。已学内容的记录、到期与草稿不随库变化。"))
        self.screen("content_libraries", "内容库", *children)
        if self.page == "content_libraries":
            state.slot = slot

    def _library_status_rows(self):
        """The status block while a library file is read, checked or installed (library_activity); [] otherwise.
        Its phase label is kept, so the next phase changes its text without building the page again."""
        state = library_activity.of(self)
        if not state.working:
            return []
        state.label = u.label(state.phase, size=15, bold=True)
        # The file's name, or an empty line that shows it once a picked file's name is known.
        state.name_label = u.label(state.file_name or "", size=13)
        lines = [u.activity_indicator(margin_bottom=6), state.label, state.name_label,
                 u.label(library_activity.WAIT_NOTE, size=12, color=u.MUTED)]
        return [self.entry_card(*lines)]

    def _library_work_current(self, token):
        """Whether the libraries page that shows the status (page token ``token``) is still the one shown."""
        return (self.page == "content_libraries" and self._page_token == token
                and not getattr(self, "_backup_closing", False))

    async def _library_status_shown(self):
        """Let the event loop turn so the window draws the status block before the work starts."""
        await asyncio.sleep(library_activity.PAINT_PAUSE)

    async def add_content_library(self, widget=None, **kwargs):
        if getattr(self, "_content_library_request", False) or library_activity.of(self).working:
            return
        reason = self._content_library_blocked()
        if reason:
            self.alert(reason)
            return
        await self._precheck_content_library_file(self.platform.import_library_pack, "导入文件")

    # ------------------------------------------------------------ a library file opened from outside (library_file_open)
    def watch_library_file_requests(self):
        """At the end of start-up: the platform reports the files the app is asked to open (the launching
        intent now, later ones as they arrive); each is handled once the app runs, never earlier."""
        watch = getattr(getattr(self, "platform", None), "watch_library_file_requests", None)
        if not callable(watch):
            return False
        requests = library_file_open.of(self)
        try:
            watch(requests.deliver)
        except Exception as error:  # noqa: BLE001 - the app runs on without opening files
            from .diagnostic_events import record_error
            record_error("library_file_open", "request", error)
            return False
        # Started: the first page is shown and the record is open; the loop runs the handler after start-up.
        self.loop.call_soon(requests.start, self._schedule_library_file_open)
        return True

    def _schedule_library_file_open(self, request):
        self.loop.create_task(self.open_content_library_file(request))

    def _library_file_open_busy(self):
        navigation, root = getattr(self, "navigation", None), getattr(self, "root", None)
        flush = getattr(self, "flush_drafts", None)
        return library_file_open.busy(
            blocked_reason=self._content_library_blocked(),
            choosing=getattr(self, "_content_library_request", False),
            working=library_activity.of(self).working,
            confirming=getattr(self, "_leave_confirmation_active", False),
            navigation_hidden=navigation is not None and root is not None and navigation not in root.children,
            unsaved=callable(flush) and flush() is False)

    async def open_content_library_file(self, request):
        """A library file the app was asked to open: the content library page with that file's precheck,
        the state after picking it with 添加内容库 (same inspection, texts and 安装 button). Never installs.
        While the app cannot leave what it is doing: one notice, and the request is dropped."""
        if getattr(self, "_backup_closing", False):
            return
        if self._library_file_open_busy():
            self.alert(library_file_open.BUSY_NOTICE)
            return
        shown = await self._precheck_content_library_file(
            lambda sink: self.platform.read_library_file(request, sink=sink), request.name, file_name=request.name)
        if not shown:
            self.alert(library_file_open.BUSY_NOTICE)

    async def _precheck_content_library_file(self, read, source_name, *, file_name=None):
        """Read one pack file, stage and precheck it, show the precheck; the libraries page shows the status
        block meanwhile. ``read(sink)``: the platform writes the file into ``sink`` (a PackStaging) block by
        block and returns the file's display name ("" when unknown), or None when nothing was chosen; a
        platform that hands over the file's bytes instead is written into the sink here. ``file_name``: the
        file's name when known before (an opened file); a picked file's name is known once it was chosen. The
        status, the precheck page and an error message name the file. False when the page could not be shown.

        Threads: the copy runs in the platform's reader (Java thread, then a worker), staging and the precheck
        on workers (asyncio.to_thread; the precheck on a ContentLibraryService.snapshot, which reads the store
        here first); the differences, the pages and every store access stay on this (the UI) thread."""
        state = library_activity.of(self)
        self._content_library_request = True
        state.begin(library_activity.READING, file_name)
        failure, staging = None, None
        try:
            self.show_content_libraries()
            if self.page != "content_libraries":
                return False
            token = self._page_token
            await self._library_status_shown()
            service = self.content_library_service()
            staging = service.open_staging(file_name or source_name)
            received = await read(staging)
            if received is None or not self._library_work_current(token):
                return True
            if isinstance(received, (bytes, bytearray)):
                await asyncio.to_thread(staging.write_all, received)  # a platform that hands over the bytes
            elif isinstance(received, str) and received.strip() and not file_name:
                file_name = library_file_open.display_name(received, None)
                staging.named(file_name)
                state.named(file_name)
            state.advance(library_activity.CHECKING)
            await self._library_status_shown()
            registry = service.registry()
            staged = await asyncio.to_thread(service.stage_file, staging, registry=registry)
            staging = None  # staged: discarded below with the inspection
            if not self._library_work_current(token) or self._content_library_blocked():
                service.discard(staged)
                return True
            inspection = await asyncio.to_thread(service.snapshot(staged.manifest).precheck_staged, staged)
            if not self._library_work_current(token) or self._content_library_blocked():
                service.discard(inspection)
                return True
            try:
                differences = self.content_library_differences(inspection)
            except Exception:
                service.discard(inspection)
                raise
            state.end()
            self._content_library_inspection = inspection
            self._content_library_differences = differences
            self.show_content_library_precheck()
            return True
        except Exception as error:  # noqa: BLE001 - the learner reads one sentence; the detail is recorded
            failure = error
            return True
        finally:
            if staging is not None:
                staging.discard()  # cancelled, refused while copying, or failed before staging
            self._content_library_request = False
            if state.end() and self.page == "content_libraries" and not getattr(self, "_backup_closing", False):
                self.show_content_libraries()  # the status block goes
            if failure is not None:
                self.alert(self._library_file_error(failure, file_name))

    def _library_file_error(self, error, file_name=None, *, prefix="无法读取内容库："):
        """One sentence for an error about a library file (content_library_rules.learner_error), after the
        file's name when known; anything else keeps its own message after ``prefix``. A version refusal (the
        pack needs a newer app) was read: its sentence never follows ``prefix`` (design 08 section 16.10)."""
        sentence = rules.learner_error(error)
        if sentence is not None:
            from .diagnostic_events import record_error
            record_error("content_library", "read", error)
        if sentence is not None and rules.is_version_refusal(error):
            return library_file_open.with_name(file_name, sentence)
        if file_name:
            return f"{file_name}：" + (sentence or prefix + _message(error))
        return prefix + (sentence or _message(error))

    @page_view()
    def show_content_library_precheck(self, widget=None, **kwargs):
        inspection = getattr(self, "_content_library_inspection", None)
        if inspection is None:
            self.show_content_libraries()
            return
        manifest, check = inspection.manifest, inspection.precheck
        differences = getattr(self, "_content_library_differences", None) or {}
        signature = differences.get("signature") or rules.pack_signature(inspection.report)
        refusal = self._content_library_refusal_rows(check, signature, differences)
        # What the file is, first: its library and version, then the file's name (both ways of opening it).
        replaces = f"（替换已安装的 {check.replaces_version}）" if check.replaces_version and not refusal else ""
        identity = [u.label(manifest["title"], size=20, bold=True),
                    u.label(f"{manifest['library_id']} · 版本 {manifest['library_version']}{replaces}", size=12,
                            color=u.MUTED),
                    u.label(f"文件名：{inspection.source_name or library_file_open.NO_NAME}", size=13)]
        if refusal:
            # Design 08 section 16.10: a refused pack shows what it is and why it is refused; nothing that
            # reads like an installation, and no 安装.
            self.screen("content_library_precheck", "添加内容库", *identity, *refusal,
                        u.button("返回", lambda w, **kw: self.show_content_libraries()))
            return
        blocking = check.blocking_reasons()
        # The summary and the buttons fit a phone's first screen; the details follow below them.
        children = [*identity,
                    u.label(_family_line(rules.count_families(manifest["provides"]["items"]))
                            + f" · 新增内容 {check.new_item_count} 项", size=13),
                    u.label("签名状态：" + signature.status_text(), size=13)]
        if blocking:
            children.append(u.label("默认不安装：" + "；".join(blocking), size=13))
            children.append(u.button("仍然安装（相关项标为依赖缺失、不可加入）",
                                     lambda w, **kw: self.loop.create_task(self.install_content_library(accept_missing_references=True)),
                                     multiline=True))
        else:
            children.append(u.button("安装", lambda w, **kw: self.loop.create_task(self.install_content_library()), primary=True))
        replaceable = self._content_library_replaceable(check, differences)
        if replaceable and not blocking:
            children.append(u.button(f"安装并接受本库声明的替代（{len(replaceable)} 项，以本库版本为准）",
                                     lambda w, **kw: self.loop.create_task(self.install_content_library(accept_overrides=True)),
                                     multiline=True))
        children.append(u.button("取消", lambda w, **kw: self.show_content_libraries()))
        children.append(self.note("安装后重启应用生效；重启前仍按当前内容出题。进度记录不会改动。"))
        if differences.get("stays_disabled"):
            children.append(self.note("这个内容库目前已停用：更新后仍保持停用，需要时请在内容库页重新启用。"))
        if check.replaces_core:
            children.append(self.note("这是核心库文件：安装后其教学数据与词典将替换内置内容；移除即恢复内置库。"))
        children += [self.heading("预检结果"),
                     u.label(f"与已安装库相同的内容 {len(check.same_fingerprint_ids)} 项", size=14),
                     u.label(f"同 ID 但内容不同（冲突） {len(check.conflicting_ids)} 项 · 默认保留已安装库的版本", size=14),
                     u.label(f"未满足的必需引用 {len(check.missing_required_references)} 项", size=14),
                     u.label(f"可选引用缺失 {len(check.missing_optional_references)} 项", size=14),
                     u.label(f"将被本库标为不再提供的已学项 {len(check.retired_learned_ids)} 项", size=14),
                     *_route_lines(check)]
        for content_id in check.missing_required_references[:10]:
            children.append(u.label("缺少：" + content_id, size=12, color=u.MUTED))
        for warning in check.warnings:
            children.append(u.label("提示：" + warning, size=12, color=u.MUTED))
        children.extend(self._content_library_source_rows(inspection, signature, differences))
        children.extend(self._content_library_difference_rows(check, differences))
        self.screen("content_library_precheck", "添加内容库", *children)

    def _content_library_refusal_rows(self, check, signature, differences):
        """The rows of a pack that is never installed (PackPrecheck.refusals, an invalid signature, a package
        error), each sentence with its reason; [] when the pack is installable (maybe after consent)."""
        package_errors = differences.get("revision_errors", ())
        sentences, details = [], []
        if check.bundled_error:
            sentences.append(check.bundled_error + "。")
        if check.same_version_error:
            sentences.append(check.same_version_error + "。")
        if signature.refused:
            sentences.append(signature.refusal_reason() + "。请向库作者重新获取文件。")
            details.append(u.label("签名状态：" + signature.status_text(), size=12))
        if package_errors:
            sentences.append(f"包错误：{len(package_errors)} 项与已安装内容同 ID、同修订号但内容不同，不能安装。"
                             "请向库作者索取修订号已更新的版本。")
            details += [u.label("包错误：" + content_id + "（同修订号但内容不同）", size=12, color=u.MUTED)
                        for content_id in package_errors[:LISTED]]
        if check.merge_errors:
            sentences.append("与已安装的内容库合并时出错，不能安装：" + "；".join(check.merge_errors) + "。")
        if not sentences:
            return []
        return [self.heading("不能安装"), *(u.label(text, size=13) for text in sentences), *details]

    @staticmethod
    def _content_library_replaceable(check, differences):
        """Conflicting ids the pack declares in overrides (section 6.3 b); core packs never need it."""
        if check.replaces_core:
            return ()
        overrides = set(differences.get("overrides", ()))
        return tuple(cid for cid in check.conflicting_ids if cid in overrides)

    def _content_library_source_rows(self, inspection, signature, differences):
        rows = [self.heading("来源"),
                u.label(f"导入时间：{_time_text(differences.get('checked_at'))}（检查时间；安装时另行记录）", size=13),
                u.label(f"文件 sha256：{_short_sha(inspection.source_sha256)}（前 12 位）", size=13)]
        for line in signature.lines():
            rows.append(u.label(line, size=12, color=u.MUTED) if signature.status == "valid" else u.label(line, size=12))
        trust = getattr(self.content_library_service(), "trust_signing_key", None)
        if signature.trust is not None and signature.trust.can_confirm and trust is not None:
            rows.append(u.button("核对指纹无误，信任此公钥", lambda w, **kw: self.trust_content_library_key(), multiline=True))
        return rows

    def trust_content_library_key(self):
        """TOFU confirmation: the service writes trusted_keys, then the precheck is redrawn."""
        inspection = getattr(self, "_content_library_inspection", None)
        differences = getattr(self, "_content_library_differences", None) or {}
        signature = differences.get("signature")
        if (inspection is None or self.page != "content_library_precheck" or signature is None
                or signature.trust is None or not signature.trust.can_confirm):
            return
        service = self.content_library_service()
        try:
            service.trust_signing_key(signature.public_key_id, signature.public_key)
            registry = service.registry()
        except Exception as error:
            self.alert("未能信任此公钥：" + _message(error))
            return
        self._content_library_differences = {**differences,
                                             "signature": rules.pack_signature(inspection.report, registry)}
        self.show_content_library_precheck()

    def _content_library_difference_rows(self, check, differences):
        rows = []
        overrides = set(differences.get("overrides", ()))
        if check.conflicting_ids:
            rows.append(self.heading("同 ID 冲突"))
            for content_id in check.conflicting_ids[:LISTED]:
                note = ("本库声明替代；核心库与已安装库优先，未确认前仍以已安装版本为准" if content_id in overrides
                        else "保留已安装库的版本")
                rows.append(u.label(f"冲突：{content_id}（{note}）", size=12, color=u.MUTED))
            if len(check.conflicting_ids) > LISTED:
                rows.append(u.label(f"另有 {len(check.conflicting_ids) - LISTED} 项冲突未列出", size=12, color=u.MUTED))
        for content_id in differences.get("revision_errors", ())[:LISTED]:
            rows.append(u.label("包错误：" + content_id + "（同修订号但内容不同）", size=12, color=u.MUTED))
        for library_id in check.missing_libraries:
            rows.append(u.label("依赖的库未安装：" + library_id, size=12, color=u.MUTED))
        upgrade = differences.get("upgrade")
        if upgrade is not None:
            rows.append(self.heading(f"升级差异（相对已安装版本 {upgrade.previous_version}）"))
            rows.extend(u.label(line, size=13) for line in upgrade.lines())
            for content_id in upgrade.fingerprint_changed_learned[:LISTED]:
                rows.append(u.label("内容有变化的已学项：" + content_id, size=12, color=u.MUTED))
            for content_id in upgrade.removed_learned[:LISTED]:
                rows.append(u.label("将变为孤儿的已学项：" + content_id, size=12, color=u.MUTED))
            for content_id, replaced_by in upgrade.retired_learned[:LISTED]:
                target = f"，由 {replaced_by} 取代" if replaced_by else ""
                rows.append(u.label(f"不再提供的已学项：{content_id}{target}", size=12, color=u.MUTED))
            for old, new in upgrade.aliases[:LISTED]:
                rows.append(u.label(f"别名：{old} → {new}", size=12, color=u.MUTED))
            rows.append(self.note("已学项沿用保存的教案快照、草稿与到期；只有未开始的项按新版本入学。"))
        elif overrides:
            rows.append(u.label(f"本库声明替代其他库同 ID 内容 {len(overrides)} 项；核心库优先，未确认前不生效。",
                                size=13))
        return rows

    async def install_content_library(self, *, accept_missing_references=False, accept_overrides=False):
        inspection = getattr(self, "_content_library_inspection", None)
        if inspection is None or self.page != "content_library_precheck":
            return
        differences = getattr(self, "_content_library_differences", None) or {}
        refusal = rules.install_refusal(inspection.report)
        if refusal:
            self.alert(refusal)
            return
        if differences.get("revision_errors"):
            self.alert("这个内容库文件有包错误（同 ID、同修订号但内容不同），不能安装。")
            return
        reason = self._content_library_blocked()
        if reason:
            self.alert(reason)
            return
        service = self.content_library_service()
        self._content_library_inspection = None
        self._content_library_differences = None
        state = library_activity.of(self)
        self._content_library_request = True
        state.begin(library_activity.INSTALLING, None if inspection.source_name == "导入文件" else inspection.source_name)
        # Threads: the re-check and the copy into the library folder run on a worker (prepare_install on a
        # snapshot, which reads the store here first); the registry write (commit_install) stays on this thread.
        try:
            self.show_content_libraries()  # the status block, from the 安装 tap until the restart
            await self._library_status_shown()
            snapshot = service.snapshot(inspection.manifest)
            prepared = await asyncio.to_thread(snapshot.prepare_install, inspection,
                                               accept_missing_references=accept_missing_references,
                                               accept_overrides=accept_overrides)
            reason = self._content_library_blocked()
            if reason or getattr(self, "_backup_closing", False):
                service.abandon_install(prepared)
                raise ValueError(reason or "应用正在关闭。")
            entry = service.commit_install(prepared)
        except Exception as error:  # noqa: BLE001 - nothing was registered; the page says why
            self._content_library_request = False
            state.end()
            if not getattr(self, "_backup_closing", False):
                self.show_content_libraries()
                self.alert(self._library_file_error(error, prefix="未能安装内容库："))
            return
        self._content_library_request = False
        title = entry["summary"].get("title", entry["library_id"])
        on_page = self.page == "content_libraries"
        if entry["enabled"] and on_page and self._begin_library_restart():
            await self._automatic_library_restart()  # the status block says RESTARTING until the app closes
            return
        if state.end() and self.page == "content_libraries":
            self.show_content_libraries()
        if not entry["enabled"]:  # nothing to restart for: it stays off until enabled
            self.alert(f"已安装 {title}；这个内容库仍保持停用，启用并重启应用后才会载入。")
        else:
            self.alert(f"已安装 {title}，重启应用后生效。" + self._restart_hint())

    def toggle_content_library(self, library_id, control):
        if getattr(self, "_content_library_reverting", False):
            return
        reason = self._content_library_blocked()
        try:
            if reason:
                raise ValueError(reason)
            self.content_library_service().set_enabled(library_id, bool(control.value))
        except Exception as error:
            self._content_library_reverting = True
            try:
                control.value = not control.value
            finally:
                self._content_library_reverting = False
            self.alert(_message(error))
            return
        enabled = bool(control.value)
        self.show_content_libraries()
        if self._begin_library_restart():
            self.loop.create_task(self._automatic_library_restart())
        else:
            self.alert(("已启用，重启应用后载入。" + self._restart_hint() if enabled
                        else "已停用：" + LIBRARY_CHANGE_NOTE + "。"))

    async def remove_content_library(self, library_id):
        reason = self._content_library_blocked()
        if reason:
            self.alert(reason)
            return
        token, origin = self._page_token, self.page
        service = self.content_library_service()
        try:
            notice = service.removal_notice(library_id)
            confirmed = await self.main_window.dialog(u.toga.ConfirmDialog("移除内容库", notice))
            if not confirmed or self.page != origin or self._page_token != token or self._content_library_blocked():
                return
            service.remove(library_id)
        except Exception as error:
            self.alert("未能移除内容库：" + _message(error))
            return
        self.show_content_libraries()
        if self._begin_library_restart():
            await self._automatic_library_restart()
        else:
            self.alert("已移除，重启应用后生效。进度记录已保留。" + self._restart_hint())
