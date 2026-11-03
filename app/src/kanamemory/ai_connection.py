"""Endpoint-scoped credentials and bounded, stateless protocol adapters.

Model discovery describes server availability, never pedagogical trust or model
capabilities. All providers enter the same local material/tutor validators.
"""
from copy import deepcopy
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit, urlencode

from .ai_service import AIServiceError, DEFAULT_MODEL, OpenAIProvider, _get, _post, validate_api_key

OPENAI_URL = "https://api.openai.com/v1"
PROTOCOLS = {"responses": "Responses", "chat": "OpenAI 兼容 Chat Completions",
             "anthropic": "Claude Messages", "ollama": "Ollama 原生"}
OUTPUT_MODES = {"json_schema": "JSON Schema", "json_object": "JSON 对象", "prompt": "提示词约束 JSON"}
AUTH_MODES = {"key": "使用密钥", "none": "无需密钥（本地）"}
# Defaults describe protocols, not a hardcoded list of available models.
PRESETS = {
    "OpenAI": ("responses", OPENAI_URL, "json_schema", "key"),
    "其他 OpenAI 兼容服务": ("chat", "", "json_object", "key"),
    "DeepSeek": ("chat", "https://api.deepseek.com", "json_object", "key"),
    "Gemini": ("chat", "https://generativelanguage.googleapis.com/v1beta/openai", "json_schema", "key"),
    "Claude": ("anthropic", "https://api.anthropic.com/v1", "json_schema", "key"),
    "Ollama（本地）": ("ollama", "http://localhost:11434", "json_schema", "none"),
    "LM Studio（本地）": ("chat", "http://localhost:1234/v1", "json_schema", "none"),
}


def preset_config(name):
    protocol, url, output, auth = PRESETS[name]
    from .tutor_service import DEFAULT_TUTOR_MODEL
    return dict(version=2, preset=name, protocol=protocol, base_url=url, output_mode=output,
                auth=auth, model=DEFAULT_MODEL if name == "OpenAI" else "",
                tutor_model=DEFAULT_TUTOR_MODEL if name == "OpenAI" else "")


def load_connection(value):
    """Read the old setting without changing learning data or moving secrets."""
    value = value if isinstance(value, dict) else {}
    config = preset_config("OpenAI")
    if value.get("version", 1) == 1:
        config.update({k: value[k] for k in ("model", "tutor_model") if k in value})
    else:
        config.update({k: value[k] for k in config if k in value})
    return config


def _local_host(host):
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or any(ip in ipaddress.ip_network(net) for net in
            ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
    except ValueError:
        return False


def validate_connection(value, *, require_models=True):
    config = load_connection(value)
    if any(not isinstance(config[field], str) or config[field] not in values
           for field, values in (("protocol", PROTOCOLS), ("output_mode", OUTPUT_MODES), ("auth", AUTH_MODES))):
        raise AIServiceError("configuration", "请选择支持的接口类型、输出格式和认证方式。")
    url = config["base_url"]
    try:
        if not isinstance(url, str) or len(url) > 500 or not url.isascii() or re.search(r"[\s\\]", url):
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.port == 0
                or not re.fullmatch(r"[A-Za-z0-9._/-]*", parsed.path)
                or any(part in {".", ".."} for part in parsed.path.split("/"))):
            raise ValueError()
        if parsed.scheme == "http" and not _local_host(parsed.hostname):
            raise ValueError()
        if config["auth"] == "none" and not _local_host(parsed.hostname):
            raise ValueError()
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parsed.port
        authority = host + (":" + str(port) if port and (parsed.scheme, port) not in {("http", 80), ("https", 443)} else "")
        config["base_url"] = parsed.scheme + "://" + authority + parsed.path.rstrip("/")
        if parsed.path.rstrip("/").endswith(("/responses", "/chat/completions", "/messages", "/models", "/api/chat", "/api/tags")):
            raise ValueError()
    except (ValueError, TypeError):
        raise AIServiceError("endpoint", "请填写 API 基础地址，不含密钥、查询参数或具体请求路径。云端使用 HTTPS；本地 HTTP 请用 localhost 或局域网 IP。") from None
    if config["protocol"] == "anthropic" and config["output_mode"] == "json_object":
        raise AIServiceError("configuration", "Claude 原生接口请选择 JSON Schema 或提示词约束 JSON。")
    if require_models:
        for field in ("model", "tutor_model"):
            validate_model_id(config[field])
    return config


