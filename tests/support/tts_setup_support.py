"""Shared tts setup fixtures and assertions; no collected scenarios."""
from kanamemory.platform_android import AndroidPlatform
from unittest.mock import Mock
from types import SimpleNamespace
import asyncio
from dataclasses import dataclass
import pytest
import sys


INSTALL = "android.speech.tts.engine.INSTALL_TTS_DATA"


@dataclass
class Voice:
    name: str
    language: str = "ja"
    network: bool = False
    features: set | None = None
    quality: int = 200
    country: str = "JP"

    def getLocale(self):
        return SimpleNamespace(getLanguage=lambda: self.language, getCountry=lambda: self.country,
                               toLanguageTag=lambda: f"{self.language}-{self.country}".rstrip("-"))

    def isNetworkConnectionRequired(self):
        return self.network

    def getFeatures(self):
        return None if self.features is None else SimpleNamespace(contains=self.features.__contains__)

    def getQuality(self):
        return self.quality

    def getName(self):
        return self.name


class Voices:
    def __init__(self, values):
        self.values = values

    def iterator(self):
        position = 0

        def advance():
            nonlocal position
            value = self.values[position]
            position += 1
            return value

        return SimpleNamespace(hasNext=lambda: position < len(self.values), next=advance)


class Intent:
    FLAG_ACTIVITY_NEW_TASK = 0x10000000

    def __init__(self, action):
        self.action, self.package, self.flags = action, None, 0

    def addFlags(self, flags):
        self.flags |= flags
        return self

    def setPackage(self, package):
        self.package = package
        return self


@pytest.fixture
def tts_environment(monkeypatch):
    host = SimpleNamespace(
        voices=[], engine="example.current.tts", init_status=0, availability=0,
        set_voice_status=0, selected_override=None, instances=[], intents=[],
        fail_actions=set(), initialization_snapshots=[], platform=None,
    )

    class TTS:
        SUCCESS = 0
        ERROR = -1
        LANG_AVAILABLE = 0
        LANG_MISSING_DATA = -1
        LANG_NOT_SUPPORTED = -2
        Engine = SimpleNamespace(ACTION_INSTALL_TTS_DATA=INSTALL)

        def __init__(self, context, listener):
            self.selected = None
            self.set_voice_calls, self.language_queries = [], []
            self.shutdown_count = 0
            host.instances.append(self)
            if host.platform is not None:
                host.initialization_snapshots.append((host.platform.tts_voice, host.platform.tts_engine))
            # Model a Java callback after construction, not a pre-completed Future.
            asyncio.get_running_loop().call_soon(listener.onInit, host.init_status)

        def shutdown(self):
            self.shutdown_count += 1

        def getDefaultEngine(self):
            if isinstance(host.engine, Exception):
                raise host.engine
            return host.engine

        def isLanguageAvailable(self, locale):
            self.language_queries.append(locale)
            return host.availability

        def getVoices(self):
            return None if host.voices is None else Voices(host.voices)

        def setVoice(self, voice):
            self.set_voice_calls.append(voice)
            self.selected = voice
            return host.set_voice_status

        def getVoice(self):
            return host.selected_override or self.selected

        def setLanguage(self, locale):
            raise AssertionError("Setup must not fall back by changing to a different language")

    def start_activity(intent):
        host.intents.append(intent)
        if intent.action in host.fail_actions:
            raise RuntimeError("No activity for " + intent.action)
        # Launch success says nothing about whether the user installs or cancels.
        return None

    context = SimpleNamespace(startActivity=start_activity)
    context.getApplicationContext = lambda: context
    japan = object()
    classes = {
        "android.speech.tts.TextToSpeech": TTS,
        "android.speech.tts.TextToSpeech$OnInitListener": object,
        "android.speech.tts.TextToSpeech$Engine": TTS.Engine,
        "java.util.Locale": SimpleNamespace(JAPAN=japan),
        "android.content.Intent": Intent,
        "android.util.Log": SimpleNamespace(i=Mock()),
    }
    monkeypatch.setitem(sys.modules, "java", SimpleNamespace(jclass=classes.__getitem__, dynamic_proxy=lambda interface: object))
    platform = AndroidPlatform(context)
    platform.stop = Mock()
    platform._configure_audio_callbacks = Mock()
    host.platform, host.japan, host.TTS = platform, japan, TTS
    return platform, host
