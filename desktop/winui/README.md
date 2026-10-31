# WinUI 3 原生界面

默认入口 `desktop/run.py` 启动本目录的 C# WinUI 3 程序。窗口沿用 1280 × 860 的逻辑尺寸，最小 980 × 700；五页导航、课程卡片、学习设置、题库与 AI 双栏、任务结果和可展开日志保持原有组织。高 DPI 使用 Windows 原生缩放。

主页面和页内分类采用滑动淡入动画，侧栏选中指示条连续移动；快速切换以最后选择为准，并遵循 Windows 的动画开关。账号连接与“我的课程”使用独立视图，成功读课后向右滑入课程，可返回账号管理；切换账号后重新连接。章节统计和任务状态采用绿色已核验、琥珀色待完成、红色失败。

界面使用 Windows App SDK 1.8.260921001，自包含、非 MSIX 部署，不要求用户另装 .NET 或 Windows App Runtime。开发机需要 Windows 10 2004（19041）或更高、x64、.NET 8 SDK；Windows SDK 构建工具通过固定版本 NuGet 包还原。[微软非打包部署说明](https://learn.microsoft.com/windows/apps/package-and-deploy/unpackage-winui-app)

## 开发运行

在 `desktop` 目录运行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
./scripts/build-winui.ps1
.venv/Scripts/python.exe run.py
```

脚本优先使用 `desktop/build/dotnet/dotnet.exe`，否则使用 PATH 中的 `dotnet`。可传 `-DotNet`、`-Python` 指定环境。默认构建到 `winui/publish/`，运行后台传输与离线 worker 测试，再启动原生五页界面验收；无交互桌面时使用 `-SkipGuiSmoke`，但这不代表完成界面验收。

也可直接运行 `winui/publish/ChaoxingDesktop.exe`：源码模式会向上查找 `desktop/run.py` 和 `.venv-build`、`.venv`、`.venv-ci`。后台不从网络下载，也不通过命令行传递凭据。

## 分发构建

```powershell
./scripts/build.ps1 -OutputDirectory dist/ChaoxingDesktop
```

默认构建流程运行全部单元回归，发布自包含 WinUI 程序，通过 `WinUIBackend.spec` 打包 Python 后台，验收冻结 worker 和原生 GUI，并记录来源哈希。输出目录包含 `ChaoxingDesktop.exe`、`ChaoxingWorker.exe`、`_internal/` 及原生运行文件，必须整体分发；后续安装包继续使用 `scripts/build_installer.py`。

只组装原生分发目录时可运行 `./scripts/build-winui.ps1 -BundleBackend -OutputDirectory dist/ChaoxingDesktop`。正式发布应使用上面的完整 `build.ps1`，生成验收与来源记录。

## 后台边界

`BackendClient.cs` 启动后台子进程，以继承的标准输入／输出交换 JSON 行，没有监听端口。`chaoxing_desktop/winui_bridge.py` 使用明确命令白名单、帧大小限制和合并更新，把现有业务状态与模型交给界面。文件选择、剪贴板、确认和人工补全由 WinUI 控件处理。原有 DPAPI、账号隔离、三类独立提交开关、提交确认、未知结果防重和设置验证继续在 Python 业务层执行。

Qt Core 及现有 Python 依赖保留在后台；这次迁移没有重写平台、题库或 AI 协议。QML 文件与既有界面回归暂留作迁移参考；仅显式传 `run.py --legacy-qml` 才启动旧界面，原生启动失败不会自动切回。

## 验证

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_winui_bridge.py -v
./scripts/run_gui_smoke.ps1 -Executable winui/publish/ChaoxingDesktop.exe -Destination build/winui-smoke -Platform windows
```

原生 GUI 验收使用独立 profile，不读取现有账号。结果写入 `build/winui-smoke/result.json`。服务真实连接与真实课程运行需要另行主动操作；离线验收不会请求这些服务。
