# KanaMemory

[中文](README.md) · **English**

KanaMemory is an Android app for learning Japanese, written in Python. It covers **kana, vocabulary, grammar and pronunciation rules**. It is offline-first: guided first lessons, continuous courses, many question types and spaced repetition based on [FSRS](https://github.com/open-spaced-repetition). AI assistance is optional; the app is fully usable without it.

> This repository is the MIT-licensed **clean source release**: the app engine, build tools and a small, newly authored demo. It contains no private dictionary, course corpus, imported audio, personal learning records, credentials or development history. The demo is for demonstration and build verification only; it is not a complete course.

## Features

- **Kana**: full hiragana/katakana tables; choose or type romaji for kana, choose or assemble kana from romaji, **handwrite kana** from audio or romaji (Google ML Kit handwriting recognition), spell words from audio, and more.
- **Vocabulary**: hearing a word and choosing it, choosing a reading, meaning, Chinese–Japanese meaning contrast, listening for meaning, readings, recall from meaning, orthography, usage in context, and short reading/listening comprehension.
- **Grammar**: meaning, cloze (including several blanks in one sentence), transformation, word-block reordering, targeted correction and constrained sentence production. Romaji input works everywhere, so no Japanese IME is required.
- **Pronunciation rules**: understanding and applying rules, pitch-accent marking, pitch fill/identify questions, and contour cues for longer phrases.
- **Scheduling**: FSRS spaced repetition per content × ability. Each practice round fixes its question list at the start, can be resumed from the home page, and saves every rating idempotently.
- **No answer leaks, clear feedback**: help before answering never reveals the answer; afterwards you see the correct answer, your answer, a short reason and speech via the system Japanese TTS.
- **Your data stays on the device**: learning records and API keys live in the app's user data area; backups can be exported and old record formats converted.
- **Optional AI**: explanations, generated questions, material checks and feedback revision, with several providers and models. AI cannot edit scheduling parameters directly or add content on its own.

## Requirements

- Windows x86-64 (the only verified build platform)
- CPython **3.13.16**
- Briefcase 0.4.5, targeting Android arm64-v8a / x86_64, minimum Android 8.0 (API 26)

The full environment contract and pinned versions are in [docs/build-environment.txt](docs/build-environment.txt).

## Quick start

From the repository root, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r environment\windows-py313.lock.txt
.\.venv\Scripts\python.exe tools\build_environment.py --host

# Build and check the demo content
.\.venv\Scripts\python.exe tools\build_demo_content.py
.\.venv\Scripts\python.exe tools\check_public_content.py app\src\kanamemory\resources --source demo\content.json

# Build the demo APK
.\environment\build_public_app.ps1 -Stage create
.\environment\build_public_app.ps1 -Stage build
.\environment\build_public_app.ps1 -Stage package
```

- The APK is written to `environment\artifacts`. Its package ID is `org.example.demo.kanamemory`, separate from the personal build, so installing the demo never overwrites personal learning records.
- The toolchain defaults to `%TEMP%\km`; choose another short ASCII path with `-ToolchainRoot C:\km-demo` and use the same path for every stage.
- To run on an emulator, pass its adb serial to the install and launch stages, e.g. `-Serial emulator-5554`. The build scripts refuse to install on a physical phone.

Detailed build notes: [docs/public-build.txt](docs/public-build.txt).

## Development and checks

```powershell
python -X utf8 tools/check_python.py
```

This runs ruff, the architecture boundary check, the build-environment check and a bounded set of pytest contract tests. It needs no private corpus, API or emulator, and it is what GitHub Actions runs.

| Path | Contents |
|---|---|
| `app/src/kanamemory/` | App engine (Toga UI, scheduling, questions, storage, AI interfaces) |
| `app/native/` | Android native components |
| `demo/content.json` | Complete editable source of the original demo content |
| `tools/` | Content building, checking, export and packaging tools |
| `tests/` | Tests; shared helpers live in `tests/support/` |
| `environment/` | Build, emulator and environment scripts, dependency locks |
| `docs/` | Design specifications, engineering structure and release notes |

Engineering principles: [docs/python-engineering.txt](docs/python-engineering.txt). Module responsibilities and dependency directions: [docs/design_current/07_engineering_structure.txt](docs/design_current/07_engineering_structure.txt).

## License

The app code, project-authored build tools and documentation, and the original demo in `demo/content.json` are released under the [MIT license](LICENSE). Third-party dependencies, Android components, fonts and models keep their own licenses; personal content packages, dictionaries and corpora are not covered by the MIT grant. See [LICENSE-SCOPE.txt](LICENSE-SCOPE.txt) for the exact boundary.
