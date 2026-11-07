Kana Memory: clean source and original demo

This distribution contains the application engine and a small, separately stored,
newly authored demo. It has no private dictionary, course corpus, imported audio,
research notes, personal progress, credentials, or Git history. The demo is not a
complete course. Network access is only needed to install build dependencies and,
optionally, Android handwriting models. No AI service is needed for local study.

LICENSING
The application code and original demo are available under the MIT license in
LICENSE. See LICENSE-SCOPE.txt for the exact boundary. Dependencies and separate
personal content packages retain their own licenses and NOTICE requirements.
This grant does not relicense third-party material in earlier repository history.
Demo material has no copied third-party dictionary definitions, example corpus,
voice recordings or fonts.

BUILD ON WINDOWS
Use CPython 3.13.16 and the declared Windows/Android environment in
docs/build-environment.txt. In a fresh copy, run PowerShell from its root:

  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r environment\windows-py313.lock.txt
  .\.venv\Scripts\python.exe tools\build_environment.py --host
  .\.venv\Scripts\python.exe tools\build_demo_content.py
  .\.venv\Scripts\python.exe tools\check_public_content.py app\src\kanamemory\resources --source demo\content.json
  .\.venv\Scripts\python.exe -m pytest tests\test_public_distribution.py -q
  .\environment\build_public_app.ps1 -Stage create
  .\environment\build_public_app.ps1 -Stage build
  .\environment\build_public_app.ps1 -Stage package

The default short ASCII toolchain path is %TEMP%\km. A different path may be
selected with -ToolchainRoot C:\km-demo. Each stage must use the same path. The
demo uses app-public within that path, separate from personal builds. A prepared
Python environment can be selected explicitly with -Python <python.exe>.
The APK is written to environment\artifacts. The package ID is
org.example.demo.kanamemory, so installing the demo cannot overwrite the personal
org.example.kanamemory installation or its learning records. A demo source tree
automatically enables strict demo checks even if run_app.ps1 is invoked directly.

The lock includes quality tools. Run python -X utf8 tools/check_python.py from
any directory for bounded engineering checks. The same command is
used by the included Windows CI. It needs no private corpus, API or emulator.
See docs/python-engineering.txt for the developer contract and test layers.

To run on an existing emulator, pass its exact adb serial to the install and
launch stages, for example -Serial emulator-5554. These scripts refuse physical
phone installation. The desktop stage opens a desktop preview.

CONTENT BOUNDARY
demo/content.json is the complete, editable demo source. Its stdlib builder
creates content.sqlite3 (read-only lexical/kana data), teaching_content.sqlite3
(read-only lesson namespaces) and a resource checksum manifest. App logic never
imports private content-building scripts. Rebuild content into an empty/new
resources directory; the builder refuses to delete or replace another pack.
Public packaging rejects private profiles, extra resources, checksum changes,
and an APK with stale private resources left by an earlier build.
To distribute the demo data independently of the engine, use:

  .\.venv\Scripts\python.exe tools\package_content_bundle.py app\src\kanamemory\resources demo-content.zip

This bundle is data only, contains its own checksums and notice, and does not
include executable application code or learner databases.

Learner progress, pending questions, drafts, AI-created local material and the
device API key belong to the runtime user's data storage, never a content pack
or source export. The optional AI UI starts unconfigured. No key is distributed.

The teaching harness preserves formal schedules and unfinished tasks. Reliable
supplemental evidence has bounded interval effects; model dialogue does not
directly edit FSRS values. Current tutoring notes can be corrected or removed.
Only bounded relevant evidence and sent messages enter AI requests. Unsent
drafts stay local. See teaching_harness_0260.txt and first_teaching_fairness_0260.txt
for the policy, backup version, engineering defaults and validation limits.

EXPORTING FROM THE DEVELOPMENT PROJECT
tools/export_clean_source.py <new-directory> --archive <new-file.zip> uses only
tools/public_source_files.txt. It does not clone or copy any Git directory.
The archive has stable ordering/timestamps, and clean-source-manifest.json lists
the SHA-256 of every exported source file. Review the explicit allowlist when
adding engine files. No private corpus restoration command is part of this build.
Allowlisted .txt/.md documents may wrap development-only passages between lines
that contain only [private] and [/private]; the export drops them, and lines that
start with "[public-only] " appear only in the export. Unbalanced markers or a
personal delivery location left in a public document stop the export.

This Windows recipe is the verified target. Other Briefcase platforms may be
usable but are not asserted to have been validated by this distribution.