def validate_model_id(model):
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}", model):
        raise AIServiceError("model", "请获取并选择模型，或填写服务支持的模型 ID。")
    return model


def credential_slot(config):
    config = validate_connection(config, require_models=False)
    if config["base_url"] == OPENAI_URL and config["protocol"] in {"responses", "chat"}:
        return "openai_api_key"
    identity = config["protocol"] + "\n" + config["base_url"]
    return "endpoint_" + hashlib.sha256(identity.encode()).hexdigest()


def validate_token(token, config):
    if config["auth"] == "none":
        return ""
    if credential_slot(config) == "openai_api_key":
        return validate_api_key(token)
    if not isinstance(token, str) or not token:
        raise AIServiceError("credentials", "尚未填写此服务的密钥。")
    if not re.fullmatch(r"[!-~]{1,2048}", token):
        raise AIServiceError("credentials", "密钥格式不正确，请只粘贴此服务的密钥。")
    return token


def normalize_token(raw, config):
    if config["auth"] == "none":
        if raw.strip():
            raise AIServiceError("credentials", "填写了密钥，请先将认证方式改为“使用密钥”。")
        return ""
    if credential_slot(config) == "openai_api_key":
        from .ai_credentials import normalize_pasted_api_key
        return normalize_pasted_api_key(raw)
    # Other providers' keys need not use OpenAI's prefix. No env evaluation or
    # removal of internal whitespace (which could join two different keys).
    return validate_token(raw.strip(), config)


def _headers(config):
    return {"anthropic-version": "2026-10-21"} if config["protocol"] == "anthropic" else {}


def fetch_models(config, token="", *, transport=_get):
    config = validate_connection(config, require_models=False)
    token = validate_token(token, config)
    path = "/api/tags" if config["protocol"] == "ollama" else "/models"
    cursor, seen, models = "", set(), set()
    for page in range(5):
        suffix = ("?" + urlencode({"limit": 200, **({"after_id": cursor} if cursor else {})})
                  if config["protocol"] == "anthropic" else "")
        result = transport(config["base_url"] + path + suffix, token=token or None,
                           timeout=12, extra_headers=_headers(config))
        rows = result.get("models" if config["protocol"] == "ollama" else "data") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) > 1000:
            raise AIServiceError("models", "模型列表格式不受支持，可手动填写模型 ID。")
        for row in rows:
            model = (row.get("name") or row.get("model")) if config["protocol"] == "ollama" and isinstance(row, dict) else row.get("id") if isinstance(row, dict) else None
            try:
                models.add(validate_model_id(model))
            except AIServiceError:
                continue
        if len(models) > 1000:
            raise AIServiceError("models", "模型列表过大，请手动填写模型 ID。")
        if config["protocol"] != "anthropic" or not result.get("has_more"):
            return sorted(models, key=str.casefold)
        cursor = result.get("last_id")
        if not isinstance(cursor, str) or not cursor or len(cursor) > 200 or cursor in seen:
            break
        seen.add(cursor)
    raise AIServiceError("models", "模型列表分页过多或无效，可手动填写模型 ID。")


def _envelope(text, usage):
    if not isinstance(text, str) or not text.strip():
        raise AIServiceError("response_format", "模型未返回有效 JSON，可更换模型或输出格式后重试。")
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}], "usage": usage}


