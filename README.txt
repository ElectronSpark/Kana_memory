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

The default short ASCII toolchain path is %LOCALAPPDATA%\km (never a path in the
temporary directory: the system deletes old files there, and the scripts refuse
it). A different path may be selected with -ToolchainRoot C:\km-demo. Each stage
must use the same path. The demo uses app-public within that path, separate from
personal builds. A prepared Python environment can be selected explicitly with
-Python <python.exe>.
The create stage writes the pinned Android template commit from Briefcase's
template cache into ToolchainRoot\briefcase\pinned-template and creates the
project from that directory (see "How create gets the pinned commit" in
docs/build-environment.txt). It needs the network only when the cache lacks
that commit. create does not replace an existing generated project: remove the
project's build\kanamemory\android directory within app-public (or app) first.
The APK is written to environment\artifacts. The package ID is
org.example.demo.kanamemory, so installing the demo cannot overwrite a personal
installation (another package ID) or its learning records. A demo source tree
automatically enables strict demo checks even if run_app.ps1 is invoked directly.

PERSONAL PACKAGE FROM THIS TREE
A personal package is built from this same public code: the content and the
package identity are given as data from outside the tree, never as a second
edition of the code. Both are needed; without them this tree builds the demo.

  .\environment\build_personal_app.ps1 -Stage create -IdentityFile <file> -ContentRoot <dir>
  .\environment\build_personal_app.ps1 -Stage build -IdentityFile <file> -ContentRoot <dir>
  .\environment\build_personal_app.ps1 -Stage package -IdentityFile <file> -ContentRoot <dir>

<dir> is a complete resources directory (content.sqlite3, teaching_content.sqlite3
of the personal profile, and the rest of a content pack). <file> is a small JSON
file kept outside the tree, for example:

  {"format": "kanamemory-build-identity-v1", "profile": "personal",
   "bundle": "org.example.yourname", "formal_name": "YourAppName"}

The package ID is bundle + ".kanamemory"; formal_name names the APK file. The
script prints the identity and the content it builds with; it writes the identity
only into the build copy in the toolchain (app within ToolchainRoot, shared with
the desktop run of a development tree), never into this tree, and refuses the
demo and QA package IDs, a file or a content directory inside the tree, and
one of the two without the other. An APK installs over an existing installation
only when it has the same package ID and the same signing certificate; debug
builds are signed with the build machine's debug key. Audit the result with
environment\verify_distribution_apk.py --profile personal --identity-file <file>
--app-source <ToolchainRoot>\app (the build copy, which holds the applied identity
and the content), then apksigner and zipalign as for any package.

app/pyproject.toml exists in one edition, this one, with the demo identity; no
tree holds a second edition of it. A development tree (a working tree without
clean-source-manifest.json) builds no package: run_app.ps1 refuses every stage
there but desktop, before any build step. To build work that is not committed
yet, export a clean tree from the development tree first and build that export:

  .\.venv\Scripts\python.exe tools\export_clean_source.py <new directory>
  <new directory>\environment\build_personal_app.ps1 -Stage create -IdentityFile <file> -ContentRoot <dir> -Python <python.exe>

(-Python names the development tree's .venv\Scripts\python.exe, since an export
has no environment of its own; then build and package as above.) The desktop
stage of a development tree applies the private identity file
config\personal_identity.json (same format) to the build copy: Toga derives the
learner data directory from formal_name (and author), so a desktop run keeps
reading the same learner record. Without that file the desktop stage refuses.

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
app/pyproject.toml is copied unchanged; any identity other than the demo stops
the export.
Allowlisted .txt/.md documents may wrap development-only passages between lines
that contain only [private] and [/private]; the export drops them, and lines that
start with "[public-only] " appear only in the export. Unbalanced markers or a
personal delivery location left in a public document stop the export.

This Windows recipe is the verified target. Other Briefcase platforms may be
usable but are not asserted to have been validated by this distribution.
