# KanaMemory

**中文** · [English](README.en.md)

KanaMemory 是一个用 Python 编写的 Android 日语学习 App，覆盖**假名、词汇、语法和读音规则**。它以离线学习为主：首次学习、连续课程、多种题型练习，再加上基于 [FSRS](https://github.com/open-spaced-repetition) 的间隔复习；AI 辅助是可选的，不配置也能完整使用。

> 本仓库是 MIT 许可的**干净源码发布**：包含 App 引擎、构建工具和一套原创的小型 demo 内容，不包含私用词典、课程语料、导入音频、个人学习记录、密钥或开发历史。demo 只用于演示和构建验证，不是完整课程。

## 功能

- **假名**：平假名/片假名完整表，看假名选罗马音、写罗马音、按罗马音选或拼假名、听音或看罗马音**手写假名**（Google ML Kit 手写识别）、听词拼词等。
- **词汇**：听词选词、看词选读音、辨义、中日词义对比、听词辨义、写读法、按含义回忆、选写法、语境用法、短文与短句听力理解。
- **语法**：含义判断、填空（含一句多空）、变形、词块重排、改错、有约束造句；支持罗马音输入，不需要日文输入法。
- **读音规则**：规则理解与应用，音高标注与音高填写/识别题，长句语流轮廓示意。
- **复习调度**：FSRS 间隔复习，按“内容 × 能力”记忆；每轮练习开始时固定题单，回首页可续练，评分逐次保存、幂等不重复计分。
- **防泄题与反馈**：答题前的帮助不暴露答案；答后给出正确答案、原回答、简短原因和朗读（系统日语 TTS）。
- **学习数据**：记录和密钥只保存在设备的用户数据区；支持备份导出、旧版记录转换。
- **可选 AI**：AI 讲解、出题、材料核对和反馈修订，可配置多个服务和模型；AI 不能直接改写复习参数，也不会自动加入内容。

## 环境要求

- Windows x86-64（当前唯一验证过的构建平台）
- CPython **3.13.16**
- Briefcase 0.4.5，目标 Android arm64-v8a / x86_64，最低 Android 8.0（API 26）

完整的环境约定和锁定版本见 [docs/build-environment.txt](docs/build-environment.txt)。

## 快速开始

在仓库根目录用 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r environment\windows-py313.lock.txt
.\.venv\Scripts\python.exe tools\build_environment.py --host

# 生成 demo 内容并检查
.\.venv\Scripts\python.exe tools\build_demo_content.py
.\.venv\Scripts\python.exe tools\check_public_content.py app\src\kanamemory\resources --source demo\content.json

# 构建 demo APK
.\environment\build_public_app.ps1 -Stage create
.\environment\build_public_app.ps1 -Stage build
.\environment\build_public_app.ps1 -Stage package
```

- APK 输出到 `environment\artifacts`，包名为 `org.example.demo.kanamemory`，与个人版分开，安装 demo 不会覆盖个人版的学习记录。
- 工具链默认放在 `%TEMP%\km`，可用 `-ToolchainRoot C:\km-demo` 换成其他较短的纯英文路径；每个阶段要用同一路径。
- 在模拟器上运行时，给安装和启动阶段传入 adb 序列号，例如 `-Serial emulator-5554`。构建脚本不会安装到实体手机。

更详细的构建说明（英文）见 [docs/public-build.txt](docs/public-build.txt)。

## 开发与检查

```powershell
python -X utf8 tools/check_python.py
```

这条命令依次运行 ruff、架构边界检查、构建环境检查和一组有限的 pytest 契约测试，不需要私用语料、API 或模拟器；GitHub Actions 运行的也是它。

| 目录 | 内容 |
|---|---|
| `app/src/kanamemory/` | App 引擎（Toga UI、学习调度、题目、存储、AI 接口） |
| `app/native/` | Android 原生组件 |
| `demo/content.json` | 原创 demo 内容的完整可编辑源文件 |
| `tools/` | 内容构建、检查、导出与打包工具 |
| `tests/` | 测试，共享测试工具在 `tests/support/` |
| `environment/` | 构建、模拟器和环境脚本、依赖锁 |
| `docs/` | 设计规范、工程结构和各版本说明 |

工程原则见 [docs/python-engineering.txt](docs/python-engineering.txt)，模块职责和依赖方向见 [docs/design_current/07_engineering_structure.txt](docs/design_current/07_engineering_structure.txt)。

## 许可

App 代码、项目自己编写的构建工具和文档，以及 `demo/content.json` 里的原创 demo 内容，采用 [MIT 许可](LICENSE)。第三方依赖、Android 组件、字体和模型保留各自的许可；私用内容包、词典和语料不在 MIT 授权范围内。确切边界见 [LICENSE-SCOPE.txt](LICENSE-SCOPE.txt)。