class ConfiguredProvider(OpenAIProvider):
    """Keep app-owned prompts and validation, adapting only wire protocols."""
    def __init__(self, config, token, *, tutor=False, transport=_post):
        self.config = validate_connection(config)
        self._key = validate_token(token, self.config)
        self.model = self.config["tutor_model" if tutor else "model"]
        self._transport = transport
        self.init_request_state()

    def _request(self, body, *, timeout=100):
        body = deepcopy(body)
        protocol, mode = self.config["protocol"], self.config["output_mode"]
        spec = body["text"]["format"]
        schema = spec["schema"]
        instructions = body["instructions"]
        if mode != "json_schema":
            instructions += "\nReturn only one JSON object, no Markdown. Follow this JSON schema:\n" + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        user_text = body["input"] if isinstance(body["input"], str) else body["input"][0]["content"][0]["text"]
        messages = [{"role": "system", "content": instructions}, {"role": "user", "content": user_text}]
        if protocol == "responses":
            path, payload = "/responses", body
            payload["instructions"] = instructions
            if self.config["base_url"] != OPENAI_URL or not re.match(r"^(gpt-[56][.-]|o[1-9](?:-|$))", self.model):
                payload.pop("reasoning", None)
            if mode == "json_object":
                payload["text"] = {"format": {"type": "json_object"}}
            elif mode == "prompt":
                payload.pop("text", None)
        elif protocol == "chat":
            path = "/chat/completions"
            payload = {"model": self.model, "messages": messages, "stream": False, "max_tokens": body["max_output_tokens"]}
            if self.config["base_url"] == OPENAI_URL:
                payload["max_completion_tokens"] = payload.pop("max_tokens")
            if mode == "json_schema":
                payload["response_format"] = {"type": "json_schema", "json_schema": {k: spec[k] for k in ("name", "strict", "schema")}}
            elif mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
        elif protocol == "anthropic":
            path = "/messages"
            payload = {"model": self.model, "system": instructions, "messages": messages[1:],
                       "stream": False, "max_tokens": min(body["max_output_tokens"], 8192)}
            if mode == "json_schema":
                payload["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        else:
            path = "/api/chat"
            payload = {"model": self.model, "messages": messages, "stream": False,
                       "options": {"num_predict": body["max_output_tokens"]}}
            if mode != "prompt":
                payload["format"] = schema if mode == "json_schema" else "json"
        if self.config['base_url'] == OPENAI_URL and protocol in {'responses', 'chat'}:
            # Numeric material estimates use Standard rates, not account-default
            # priority processing. Other compatible servers receive no extra key.
            payload['service_tier'] = 'default'
        response = self._transport(self.config["base_url"] + path, payload, token=self._key or None,
                                   timeout=timeout, extra_headers=_headers(self.config))
        if not isinstance(response, dict) or response.get("error"):
            raise AIServiceError("response_format", "服务未返回有效结果，请检查模型及接口设置。")
        if protocol == "responses":
            return response
        usage = response.get("usage", {})
        usage = usage if isinstance(usage, dict) else {}
        if protocol == "chat":
            choices = response.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise AIServiceError("response_format", "模型回答格式不正确。")
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict):
                raise AIServiceError("response_format", "模型回答格式不正确。")
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise AIServiceError("refusal", "模型未回答这次请求。")
            if choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("function_call"):
                raise AIServiceError("incomplete", "模型回答未完成；未添加内容，可减少数量或更换模型。")
            mapped = {out: usage.get(src) for out, src in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens"))}
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                mapped["input_tokens_details"] = {"cached_tokens": details.get("cached_tokens")}
            return _envelope(message.get("content"), mapped)
        if protocol == "anthropic":
            if response.get("stop_reason") == "refusal" or (isinstance(response.get("stop_details"), dict) and response["stop_details"].get("type") == "refusal"):
                raise AIServiceError("refusal", "模型未回答这次请求。")
            if response.get("stop_reason") != "end_turn":
                raise AIServiceError("incomplete", "模型回答未完成；未添加内容，可减少数量或更换模型。")
            parts = response.get("content")
            if not isinstance(parts, list) or any(not isinstance(p, dict) or p.get("type") not in {"text", "thinking", "redacted_thinking"} for p in parts):
                raise AIServiceError("response_format", "模型回答格式不正确。")
            mapped = dict(usage)
            # Anthropic separates cached input from fresh input; account for all.
            counts = [usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
            if all(type(v) is int and v >= 0 for v in counts):
                mapped["input_tokens"] = sum(counts)
            mapped["input_tokens_details"] = {"cached_tokens": usage.get("cache_read_input_tokens")}
            return _envelope("".join(p.get("text", "") for p in parts if p["type"] == "text" and isinstance(p.get("text"), str)), mapped)
        message = response.get("message")
        if response.get("done") is not True or response.get("done_reason") != "stop":
            raise AIServiceError("incomplete", "本地模型回答未完成，可减少数量后重试。")
        if not isinstance(message, dict) or message.get("tool_calls"):
            raise AIServiceError("response_format", "本地模型回答格式不正确。")
        return _envelope(message.get("content"), {"input_tokens": response.get("prompt_eval_count"), "output_tokens": response.get("eval_count")})
