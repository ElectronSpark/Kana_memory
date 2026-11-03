"""Device-local AI service setup; discovery never sends learning records."""
import asyncio

from .ai_credentials import DeviceSecrets
from .ai_service import AIServiceError
from .ai_connection import (AUTH_MODES, OUTPUT_MODES, PRESETS, PROTOCOLS, ConfiguredProvider,
    credential_slot, fetch_models, load_connection, normalize_token, preset_config,
    validate_connection, validate_token)
from .navigation import page_view

CONNECTION_SETTING = "ai_connection_v1"  # Preserve backup exclusion and settings identity.
MODEL_PLACEHOLDER = "手动填写模型 ID"


class AIServiceUIMixin:
    def _ai_secrets(self, config=None):
        config = config or load_connection(self.store.get_setting(CONNECTION_SETTING, {}))
        return DeviceSecrets(self, credential_slot(config))

    def ai_connection_status(self):
        try:
            config = validate_connection(self.store.get_setting(CONNECTION_SETTING, {}))
            key = self._ai_secrets(config).get() if config["auth"] == "key" else ""
            validate_token(key, config)
            return {"ready": True, "message": "已配置本机服务；连接状态将在请求时检查"}
        except AIServiceError as exc:
            return {"ready": False, "message": str(exc)}

    def ai_client(self):
        config = validate_connection(self.store.get_setting(CONNECTION_SETTING, {}))
        return ConfiguredProvider(config, self._ai_secrets(config).get() if config["auth"] == "key" else "")

    def tutor_client(self):
        config = validate_connection(self.store.get_setting(CONNECTION_SETTING, {}))
        return ConfiguredProvider(config, self._ai_secrets(config).get() if config["auth"] == "key" else "", tutor=True)

    def material_budget_context(self):
        from .material_budget import context_for
        return context_for(self.store, validate_connection(self.store.get_setting(CONNECTION_SETTING, {})))

    @page_view()
    def show_material_budget_settings(self, widget=None):
        from . import ui_components as u
        from .material_budget import policy_for, DEFAULT_LIMITS
        config = load_connection(self.store.get_setting(CONNECTION_SETTING, {}))
        policy = policy_for(self.store, config)
        limits = policy['limits'] if policy else DEFAULT_LIMITS
        self._material_budget_config = config
        self._material_budget_fields = {
            'tokens': u.toga.TextInput(value=str(limits['tokens'])),
            'microusd': u.toga.TextInput(value=str(limits['microusd'] / 1000000)),
            'input': u.toga.TextInput(value=str(policy['rates']['input'] / 1000) if policy else ''),
            'output': u.toga.TextInput(value=str(policy['rates']['output'] / 1000) if policy else ''),
        }
        self._material_budget_status = u.label('', size=13)
        children = [u.label(config['model'] or '请先保存服务和模型', size=18, bold=True),
                    u.label('用于每批材料的生成、复核和一次修订。修改只用于新批次；已有调用不会清零。', size=14)]
        for key, label in [('tokens', '每批 token 上限'), ('microusd', '每批费用估算上限（美元）'),
                           ('input', '输入每百万 token 单价（美元）'), ('output', '输出每百万 token 单价（美元）')]:
            children.extend([u.label(label, size=14), self._material_budget_fields[key]])
        children.extend([u.label('按填写的单价估算，不等于账单。输入单价应覆盖缓存写入等附加费；未知用量保留预留额度。免费本地模型单价可填 0。', size=13, color=u.MUTED),
            self._material_budget_status, u.button('保存材料预算', self._save_material_budget, primary=True)])
        self.screen('material_budget_settings', '材料调用预算', *children)

    def _save_material_budget(self, widget=None):
        from .material_budget import SETTINGS, connection_key, decimal_units, validate_policy, validate_settings
        try:
            config = validate_connection(self.store.get_setting(CONNECTION_SETTING, {}))
            if connection_key(config) != connection_key(self._material_budget_config):
                raise ValueError('服务或模型已变化，请重新打开预算设置。')
            fields = self._material_budget_fields
            policy = validate_policy({'limits': {'tokens': decimal_units(fields['tokens'].value, 1),
                'microusd': decimal_units(fields['microusd'].value, 1000000)},
                'rates': {k: decimal_units(fields[k].value, 1000) for k in ('input', 'output')}})
            values = validate_settings(self.store.get_setting(SETTINGS, {}))
            values[connection_key(config)] = policy
            self.store.set_setting(SETTINGS, validate_settings(values))
        except (ValueError, AIServiceError) as exc:
            self._material_budget_status.text = str(exc); return
        self._material_budget_status.text = '已保存，将用于新批次。'

    @page_view()
    def show_ai_service_settings(self, widget=None):
        from . import ui_components as u
        value = load_connection(self.store.get_setting(CONNECTION_SETTING, {}))
        self._ai_discovery_token = object()
        self._ai_form_loading = True
        self.ai_preset_input = u.selection(items=list(PRESETS), value=value.get("preset") if value.get("preset") in PRESETS else "其他 OpenAI 兼容服务")
        self.ai_protocol_input = u.selection(items=list(PROTOCOLS.values()), value=PROTOCOLS.get(value["protocol"], PROTOCOLS["responses"]))
        self.ai_url_input = u.toga.TextInput(value=value["base_url"], placeholder="API 基础地址", style=u.Pack(margin_bottom=8))
        self.ai_auth_input = u.selection(items=list(AUTH_MODES.values()), value=AUTH_MODES.get(value["auth"], AUTH_MODES["key"]))
        self.ai_format_input = u.selection(items=list(OUTPUT_MODES.values()), value=OUTPUT_MODES.get(value["output_mode"], OUTPUT_MODES["json_schema"]))
        self.ai_model_input = u.toga.TextInput(value=value["model"], placeholder="模型 ID", style=u.Pack(margin_bottom=8))
        self.ai_tutor_model_input = u.toga.TextInput(value=value["tutor_model"], placeholder="模型 ID", style=u.Pack(margin_bottom=8))
        self.ai_model_picker = u.selection(items=[MODEL_PLACEHOLDER])
        self.ai_tutor_model_picker = u.selection(items=[MODEL_PLACEHOLDER])
        self.ai_model_picker.enabled = self.ai_tutor_model_picker.enabled = False
        self.ai_key_input = u.toga.PasswordInput(placeholder="留空可继续使用此地址已保存的密钥", style=u.Pack(margin_bottom=8))
        self.ai_key_input._feedback_sensitive = True
        # A mistyped URL might contain a key; never capture raw endpoint input.
        self.ai_url_input._feedback_sensitive = True
        self.ai_connection_label = u.label(self.ai_connection_status()["message"], size=14, color=u.MUTED)
        self.ai_discovery_label = u.label("获取列表后可选择模型，也可直接填写 ID。", size=13, color=u.MUTED)
        self.ai_fetch_button = u.button("获取此服务的模型", self._fetch_ai_models)
        self.ai_fetch_button.enabled = not getattr(self, "_ai_models_running", False)
        self.ai_preset_input.on_change = self._change_ai_preset
        for control in (self.ai_url_input, self.ai_protocol_input, self.ai_auth_input):
            control.on_change = self._change_ai_endpoint
        self.ai_model_picker.on_change = lambda widget: self._choose_ai_model(False)
        self.ai_tutor_model_picker.on_change = lambda widget: self._choose_ai_model(True)
        self._ai_form_loading = False
        children = [self.ai_connection_label, u.label("服务", size=14), self.ai_preset_input,
            u.label("API 基础地址", size=14), self.ai_url_input,
            u.label("接口类型", size=14), self.ai_protocol_input,
            u.label("认证", size=14), self.ai_auth_input, self.ai_key_input,
            self.ai_fetch_button, self.ai_discovery_label,
            u.label("语料生成模型", size=14), self.ai_model_picker, self.ai_model_input,
            u.label("实时辅导模型", size=14), self.ai_tutor_model_picker, self.ai_tutor_model_input,
            u.label("输出格式", size=14), self.ai_format_input,
            u.button("保存本机配置", self._save_ai_device_settings, primary=True),
            u.button('材料调用预算', self.show_material_budget_settings),
            u.button("移除此地址的密钥", self._remove_ai_device_key)]
        if getattr(self.platform, "_context", None) is not None:
            children.extend([u.button("OpenAI：准备从电脑安全导入", self._prepare_ai_key_import),
                u.button("OpenAI：完成安全导入", self._complete_ai_key_import)])
        children.extend([u.label("本地服务运行在电脑时，手机应填写电脑的局域网 IP；localhost 指当前设备。", size=13, color=u.MUTED),
            u.label("获取模型只发送服务凭据。生成时发送本次内容及相关学习摘要；密钥加密保存在本机，不随备份或反馈导出。云端调用按服务账户计费。", size=13, color=u.MUTED)])
        self.screen("ai_service", "AI 服务", *children)

    def _ai_form_config(self, *, require_models=True):
        def code(control, mapping):
            return next((key for key, title in mapping.items() if title == control.value), "")
        return validate_connection(dict(version=2, preset=self.ai_preset_input.value,
            protocol=code(self.ai_protocol_input, PROTOCOLS), base_url=(self.ai_url_input.value or "").strip(),
            auth=code(self.ai_auth_input, AUTH_MODES), output_mode=code(self.ai_format_input, OUTPUT_MODES),
            model=(self.ai_model_input.value or "").strip(), tutor_model=(self.ai_tutor_model_input.value or "").strip()),
            require_models=require_models)

    def _choose_ai_model(self, tutor):
        picker = self.ai_tutor_model_picker if tutor else self.ai_model_picker
        if picker.value and picker.value != MODEL_PLACEHOLDER:
            (self.ai_tutor_model_input if tutor else self.ai_model_input).value = picker.value

    def _change_ai_preset(self, widget=None):
        if self._ai_form_loading:
            return
        value = preset_config(self.ai_preset_input.value)
        self._ai_form_loading = True
        try:
            self.ai_url_input.value = value["base_url"]
            self.ai_protocol_input.value = PROTOCOLS[value["protocol"]]
            self.ai_auth_input.value = AUTH_MODES[value["auth"]]
            self.ai_format_input.value = OUTPUT_MODES[value["output_mode"]]
            self.ai_model_input.value = value["model"]
            self.ai_tutor_model_input.value = value["tutor_model"]
        finally:
            self._ai_form_loading = False
        self._change_ai_endpoint()

    def _change_ai_endpoint(self, widget=None):
        if self._ai_form_loading:
            return
        self._ai_discovery_token = object()
        self.ai_key_input.value = ""
        for picker in (self.ai_model_picker, self.ai_tutor_model_picker):
            picker.items = [MODEL_PLACEHOLDER]
            picker.enabled = False
        self.ai_fetch_button.enabled = not getattr(self, "_ai_models_running", False)
        self.ai_discovery_label.text = "服务已变更，请重新获取列表。"
        self.ai_connection_label.text = "尚未保存。仅使用此地址自己的密钥。"

    async def _fetch_ai_models(self, widget=None):
        if getattr(self, "_ai_models_running", False):
            return
        self._ai_models_running = True
        token = self._ai_discovery_token = object()
        page_token = self._page_token
        try:
            config = self._ai_form_config(require_models=False)
            raw = self.ai_key_input.value or ""
            key = normalize_token(raw, config) if raw.strip() else (self._ai_secrets(config).get() if config["auth"] == "key" else "")
            validate_token(key, config)
            self.ai_fetch_button.enabled = False
            self.ai_discovery_label.text = "正在获取模型…"
            models = await asyncio.to_thread(fetch_models, config, key)
            if token is not self._ai_discovery_token or self._page_token != page_token or self.page != "ai_service":
                return
            for picker in (self.ai_model_picker, self.ai_tutor_model_picker):
                picker.items = [MODEL_PLACEHOLDER, *models]
                picker.value = MODEL_PLACEHOLDER
                picker.enabled = bool(models)
            self.ai_discovery_label.text = (f"服务返回 {len(models)} 个模型；请选择支持文本生成及所选输出格式的模型。" if models else "服务未列出模型，可手动填写 ID。")
        except AIServiceError as exc:
            if token is self._ai_discovery_token and self._page_token == page_token:
                self.ai_discovery_label.text = str(exc) + " 可继续手动填写模型 ID。"
        except Exception:
            if token is self._ai_discovery_token and self._page_token == page_token:
                self.ai_discovery_label.text = "获取失败，可继续手动填写模型 ID。"
        finally:
            self._ai_models_running = False
            if self.page == "ai_service":
                self.ai_fetch_button.enabled = True

    def _save_ai_device_settings(self, widget=None):
        try:
            config = self._ai_form_config()
            raw = self.ai_key_input.value or ""
            key = normalize_token(raw, config) if raw.strip() else ""
            secrets = self._ai_secrets(config)
            validate_token(key or (secrets.get() if config["auth"] == "key" else ""), config)
            if key:
                secrets.put(key)
                if secrets.get() != key:
                    raise AIServiceError("credentials", "保存后未能正确读取本机密钥，请重试。")
            self.store.set_setting(CONNECTION_SETTING, config)
            self.ai_key_input.value = ""
            self.ai_connection_label.text = "已保存并验证本机读取。首次请求时会检查服务授权及模型支持。"
        except AIServiceError as exc:
            self.ai_connection_label.text = str(exc)
        except Exception:
            self.ai_connection_label.text = "配置未保存，请检查输入后重试。"

    def _remove_ai_device_key(self, widget=None):
        self.ai_key_input.value = ""
        try:
            self._ai_secrets(self._ai_form_config(require_models=False)).remove()
            self.ai_connection_label.text = "已移除此地址的本机密钥"
        except AIServiceError as exc:
            self.ai_connection_label.text = str(exc)
        except Exception:
            self.alert("无法移除密钥，请稍后重试。")

    def _prepare_ai_key_import(self, widget=None):
        try:
            self._ai_secrets(self._ai_form_config(require_models=False)).prepare_import()
            self.ai_connection_label.text = "已准备。用 USB 连接电脑运行导入助手；10 分钟内完成导入。"
        except AIServiceError as exc:
            self.ai_connection_label.text = str(exc)

    def _complete_ai_key_import(self, widget=None):
        try:
            self._ai_secrets(self._ai_form_config(require_models=False)).complete_import()
            self.ai_connection_label.text = "已安全导入 OpenAI 本机密钥"
        except AIServiceError as exc:
            self.ai_connection_label.text = str(exc)
