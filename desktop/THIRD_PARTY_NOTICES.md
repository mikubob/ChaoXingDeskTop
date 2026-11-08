# 来源与许可

验证码 OCR 精简：`vendor/chaoxing/api/ocr.py` 直接使用 Pillow、NumPy、ONNX Runtime CPU 完成预处理与推理，替代 ddddocr Python 运行入口。模型仍取自锁定的 ddddocr 1.6.1 wheel 的 `common_old.onnx`；`resource/ocr_charset.json` 提取自该版本 `models/charset_manager.py` 的 `_get_old_charset` 常量，顺序保持不变。项目来源 https://github.com/sml2h3/ddddocr ，MIT 许可证原文保留于 `vendor/chaoxing/resource/OCR-LICENSE.txt` 及分发依赖元数据。发行后台不包含 OpenCV；构建环境仍保留完整上游包，以提供原始模型与结果对照。

桌面版：ChaoxingDesktop 0.4.2，GPL-3.0。完整许可见 LICENSE。

0.4.2 合入 PR #1 的独立浏览器登录，使用既有 PySide6 的 QtWebSockets 与本机 Edge / Chrome 的本地调试接口获取 Cookie；不分发浏览器，不新增第三方依赖。其余来源与许可沿用 0.4.1。

0.4.1 独立实现填空布局与逐空校验、来源分隔符声明、保存诊断和只读核验增量刷新／进度／失败恢复；平台适配改动位于 api/base.py、api/decode.py、api/activity.py、api/discussion.py、api/responses.py。移除无调用的列表快照、重复章节索引与无用导入；未新增第三方代码或依赖。

0.4.0 界面修订 1：独立实现通用操作弹框、语义按钮和分操作提示，改进空态／失败／未保存反馈；未新增依赖或第三方代码，未修改 vendor 协议。

0.4.0 独立实现待处理中心、只读核验、启动策略摘要、排障导出、运行摘要、DPAPI AI 档案、AI 用量控制和本地人工副本，改善 QML 界面与容量提示；删除无调用的旧副本答案查找函数。未新增第三方源码或运行依赖，vendor 协议源码未变。副本没有独立提交入口。

0.3.9 新增独立实现的投票／问卷策略、逐题验证、草稿与提交意图存储、QML 设置及记录界面；平台适配改动位于 api/activity.py、api/decode.py 与 api/base.py。协议与支持边界见 [ACTIVITY_PROTOCOL.md](vendor/chaoxing/api/ACTIVITY_PROTOCOL.md)。删除无调用方的限速副本与字体原始 HTML 成员，未新增运行依赖。

0.3.8 新增独立实现的讨论回复策略、持久化发布去重及 QML 界面，平台协议适配位于 api/discussion.py；协议与支持边界见 [DISCUSSION_PROTOCOL.md](vendor/chaoxing/api/DISCUSSION_PROTOCOL.md)。未新增运行依赖。

0.3.7 同版本滚动修订：课程列表、题库与 AI 和设置表单共用独立实现的 QML 滚动组件，统一为每格 136 像素；保留触控板像素滚动，新增服务页真实滚轮事件验收。未新增依赖或第三方源码。

0.3.7 补全音频与资源类型识别、校验媒体/文档/阅读响应并隔离各任务卡参数；课程滚轮步幅降低 15%；删除已被桌面路由替代的旧模糊匹配、答案分段模块与无用导入，调整测试等待补丁。未新增运行依赖，来源与独立实现说明见下文。

0.3.6 修正同一账号任务重启时的 Cookie 文件版本同步，统一下拉框与课程按钮样式并调整滚轮步幅；清理通知模块的旧 INI 入口、题库无效参数、无用成员、隐藏控件及图标变体。未新增运行依赖，保留既有配置迁移、通知服务和平台协议兼容逻辑。

0.3.5 精简发行资源：ddddocr 1.6.1 保持原默认模型 `common_old.onnx`，仅删除未使用的 beta / 检测模型；OpenCV 保留图像处理，移除未使用的视频 I/O 插件。Qt 按当前 QML 导入及原生依赖闭包收集，保留开源依赖元数据和许可。移除未调用的姓名查询、无读取方的重做计数与题库展示元数据；未新增运行依赖，未改变媒体结束上报协议。

0.3.2 独立实现本机多账号管理、Windows DPAPI Cookie 加密与迁移、账号切换和会话身份校验；api/cookies.py 适配独立账号存储，api/base.py 在密码登录成功后清理旧会话 Cookie。未新增第三方依赖。

