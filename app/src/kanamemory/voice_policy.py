"""Conservative locale policy for system TTS demonstrations, not dictionary audio.

Android Voice.getLocale() is authoritative; names are engine-specific opaque IDs.
zh-HK does not identify Cantonese. Only an explicit yue locale in HK is accepted.
"""
from dataclasses import asdict, dataclass


VOICE_LABELS = {"ja": "日语", "cmn": "普通话", "yue": "香港粤语"}


def chinese_language(language):
    if language not in {"cmn", "yue"}:
        raise ValueError("Chinese voice language must be 'cmn' or 'yue'.")
    return language


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    language: str
    country: str
    locale: str
    quality: int
    network_required: bool
    not_installed: bool

    def supports(self, requested):
        if self.network_required or self.not_installed:
            return False
        language, country = self.language.lower(), self.country.upper()
        if requested == "ja":
            return language == "ja"
        if requested == "cmn":
            return language in {"cmn", "zh"} and country == "CN"
        if requested == "yue":
            return language == "yue" and country == "HK"
        return False

    def to_dict(self):
        return asdict(self)


def describe_voice(voice):
    locale = voice.getLocale()
    features = voice.getFeatures()
    return VoiceProfile(
        name=str(voice.getName()), language=str(locale.getLanguage()),
        country=str(locale.getCountry()), locale=str(locale.toLanguageTag()),
        quality=int(voice.getQuality()),
        network_required=bool(voice.isNetworkConnectionRequired()),
        not_installed=bool(features is not None and features.contains("notInstalled")),
    )


def rank_voices(voices, requested):
    choices = [(describe_voice(voice), voice) for voice in voices]
    return sorted(((profile, voice) for profile, voice in choices if profile.supports(requested)),
                  key=lambda entry: (-entry[0].quality, entry[0].name))
