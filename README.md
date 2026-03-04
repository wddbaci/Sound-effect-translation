# WavTranslator - AI音频名称批量翻译工具

## 本软件利用 DeepSeek Chat API，将文件夹内所有支持的音频文件（.wav, .mp3, .flac, .aac, .m4a, .ogg 等）的文件名翻译成指定语言（如中文），并按用户选择的命名规则重命名。同时支持递归扫描子文件夹、驼峰拆分、翻译缓存、断点续传、预览编辑、正则过滤等功能。v2.0.1 新增文件夹名称翻译功能，可一次性将整个文件夹树（包括根目录及所有子文件夹）翻译为中文。

### 安装事项： 我的实力不行，所以安装完成打开软件时，有时候出现文件损坏，目前解决方法在安装包里放了一个隔离修复脚本   点一下就可以了。

## 手动修复指南（针对 macOS）

如果打开应用时提示“已损坏，无法打开”，请按以下步骤操作：

### 1. 移除隔离属性
- 将应用拖到 `Applications` 文件夹。
- 打开“终端”（Terminal），输入以下命令并回车：
  ```bash
  sudo xattr -rd com.apple.quarantine /Applications/音效翻译.app

### 2. （可选）开启“任何来源”

如果你希望允许运行所有来源的应用，可以在终端执行：

```bash
sudo spctl --master-disable
```

### 此命令会在“系统设置 → 隐私与安全性”中显示“任何来源”选项。注意：即使开启“任何来源”，仍需执行第 1 步的 xattr 命令来移除隔离属性。




https://github.com/user-attachments/assets/e89a8d24-60f8-4d99-8866-db515a8213a0





WavTranslator 是一个基于 DeepSeek API 的桌面工具，可以批量将音频文件名中的英文部分翻译成中文，并按照用户指定的命名模式重命名文件。支持多种常见音频格式，内置翻译缓存以避免重复请求，适合音效库、音乐文件整理等场景。

<img width="1840" height="1696" alt="image" src="https://github.com/user-attachments/assets/d26cbd3d-2ab5-4709-88b7-d8ace2edcd82" />






## ✨ 功能特性

- **多格式支持**：.wav, .mp3, .flac, .aac, .m4a, .ogg, .wma, .aiff, .ape, .opus 等常见音频格式。
- **批量翻译**：递归扫描文件夹，自动过滤非音频文件，一次处理成百上千个文件。
- **智能命名模式**：
  - `中文_英文`（翻译\_原文件名）
  - `只中文`（仅保留翻译）
  - `英文_中文`（原文件名\_翻译）
- **驼峰拆分翻译**：自动拆分 CamelCase（如 `ExplosionMedium` → `Explosion Medium`），提高翻译准确性。
- **翻译缓存**：已翻译的短语自动缓存，避免重复调用 API，节省时间和费用；缓存命中率实时统计。
- **断点续传**：处理中断后可继续未完成任务，进度文件自动保存。
- **预览编辑**：翻译结果以表格形式展示，可双击修改，确认后再执行重命名。
- **自定义提示词**：高级设置中可自定义 System Prompt，满足专业领域翻译需求。
- **正则过滤**：支持包含/排除规则（基于相对路径），精细控制处理范围。
- **拖放支持**：直接将文件夹拖入窗口即可开始扫描（需安装 `tkinterdnd2`）。
- **统计信息**：实时显示缓存命中率、API 调用次数等。

### 🆕 v2.0.1 新增功能
- **文件夹名称翻译（可选）**  
  勾选“翻译文件夹名称”后，软件将按从深到浅的顺序翻译所有文件夹（包括根目录和子文件夹），自动处理重名冲突（添加数字后缀）。




## 🖥️ 适用平台

- **macOS** (已打包为 .app 应用)
- Windows / Linux (可运行源码，需安装 Python 及依赖)

---

## 📦 安装

### 1. 使用预打包的 macOS 应用
从 [Releases](https://github.com/yourname/wavtranslator/releases) 下载最新版 `音效翻译.app`，拖入“应用程序”文件夹即可。

> ⚠️ 首次打开可能提示“无法验证开发者”，请在“系统设置” → “隐私与安全性”中点击“仍要打开”。

### 2. 从源码运行
#### 环境要求
- Python 3.8+
- 安装依赖：
```bash
pip install tkinterdnd2
```

运行

```
bash
python 音效翻译.py
```

打包（macOS）
如需自行打包为 .app，可使用 PyInstaller：
```
bash
pip install pyinstaller
pyinstaller --windowed --name "音效翻译" --icon icon.icns --additional-hooks-dir . 音效翻译.py
```

请确保当前目录下有 hook-tkinterdnd2.py 文件，内容如下：
```
python
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
datas = collect_data_files('tkinterdnd2')
hiddenimports = collect_submodules('tkinterdnd2')
```


🚀 使用方法

获取 API Key
前往 DeepSeek 平台 注册并创建 API Key。
启动软件
打开应用，在“API Key”输入框中粘贴你的 Key，点击“保存 Key”。
选择文件夹
点击“浏览”选择目标文件夹，或直接将文件夹拖入窗口。
设置翻译选项

选择源语言和目标语言（支持自动检测、英语、中文、日语、韩语等）。
选择命名模式（中文_英文 / 只中文 / 英文_中文）。
可选：启用驼峰拆分、翻译文件夹名称。
扫描音频
点击“检索音频”可预览文件夹内的音频文件（支持正则过滤）。也可跳过扫描，直接开始翻译。
开始翻译重命名
点击“开始翻译重命名”，软件将：

扫描文件（如未预扫描）
翻译文件名（利用缓存）
弹出预览窗口，可修改翻译结果
确认后执行文件重命名
若勾选“翻译文件夹名称”，将自动重命名所有文件夹
查看日志与统计
日志区域实时显示处理过程；切换到“统计”标签可查看缓存命中率、API 调用次数等。
⚙️ 配置说明

所有设置（API Key、自定义提示词、语言选项、正则过滤等）自动保存在：

Windows: %APPDATA%\WavTranslator\config.json
macOS/Linux: ~/.config/WavTranslator/config.json
缓存文件位于同一目录下的 translation_cache.json，可手动清空或通过界面“清空缓存”按钮操作。

❓ 常见问题

Q：翻译失败/API 错误怎么办？
A：请检查 API Key 是否正确，网络是否通畅。可在高级设置中自定义 System Prompt 以优化翻译效果。

Q：文件夹翻译后路径混乱？
A：软件按深度优先处理（最深层的文件夹先改名），确保父文件夹改名时子路径仍然有效。若仍有问题，请提供日志反馈。

Q：如何关闭拖放功能？
A：若未安装 tkinterdnd2，软件将自动回退为标准 Tk，拖放功能不可用，但不影响其他操作。

Q：打包后的应用很大？
A：约 33MB 属于 Python 打包应用的正常大小，包含了 Python 运行时、依赖库和资源文件。


📄 许可证

本项目采用 MIT 许可证。您可以自由使用、修改、分发，但需保留版权声明。


🙏 致谢

翻译服务由 DeepSeek Chat 提供

拖放支持基于 tkinterdnd2

打包工具 PyInstaller


让音频文件管理从此井井有条！

如有问题或建议，欢迎提交 Issue 或 Pull Request。
