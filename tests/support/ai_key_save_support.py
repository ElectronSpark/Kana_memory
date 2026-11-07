"""Shared ai key save fixtures and assertions; no collected scenarios."""
from kanamemory.ai_service import AIServiceError


FAKE = 'sk-unit-test-' + 'A1_-b2' * 25


class Vault:
    def __init__(self, value=''): self.value=value; self.fail=False
    def get(self): return self.value
    def put(self, value):
        if self.fail: raise AIServiceError('credentials','无法安全保存 AI 连接，请重试。')
        self.value=value
    def remove(self): self.value=''
