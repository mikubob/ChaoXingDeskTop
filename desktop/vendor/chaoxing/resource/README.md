# Resource 文件夹

此文件夹包含项目所需的资源文件：

- `font_map_table.json`：字体映射表，包含字体字符与其对应哈希值的映射关系，用于字体渲染和处理。
- `ocr_charset.json`：ddddocr 1.6.1 `common_old.onnx` 的原始字符集（8210 项，首项为空白），提取自上游 `models/charset_manager.py` 的 `_get_old_charset` 常量。
- `OCR-LICENSE.txt`：上述字符集及模型的 MIT 许可原文；模型由构建环境的锁定 ddddocr wheel 提供，运行时仅使用模型文件，不导入 ddddocr 或 OpenCV。

这些映射关系被用于将字符转换为对应的唯一标识符，支持多种语言和符号的显示。