0.3.0 根据 OCSJS 借鉴计划独立实现统一查询结果、逐题诊断、命名空位校验、协作停止与暂停后续调度、受限声明式 JSON 题库、配置格式迁移和离线回归/Windows CI。参考基线仍为 OCSJS 8b25a3f44523a0d4b7373878488056f4aa478cf6；未复制其 TypeScript 或测试数据，未执行导入的 handler，未新增运行依赖。

0.2.11 新增题库答案审核策略（严格、跟随 AI 状态、选项优先）。放宽题干审核时仍核验题型 / 选项，并阻止这类答案进入相似题缓存。未新增依赖。

0.2.10 修正超星验证码重定向判断，复用媒体登录会话、串行识别与有限重试，并区分普通 403 与验证码页面；遇到未恢复的权限 / 会话拦截，章节保留待处理。保留 skreon 的验证码适配来源信息。OCS 言溪 v2 的图片占位符还原与答案匹配参照公开接口行为独立实现，未执行远端脚本或用户提供的 handler。

新增离线验证码识别依赖及模型：ddddocr 1.6.1（MIT，https://github.com/sml2h3/ddddocr）、ONNX Runtime（MIT，https://github.com/microsoft/onnxruntime）、OpenCV Python（Apache 2.0，https://github.com/opencv/opencv-python）、Pillow（MIT-CMU，https://github.com/python-pillow/Pillow）、NumPy（BSD-3-Clause 及其发行包附带的组件许可，https://github.com/numpy/numpy）。准确依赖版本见 requirements-lock.txt，发行包 _internal 中保留相关发行元数据与许可。默认使用 CPU 模型，不要求 GPU / CUDA，也不将验证码图片发送到第三方识别服务。

参考与复用来源：https://github.com/Samueli924/chaoxing
固定提交：45d2106b16764231c2de2038fb042e71b23d878f
上游版本元数据：3.1.3

`vendor/chaoxing` 的平台协议适配模块与字体资源来源于上述提交，保留原文件作者与许可。0.2.0 删除旧 main.py、配置模板和终端任务辅助模块；api/answer.py 仅保留改编的题库协议适配器，移除旧 AI、手动输入、回退与标题缓存实现。api/base.py 移除终端进度与随机作答，改为未解决题目留空、全部解决才允许自动提交，并去除重做后的强制提交。api/logger.py 由桌面 worker 接管脱敏日志；api/cxsecret_font.py 使用模块位置加载资源。题库请求恢复 TLS 证书验证，登录、题库与通知请求具有明确超时。

桌面配置默认值、配置迁移、任务调度、答题路由、AI 请求、图片消息和 Qt 交互独立实现于 chaoxing_desktop，运行不加载参考仓库或旧 CLI。其余平台协议实现仍属于来源明确的改编代码，并非完全从零重写。

0.2.1 增加由用户显式启用的随机补全选项，仅作为题库、AI 和人工补全之后的最后回退。随机答案有独立来源标记、不计入服务命中率，并遵循单独的保存 / 自动提交设置；保留未填写题目仅保存的规则。

0.2.2 修复 api/base.py 的会话单例初始化，并直接使用桌面路由核验后的选项编号生成答题表单；api/decode.py 保留选项图片，供视觉模型和人工补全使用。桌面配置统一布尔值大小写，日志逐个过滤各题库 Token。

0.2.5 在真实任务初始化时校验字体映射表 SHA256，修复错误日志占位符，并清理未使用的终端进度、旧答案校验和辅助函数。字体映射表本身未改动；保留 requests 间接使用的 chardet。

2026-10-14 性能与稳定性修复：api/base.py 的视频计时改用单调时钟，限制结束后的完成核验次数；api/live_process.py 的时长提交连续失败时返回失败，交由桌面章节调度重试。

2026-10-17 媒体完成确认修复：对照 OCS 5.0 的 cx.ts 真实播放器结束事件处理，并只读核对超星公开 videojs-ext.min.js 的 ended → isdrag=4 映射，修复正常片尾遗漏结束上报；按任务卡上报间隔和真实时间进行有限确认。未复制或执行远端 JavaScript，未新增依赖。

2026-10-19 音频及资源格式修复：参考 OCS 5.0 的 cx.ts 中 property.module、属性任务标识与真实媒体/文档分类方式，独立补全旧视频卡中的音频识别，校验媒体、文档和阅读响应，并隔离每张任务卡参数。未复制或执行远端 TypeScript，未新增依赖。

0.2.6 根据用户提供的言溪 v2 配置示例修正 api/answer.py 的 POST JSON 请求、响应处理与 Token 切换，并修复 LIKE 关闭重试时完全不发请求的问题。题库接口地址与超时由桌面独立配置传入；api/base.py 增加媒体进度事件回调，api/decode.py 识别音频任务，桌面 QML 展示并发章节的时长进度。新增桌面脱敏日志持久化、目录设置及按应用命名删除日志。

0.2.7 根据真实响应修正 api/answer.py 的未命中、凭据和余额诊断。新增 bank_question.py，参考 OCS 官方 5.0 分支的 answerer-env.ts、answer.wrapper.handler.ts 和 cx.ts 的公开协议约定，独立实现超星题干元信息清理、文本字段默认值、图片 PNG Base64 和编号占位符；未复制其源码。题库路由增加分类统计并支持有明确标签的小写选项。来源：https://github.com/ocsjs/ocsjs/tree/5.0 。

0.2.8 经截图题目真实对比确认题号残留造成部分漏查，修复 api/decode.py 的题目头部提取和 bank_question.py 的元信息清理顺序，保留正文年份与数学表达式。

0.2.9 新增独立实现的 matching.py，使用 Python 标准库 difflib 和 decimal 处理相似度与数值单位，增加原文 / 格式优先、关键内容保护、候选差距、同轮缓存及选项换序定位。api/answer.py 的言溪实现仅在未命中时尝试有限的格式等价题干，并保留返回题干、选项和 AI 标记供核验。未新增第三方依赖。

桌面代码与上游核心一起以 GPL-3.0 提供，源码目录为 desktop；同次交付的源码包包含此目录的源码、构建文件、上游核心与许可，不含用户账号、Cookie 或 API Key。

第三方运行依赖及各自许可保留于打包依赖的 dist-info 和模块文件中。Qt/PySide6 使用其开源许可；依赖版本详见 requirements-lock.txt。本发行包由开源源码构建，未进行代码签名。

PySide6_Essentials 6.12.0 的本地 METADATA 声明许可选择为 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`。本工程采用 GPL-3.0 开源交付；GPL-3.0 文本随 LICENSE 提供。Qt for Python 源码与构建资料来源：https://code.qt.io/cgit/pyside/pyside-setup.git/ ，文档：https://doc.qt.io/qtforpython 。依赖 DLL 单独放置于 `_internal`，可与匹配版本的自行构建依赖一起使用。
# QML 界面组件

QML 学习工作台使用既有 PySide6 分发的 Qt Quick / Qt Quick Controls（FluentWinUI3、Basic）组件，继续遵循 Qt / PySide6 在本 GPL-3.0 项目中的许可要求。官方控件文档：<https://doc.qt.io/qt-6/qtquickcontrols-fluentwinui3.html>。

Web 调研参考 shadcn/ui、shadcn-admin、Tabler，QML 调研参考 zhuzichu520/FluentUI；本轮没有复制这些第三方模板源码，也没有引入第三方 FluentUI / Pro 模块。布局、页面及状态桥代码在本仓库中实现。

2026-10-15 发布前清理：移除旧 Widgets 界面及其专用组件 / 验收入口，将配置持久化回归测试迁移到 QML 后端。改编的平台适配层移除未使用的签到接口、状态枚举、异常类、通知工厂和字体辅助方法；通知、媒体、验证码和字体映射资源保留。源码包使用明确的源码 / 资源范围，避免收集本机配置及临时文件。
# 原生界面迁移补充

源码默认界面采用 Microsoft Windows App SDK / WinUI 3（MIT）及 .NET 8（MIT），自包含发布时包含相应运行组件。项目固定引用 Windows App SDK `1.8.260921001` 与 Windows SDK BuildTools `10.0.26100.4654`；Windows SDK 构建工具遵循微软随包许可。Python 后台继续使用本文件所述依赖，Qt Core 用于进程、事件循环和模型；原 QML 界面仅保留作兼容参考。

- [Windows App SDK 许可证](https://github.com/microsoft/WindowsAppSDK/blob/main/LICENSE)
- [WinUI 许可证](https://github.com/microsoft/microsoft-ui-xaml/blob/main/LICENSE)
- [.NET 运行时许可证](https://github.com/dotnet/runtime/blob/main/LICENSE.TXT)
