import os
import re
import json
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import time
import threading
import queue
import sys
from pathlib import Path
import platform
import subprocess
import atexit
import hashlib
from datetime import datetime

# ========== 尝试导入拖放支持 ==========
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False
    DND_FILES = None
    TkinterDnD = tk.Tk

# ========== 常量配置 ==========
APP_NAME = "WavTranslator"
if sys.platform == "win32":
    CONFIG_DIR = Path(os.environ.get("APPDATA", ".")) / APP_NAME
else:
    CONFIG_DIR = Path.home() / ".config" / APP_NAME

CACHE_FILE = CONFIG_DIR / "translation_cache.json"
PROGRESS_FILE = CONFIG_DIR / "progress.json"          # 断点续传状态文件
MAX_RETRIES = 3
RETRY_DELAY = 2
BATCH_SIZE = 20
CONCURRENT_BATCHES = 2
CACHE_AUTO_SAVE_INTERVAL = 30

# 支持格式
SUPPORTED_AUDIO_EXTS = [
    '.wav', '.mp3', '.flac', '.aac', '.m4a', '.ogg', '.wma',
    '.aiff', '.ape', '.opus'
]

# ========== 语言选项 ==========
LANGUAGES = {
    "自动检测": "auto",
    "英语": "en",
    "中文": "zh",
    "日语": "ja",
    "韩语": "ko",
    "法语": "fr",
    "德语": "de",
    "西班牙语": "es",
    "俄语": "ru",
}

# ========== 配置管理 ==========
class ConfigManager:
    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = ""
        self.custom_prompt = ""
        self.source_lang = "en"
        self.target_lang = "zh"
        self.enable_camel_split = True
        self.include_pattern = ""
        self.exclude_pattern = ""
        self.translate_folders = False          # 新增：是否翻译文件夹名
        self.load()

    def load(self):
        config_file = self.config_dir / "config.json"
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.api_key = data.get("api_key", "")
                    self.custom_prompt = data.get("custom_prompt", "")
                    self.source_lang = data.get("source_lang", "en")
                    self.target_lang = data.get("target_lang", "zh")
                    self.enable_camel_split = data.get("enable_camel_split", True)
                    self.include_pattern = data.get("include_pattern", "")
                    self.exclude_pattern = data.get("exclude_pattern", "")
                    self.translate_folders = data.get("translate_folders", False)
            except Exception:
                pass

    def save(self):
        try:
            config_file = self.config_dir / "config.json"
            data = {
                "api_key": self.api_key,
                "custom_prompt": self.custom_prompt,
                "source_lang": self.source_lang,
                "target_lang": self.target_lang,
                "enable_camel_split": self.enable_camel_split,
                "include_pattern": self.include_pattern,
                "exclude_pattern": self.exclude_pattern,
                "translate_folders": self.translate_folders,
            }
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if sys.platform != "win32":
                os.chmod(config_file, 0o600)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False

# ========== 缓存管理 ==========
class CacheManager:
    def __init__(self, log_callback=None):
        self.cache_file = CACHE_FILE
        self.cache = {}
        self.dirty = False
        self.lock = threading.Lock()
        self.log_callback = log_callback or (lambda msg: None)
        self._stop_auto_save = threading.Event()
        self._auto_save_thread = None
        self.hits = 0          # 缓存命中次数
        self.misses = 0         # 未命中次数
        self.api_calls = 0      # API 调用次数
        self.load()
        self._start_auto_save()

    def load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
                self.log_callback(f"已加载缓存，共 {len(self.cache)} 条记录")
            except Exception as e:
                self.log_callback(f"加载缓存失败: {e}")
                self.cache = {}

    def _start_auto_save(self):
        def auto_save_loop():
            while not self._stop_auto_save.wait(CACHE_AUTO_SAVE_INTERVAL):
                self.save()
        self._auto_save_thread = threading.Thread(target=auto_save_loop, daemon=True)
        self._auto_save_thread.start()
        atexit.register(self.save, force=True)

    def stop_auto_save(self):
        self._stop_auto_save.set()
        if self._auto_save_thread and self._auto_save_thread.is_alive():
            self._auto_save_thread.join(timeout=1)

    def save(self, force=False):
        with self.lock:
            if not self.dirty and not force:
                return
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_file, "w", encoding="utf-8") as f:
                    json.dump(self.cache, f, ensure_ascii=False, indent=2)
                self.dirty = False
                self.log_callback(f"缓存已保存，当前 {len(self.cache)} 条")
            except Exception as e:
                self.log_callback(f"保存缓存失败: {e}")

    def get(self, key):
        with self.lock:
            val = self.cache.get(key)
            if val is not None:
                self.hits += 1
            else:
                self.misses += 1
            return val

    def set(self, key, value):
        with self.lock:
            self.cache[key] = value
            self.dirty = True

    def set_batch(self, items):
        with self.lock:
            for k, v in items:
                self.cache[k] = v
            self.dirty = True

    def clear(self):
        with self.lock:
            self.cache.clear()
            self.dirty = True
        self.save(force=True)

    def __len__(self):
        with self.lock:
            return len(self.cache)

    def reset_stats(self):
        with self.lock:
            self.hits = 0
            self.misses = 0
            self.api_calls = 0

    def get_stats(self):
        with self.lock:
            total = self.hits + self.misses
            hit_rate = (self.hits / total * 100) if total > 0 else 0
            return {
                "cache_entries": len(self.cache),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": hit_rate,
                "api_calls": self.api_calls,
            }

# ========== 翻译器（支持多语言、自定义提示词）==========
class Translator:
    def __init__(self, api_key, cache_mgr, log_callback, config_mgr):
        self.api_key = api_key
        self.cache = cache_mgr
        self.log = log_callback
        self.config = config_mgr
        self.stop_event = threading.Event()
        self.url = "https://api.deepseek.com/v1/chat/completions"

    def translate_batch(self, texts):
        results = [None] * len(texts)
        to_translate_indices = []
        for i, t in enumerate(texts):
            cached = self.cache.get(t)
            if cached is not None:
                results[i] = cached
            else:
                to_translate_indices.append(i)

        if not to_translate_indices:
            return results

        need_translate = [texts[i] for i in to_translate_indices]
        batches = [need_translate[i:i+BATCH_SIZE] for i in range(0, len(need_translate), BATCH_SIZE)]
        total_batches = len(batches)

        self.log(f"需要翻译 {len(need_translate)} 个短语，共 {total_batches} 批，并发 {CONCURRENT_BATCHES} 批")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        batch_futures = {}
        batch_results = [None] * total_batches

        with ThreadPoolExecutor(max_workers=CONCURRENT_BATCHES) as executor:
            for idx, batch in enumerate(batches):
                future = executor.submit(self._translate_single_batch, batch, idx+1, total_batches)
                batch_futures[future] = idx

            for future in as_completed(batch_futures):
                if self.stop_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise Exception("用户取消了翻译")

                batch_idx = batch_futures[future]
                try:
                    translations = future.result()
                    batch_results[batch_idx] = translations
                except Exception as e:
                    self.log(f"第 {batch_idx+1} 批翻译失败: {e}")
                    raise

        for batch_idx, batch in enumerate(batches):
            translations = batch_results[batch_idx]
            start = batch_idx * BATCH_SIZE
            items_to_cache = []
            for i, tgt in zip(to_translate_indices[start:start+len(batch)], translations):
                results[i] = tgt
                items_to_cache.append((texts[i], tgt))
            if items_to_cache:
                self.cache.set_batch(items_to_cache)

        return results

    def _translate_single_batch(self, batch, batch_num, total):
        # 构建翻译指令
        source_lang_name = next((k for k, v in LANGUAGES.items() if v == self.config.source_lang), "英文")
        target_lang_name = next((k for k, v in LANGUAGES.items() if v == self.config.target_lang), "中文")

        if self.config.custom_prompt.strip():
            system_content = self.config.custom_prompt.strip()
        else:
            system_content = f"你是一个专业的音效命名助手，请将{source_lang_name}音效名称翻译成{target_lang_name}。"

        user_prompt = (
            f"请将以下{source_lang_name}音效名称翻译成{target_lang_name}。"
            "以 JSON 数组格式返回，每个元素是一个字符串，直接对应输入的每个短语。"
            "不要包含任何其他内容，只输出 JSON 数组。\n" + "\n".join(batch)
        )

        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        for attempt in range(MAX_RETRIES):
            if self.stop_event.is_set():
                raise Exception("翻译已取消")

            try:
                req = urllib.request.Request(
                    self.url,
                    data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=90) as response:
                    resp_data = json.loads(response.read().decode("utf-8"))
                    content = resp_data["choices"][0]["message"]["content"].strip()

                # 统计 API 调用次数
                self.cache.api_calls += 1

                # 解析 JSON
                try:
                    translations = json.loads(content)
                    if not isinstance(translations, list) or len(translations) != len(batch):
                        raise ValueError("返回格式不是长度匹配的数组")
                except json.JSONDecodeError:
                    match = re.search(r'\[.*\]', content, re.DOTALL)
                    if match:
                        translations = json.loads(match.group())
                        if len(translations) != len(batch):
                            raise ValueError("提取的数组长度不匹配")
                    else:
                        raise ValueError("无法解析返回内容为 JSON")

                translations = [t.strip() for t in translations]
                self.log(f"第 {batch_num}/{total} 批翻译成功")
                return translations

            except urllib.error.HTTPError as e:
                status = e.code
                if status >= 400 and status < 500:
                    error_body = e.read().decode()
                    raise Exception(f"API 请求失败 (HTTP {status}): {error_body}")
                else:
                    if attempt == MAX_RETRIES - 1:
                        raise
                    delay = RETRY_DELAY * (2 ** attempt)
                    self.log(f"第 {batch_num} 批 HTTP {status}，{delay} 秒后重试 ({attempt+2}/{MAX_RETRIES})...")
                    time.sleep(delay)
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                delay = RETRY_DELAY * (2 ** attempt)
                self.log(f"第 {batch_num} 批翻译异常: {e}，{delay} 秒后重试 ({attempt+2}/{MAX_RETRIES})...")
                time.sleep(delay)

        raise Exception("未知错误")

# ========== 文件处理器 ==========
class FileProcessor:
    @staticmethod
    def split_camel_case(name):
        s = re.sub(r'(?<=[a-zA-Z])(?=\d)', ' ', name)
        s = re.sub(r'(?<=\d)(?=[a-zA-Z])', ' ', s)
        s = re.sub(r'(?<!^)(?=[A-Z][a-z])', ' ', s)
        s = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', s)
        s = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', ' ', s)
        return s

    @staticmethod
    def generate_new_name(original_filename, translation, mode):
        base, ext = os.path.splitext(original_filename)
        if mode == "中文_英文":
            new_name = f"{translation}_{original_filename}"
        elif mode == "只中文":
            new_name = f"{translation}{ext}"
        elif mode == "英文_中文":
            new_name = f"{base}_{translation}{ext}"
        else:
            new_name = f"{translation}_{original_filename}"
        return new_name

    @staticmethod
    def apply_regex_filter(rel_path, include_pattern, exclude_pattern):
        """应用包含/排除正则过滤，返回 True 表示接受该文件"""
        if include_pattern:
            try:
                if not re.search(include_pattern, rel_path):
                    return False
            except re.error:
                pass
        if exclude_pattern:
            try:
                if re.search(exclude_pattern, rel_path):
                    return False
            except re.error:
                pass
        return True

# ========== 预览窗口 ==========
class PreviewWindow:
    def __init__(self, parent, file_list, translations, callback):
        self.parent = parent
        self.file_list = file_list
        self.translations = translations
        self.callback = callback
        self.modified_translations = translations.copy()

        self.win = tk.Toplevel(parent)
        self.win.title("预览翻译结果")
        self.win.geometry("800x500")
        self.win.transient(parent)
        self.win.grab_set()

        # 创建 Treeview
        columns = ("original", "translation")
        self.tree = ttk.Treeview(self.win, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("original", text="原文件名")
        self.tree.heading("translation", text="翻译结果 (双击可编辑)")
        self.tree.column("original", width=350)
        self.tree.column("translation", width=350)

        vsb = ttk.Scrollbar(self.win, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        vsb.grid(row=0, column=1, sticky="ns")

        # 填充数据
        for orig, trans in zip(file_list, self.modified_translations):
            self.tree.insert("", tk.END, values=(orig, trans))

        # 绑定双击编辑
        self.tree.bind("<Double-1>", self.on_double_click)

        # 按钮
        btn_frame = tk.Frame(self.win)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=10)
        tk.Button(btn_frame, text="确认重命名", command=self.on_confirm, width=15).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="取消", command=self.on_cancel, width=10).pack(side=tk.LEFT, padx=5)

        self.win.grid_rowconfigure(0, weight=1)
        self.win.grid_columnconfigure(0, weight=1)

        self.win.protocol("WM_DELETE_WINDOW", self.on_cancel)

    def on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        if column == "#2":  # 翻译列
            item = self.tree.selection()[0]
            col_index = int(column[1:]) - 1
            x, y, width, height = self.tree.bbox(item, column)
            value = self.tree.item(item, "values")[col_index]

            # 获取Treeview的字体（通过样式）
            style = ttk.Style()
            font = style.lookup("Treeview", "font")
            if not font:
                font = "TkDefaultFont"

            # 使用 tk.Entry 并设置相同字体
            entry = tk.Entry(self.tree, font=font, borderwidth=1, relief="solid")
            entry.place(x=x, y=y, width=width, height=height)
            entry.insert(0, value)
            entry.select_range(0, tk.END)
            entry.focus_set()

            def on_confirm(event=None):
                new_value = entry.get()
                entry.destroy()
                values = list(self.tree.item(item, "values"))
                values[col_index] = new_value
                self.tree.item(item, values=values)
                idx = self.tree.index(item)
                self.modified_translations[idx] = new_value

            entry.bind("<Return>", on_confirm)
            entry.bind("<FocusOut>", lambda e: entry.destroy())

    def on_confirm(self):
        self.win.destroy()
        self.callback(self.modified_translations)

    def on_cancel(self):
        self.win.destroy()
        self.callback(None)

# ========== 主应用程序 ==========
class TranslationApp:
    def __init__(self, root):
        self.root = root
        root.title("波波的音频翻译软件")  # 添加版本号
        root.geometry("920x820")
        root.resizable(True, True)

        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.progress_var = tk.IntVar(value=0)

        self.config_mgr = ConfigManager()
        self.cache_mgr = CacheManager(log_callback=self.log)
        self.translator = None

        # 存储扫描到的音频文件相对路径列表
        self.audio_files = []
        self.scanning = False

        # 断点续传进度
        self.progress_data = {}  # key: rel_path, value: 'pending' | 'translated' | 'renamed'
        self.load_progress()

        self.create_widgets()
        self.process_log_queue()
        self.update_stats_display()

        # 程序退出时保存
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 拖放支持
        if HAS_DND:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.on_drop)

    def create_widgets(self):
        # 主框架使用 Notebook 组织
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)

        # 主页面（原有功能）
        self.main_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.main_frame, text="翻译")
        self.create_main_tab()

        # 高级设置页
        self.settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_frame, text="高级设置")
        self.create_settings_tab()

        # 统计信息页
        self.stats_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.stats_frame, text="统计")
        self.create_stats_tab()

    def create_main_tab(self):
        frame = self.main_frame

        # 文件夹选择
        tk.Label(frame, text="选择包含音频文件的文件夹：").pack(pady=(10, 0))
        folder_frame = tk.Frame(frame)
        folder_frame.pack(pady=5, fill="x", padx=10)
        self.folder_path = tk.StringVar()
        self.folder_entry = tk.Entry(folder_frame, textvariable=self.folder_path, width=60)
        self.folder_entry.pack(side=tk.LEFT, expand=True, fill="x")
        tk.Button(folder_frame, text="浏览...", command=self.choose_folder).pack(side=tk.RIGHT, padx=5)

        # API Key 输入
        key_frame = tk.Frame(frame)
        key_frame.pack(pady=5, fill="x", padx=10)
        tk.Label(key_frame, text="API Key：").pack(side=tk.LEFT)
        self.api_key_var = tk.StringVar(value=self.config_mgr.api_key)
        self.key_entry = tk.Entry(key_frame, textvariable=self.api_key_var, width=50, show="*")
        self.key_entry.pack(side=tk.LEFT, expand=True, fill="x", padx=5)
        self.show_key_btn = tk.Button(key_frame, text="👁", command=self.toggle_key_visibility, width=3)
        self.show_key_btn.pack(side=tk.LEFT)
        tk.Button(key_frame, text="保存 Key", command=self.save_key).pack(side=tk.LEFT, padx=5)
        tk.Button(key_frame, text="测试 API", command=self.test_api).pack(side=tk.LEFT, padx=5)

        # 命名模式
        mode_frame = tk.Frame(frame)
        mode_frame.pack(pady=5, fill="x", padx=10)
        tk.Label(mode_frame, text="命名模式：").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="中文_英文")
        modes = ["中文_英文", "只中文", "英文_中文"]
        mode_menu = ttk.Combobox(mode_frame, textvariable=self.mode_var, values=modes, state="readonly", width=15)
        mode_menu.pack(side=tk.LEFT, padx=5)

        # 语言选择
        lang_frame = tk.Frame(frame)
        lang_frame.pack(pady=5, fill="x", padx=10)
        tk.Label(lang_frame, text="源语言：").pack(side=tk.LEFT)
        self.source_lang_var = tk.StringVar(value=self.get_key_by_value(LANGUAGES, self.config_mgr.source_lang))
        source_combo = ttk.Combobox(lang_frame, textvariable=self.source_lang_var,
                                    values=list(LANGUAGES.keys()), state="readonly", width=10)
        source_combo.pack(side=tk.LEFT, padx=5)
        tk.Label(lang_frame, text="目标语言：").pack(side=tk.LEFT, padx=(10,0))
        self.target_lang_var = tk.StringVar(value=self.get_key_by_value(LANGUAGES, self.config_mgr.target_lang))
        target_combo = ttk.Combobox(lang_frame, textvariable=self.target_lang_var,
                                    values=list(LANGUAGES.keys()), state="readonly", width=10)
        target_combo.pack(side=tk.LEFT, padx=5)

        # 驼峰拆分选项
        self.camel_split_var = tk.BooleanVar(value=self.config_mgr.enable_camel_split)
        tk.Checkbutton(lang_frame, text="启用驼峰拆分", variable=self.camel_split_var).pack(side=tk.LEFT, padx=10)

        # 新增：翻译文件夹名称选项
        self.translate_folders_var = tk.BooleanVar(value=self.config_mgr.translate_folders)
        tk.Checkbutton(lang_frame, text="翻译文件夹名称", variable=self.translate_folders_var).pack(side=tk.LEFT, padx=10)

        # 进度条
        progress_frame = tk.Frame(frame)
        progress_frame.pack(pady=5, fill="x", padx=10)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.LEFT, expand=True, fill="x", padx=(0, 10))
        self.progress_label = tk.Label(progress_frame, text="0/0")
        self.progress_label.pack(side=tk.RIGHT)

        # 按钮区域
        btn_frame = tk.Frame(frame)
        btn_frame.pack(pady=10)
        self.scan_button = tk.Button(btn_frame, text="检索音频", command=self.scan_folder, width=12)
        self.scan_button.pack(side=tk.LEFT, padx=5)
        self.start_button = tk.Button(btn_frame, text="开始翻译重命名", command=self.start_rename, width=15)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.cancel_button = tk.Button(btn_frame, text="取消", command=self.cancel_rename, state=tk.DISABLED, width=10)
        self.cancel_button.pack(side=tk.LEFT, padx=5)

        # 日志文本框
        log_frame = tk.Frame(frame)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tk.Label(log_frame, text="日志：").pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=12, wrap=tk.WORD)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill="y")
        self.log_text.pack(side=tk.LEFT, fill="both", expand=True)

    def create_settings_tab(self):
        frame = self.settings_frame

        # 缓存信息
        cache_frame = tk.Frame(frame)
        cache_frame.pack(pady=5, fill="x", padx=10)
        self.cache_label = tk.Label(cache_frame, text=f"缓存条目: {len(self.cache_mgr)}")
        self.cache_label.pack(side=tk.LEFT)
        tk.Button(cache_frame, text="清空缓存", command=self.clear_cache).pack(side=tk.LEFT, padx=5)
        tk.Button(cache_frame, text="打开缓存文件夹", command=self.open_cache_folder).pack(side=tk.LEFT, padx=5)

        # 自定义提示词
        tk.Label(frame, text="自定义 System Prompt (留空则使用默认):").pack(anchor="w", padx=10, pady=(10,0))
        self.custom_prompt_text = tk.Text(frame, height=5, wrap=tk.WORD)
        self.custom_prompt_text.pack(fill="x", padx=10, pady=5)
        self.custom_prompt_text.insert("1.0", self.config_mgr.custom_prompt)

        # 正则过滤
        filter_frame = tk.LabelFrame(frame, text="正则表达式过滤文件 (相对路径匹配)")
        filter_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(filter_frame, text="包含模式 (留空不过滤):").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.include_pattern_var = tk.StringVar(value=self.config_mgr.include_pattern)
        tk.Entry(filter_frame, textvariable=self.include_pattern_var, width=40).grid(row=0, column=1, padx=5, pady=2)

        tk.Label(filter_frame, text="排除模式 (留空不过滤):").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.exclude_pattern_var = tk.StringVar(value=self.config_mgr.exclude_pattern)
        tk.Entry(filter_frame, textvariable=self.exclude_pattern_var, width=40).grid(row=1, column=1, padx=5, pady=2)

        # 保存所有设置按钮
        btn_save = tk.Button(frame, text="保存所有设置", command=self.save_all_settings)
        btn_save.pack(pady=10)

    def create_stats_tab(self):
        frame = self.stats_frame

        # 统计信息标签
        self.stats_text = tk.Text(frame, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self.stats_text.pack(fill="both", expand=True, padx=10, pady=10)

        # 重置按钮
        tk.Button(frame, text="重置统计", command=self.reset_stats).pack(pady=5)

    # ---------- 工具方法 ----------
    def get_key_by_value(self, d, val):
        for k, v in d.items():
            if v == val:
                return k
        return list(d.keys())[0]

    def toggle_key_visibility(self):
        if self.key_entry.cget("show") == "*":
            self.key_entry.config(show="")
            self.show_key_btn.config(text="🔒")
        else:
            self.key_entry.config(show="*")
            self.show_key_btn.config(text="👁")

    def save_key(self):
        new_key = self.api_key_var.get().strip()
        if not new_key:
            messagebox.showwarning("警告", "API Key 不能为空")
            return
        self.config_mgr.api_key = new_key
        self.config_mgr.save()
        messagebox.showinfo("成功", "API Key 已保存")

    def save_all_settings(self):
        self.config_mgr.custom_prompt = self.custom_prompt_text.get("1.0", tk.END).strip()
        self.config_mgr.source_lang = LANGUAGES[self.source_lang_var.get()]
        self.config_mgr.target_lang = LANGUAGES[self.target_lang_var.get()]
        self.config_mgr.enable_camel_split = self.camel_split_var.get()
        self.config_mgr.include_pattern = self.include_pattern_var.get().strip()
        self.config_mgr.exclude_pattern = self.exclude_pattern_var.get().strip()
        self.config_mgr.translate_folders = self.translate_folders_var.get()
        self.config_mgr.save()
        messagebox.showinfo("成功", "所有设置已保存")

    def clear_cache(self):
        if not messagebox.askyesno("确认", "确定清空翻译缓存吗？\n已翻译的短语将需要重新翻译。"):
            return
        self.cache_mgr.clear()
        self.cache_label.config(text=f"缓存条目: 0")
        self.log("缓存已清空。")
        self.update_stats_display()

    def open_cache_folder(self):
        folder_path = CONFIG_DIR
        if not folder_path.exists():
            messagebox.showinfo("提示", f"缓存文件夹不存在\n路径: {folder_path}")
            return
        try:
            if platform.system() == 'Windows':
                os.startfile(folder_path)
            elif platform.system() == 'Darwin':
                subprocess.run(['open', folder_path])
            else:
                subprocess.run(['xdg-open', folder_path])
        except Exception as e:
            messagebox.showerror("错误", f"无法打开缓存文件夹: {e}")

    def reset_stats(self):
        self.cache_mgr.reset_stats()
        self.update_stats_display()
        self.log("统计信息已重置")

    def update_stats_display(self):
        stats = self.cache_mgr.get_stats()
        text = f"""缓存条目: {stats['cache_entries']}
缓存命中: {stats['hits']}
缓存未命中: {stats['misses']}
命中率: {stats['hit_rate']:.2f}%
API 调用次数: {stats['api_calls']}
"""
        self.stats_text.config(state=tk.NORMAL)
        self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert("1.0", text)
        self.stats_text.config(state=tk.DISABLED)

    def load_progress(self):
        """加载断点续传状态"""
        if PROGRESS_FILE.exists():
            try:
                with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                    self.progress_data = json.load(f)
                self.log(f"已加载断点续传进度，包含 {len(self.progress_data)} 条记录")
            except Exception as e:
                self.log(f"加载进度文件失败: {e}")
                self.progress_data = {}
        else:
            self.progress_data = {}

    def save_progress(self):
        """保存断点续传状态"""
        try:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.progress_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"保存进度文件失败: {e}")

    # ---------- 拖放 ----------
    def on_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        if files:
            for f in files:
                if os.path.isdir(f):
                    self.folder_path.set(f)
                    self.audio_files = []
                    self.log(f"已拖入文件夹: {f}")
                    break
            else:
                self.log("拖入的不是文件夹，请拖入文件夹")

    # ---------- 文件夹选择 ----------
    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path.set(folder)
            self.audio_files = []

    # ---------- 日志 ----------
    def log(self, message):
        self.log_queue.put(message)

    def process_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
        except queue.Empty:
            pass
        self.root.after(100, self.process_log_queue)

    def update_progress(self, current, total, stage=""):
        self.progress_bar.config(maximum=total)
        self.progress_var.set(current)
        self.progress_label.config(text=f"{current}/{total} {stage}")
        self.root.update_idletasks()

    # ---------- API 测试 ----------
    def test_api(self):
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror("错误", "请输入 API Key")
            return

        def test():
            try:
                url = "https://api.deepseek.com/v1/chat/completions"
                data = {
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 5
                }
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
                req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode())
                self.root.after(0, lambda: messagebox.showinfo("测试成功", f"API 有效，响应: {result['choices'][0]['message']['content']}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("测试失败", f"API 无效: {e}"))

        threading.Thread(target=test, daemon=True).start()

    # ---------- 扫描 ----------
    def scan_folder(self):
        folder = self.folder_path.get().strip()
        if not folder:
            messagebox.showerror("错误", "请先选择文件夹")
            return
        if self.scanning:
            self.log("正在扫描中，请稍候...")
            return
        self.scanning = True
        self.scan_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.DISABLED)
        self.log(f"开始递归扫描文件夹: {folder}")
        self.audio_files = []
        thread = threading.Thread(target=self.scan_worker, args=(folder,))
        thread.daemon = True
        thread.start()

    def scan_worker(self, folder):
        try:
            audio_relpaths = []
            include = self.config_mgr.include_pattern
            exclude = self.config_mgr.exclude_pattern
            for root_dir, dirs, files in os.walk(folder):
                for file in files:
                    if self.stop_event.is_set():
                        break
                    ext = os.path.splitext(file)[1].lower()
                    if ext in SUPPORTED_AUDIO_EXTS:
                        full_path = os.path.join(root_dir, file)
                        rel_path = os.path.relpath(full_path, folder)
                        if FileProcessor.apply_regex_filter(rel_path, include, exclude):
                            audio_relpaths.append(rel_path)
                if self.stop_event.is_set():
                    break
            self.root.after(0, self.scan_complete, audio_relpaths)
        except Exception as e:
            self.root.after(0, self.scan_complete, [], str(e))

    def scan_complete(self, audio_relpaths, error=None):
        self.scanning = False
        self.scan_button.config(state=tk.NORMAL)
        self.start_button.config(state=tk.NORMAL)
        if error:
            self.log(f"扫描出错: {error}")
            messagebox.showerror("扫描错误", error)
        else:
            self.audio_files = audio_relpaths
            count = len(audio_relpaths)
            self.log(f"扫描完成，找到 {count} 个音频文件")
            if count == 0:
                self.log(f"支持的格式: {', '.join(SUPPORTED_AUDIO_EXTS)}")
            else:
                if count <= 200:
                    for rel in audio_relpaths:
                        self.log(f"  {rel}")
                else:
                    for i, rel in enumerate(audio_relpaths[:100]):
                        self.log(f"  {rel}")
                    self.log(f"  ... 共 {count} 个文件")
        self.cache_label.config(text=f"缓存条目: {len(self.cache_mgr)}")

    # ---------- 重命名流程 ----------
    def start_rename(self):
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror("错误", "请输入 API Key")
            return

        folder = self.folder_path.get().strip()
        if not folder:
            messagebox.showerror("错误", "请选择文件夹")
            return

        if not self.audio_files:
            self.log("未预先检索，正在扫描文件夹...")
            self.scan_and_then_translate(api_key, folder)
        else:
            self.start_translation(api_key, folder)

    def scan_and_then_translate(self, api_key, folder):
        if self.scanning:
            self.log("正在扫描中，请稍候...")
            return
        self.scanning = True
        self.scan_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self.stop_event.clear()
        self.log(f"开始递归扫描文件夹: {folder}")

        def worker():
            try:
                audio_relpaths = []
                include = self.config_mgr.include_pattern
                exclude = self.config_mgr.exclude_pattern
                for root_dir, dirs, files in os.walk(folder):
                    for file in files:
                        if self.stop_event.is_set():
                            break
                        ext = os.path.splitext(file)[1].lower()
                        if ext in SUPPORTED_AUDIO_EXTS:
                            full_path = os.path.join(root_dir, file)
                            rel_path = os.path.relpath(full_path, folder)
                            if FileProcessor.apply_regex_filter(rel_path, include, exclude):
                                audio_relpaths.append(rel_path)
                    if self.stop_event.is_set():
                        break
                if self.stop_event.is_set():
                    self.root.after(0, self.rename_cancelled)
                    return
                self.root.after(0, self.start_translation_with_files, api_key, folder, audio_relpaths)
            except Exception as e:
                self.root.after(0, self.rename_finished, "错误", str(e))

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()

    def start_translation_with_files(self, api_key, folder, audio_relpaths):
        self.scanning = False
        self.scan_button.config(state=tk.NORMAL)
        if not audio_relpaths:
            self.log("未找到任何音频文件")
            self.rename_finished("提示", "文件夹中没有支持的音频文件")
            return
        self.audio_files = audio_relpaths
        self.start_translation(api_key, folder)

    def start_translation(self, api_key, folder):
        if not self.audio_files:
            messagebox.showwarning("警告", "没有可处理的音频文件，请先检索或选择其他文件夹")
            return

        self.config_mgr.api_key = api_key
        self.config_mgr.source_lang = LANGUAGES[self.source_lang_var.get()]
        self.config_mgr.target_lang = LANGUAGES[self.target_lang_var.get()]
        self.config_mgr.enable_camel_split = self.camel_split_var.get()
        self.config_mgr.translate_folders = self.translate_folders_var.get()
        self.config_mgr.save()

        self.start_button.config(state=tk.DISABLED)
        self.scan_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self.stop_event.clear()

        self.thread = threading.Thread(target=self.rename_worker, args=(folder, self.mode_var.get(), api_key))
        self.thread.daemon = True
        self.thread.start()

    def rename_worker(self, folder, mode, api_key):
        try:
            audio_relpaths = self.audio_files
            total_files = len(audio_relpaths)
            self.log(f"准备处理 {total_files} 个音频文件")

            pending_files = []
            pending_indices = []
            for i, rel in enumerate(audio_relpaths):
                if self.progress_data.get(rel) != 'renamed':
                    pending_files.append(rel)
                    pending_indices.append(i)
                else:
                    self.log(f"跳过已完成的文件: {rel}")

            if not pending_files:
                self.log("所有文件已完成，无需处理")
                self.root.after(0, self.rename_finished, "完成", "所有文件已处理")
                return

            clean_names = []
            for rel in pending_files:
                base = os.path.basename(rel)
                name_without_ext = os.path.splitext(base)[0]
                if self.config_mgr.enable_camel_split:
                    clean = FileProcessor.split_camel_case(name_without_ext)
                else:
                    clean = name_without_ext
                clean_names.append(clean)

            translator = Translator(api_key, self.cache_mgr, self.log, self.config_mgr)
            translator.stop_event = self.stop_event

            self.log("开始翻译...")
            try:
                translations = translator.translate_batch(clean_names)
            except Exception as e:
                if self.stop_event.is_set():
                    self.log("翻译已取消")
                else:
                    self.log(f"翻译失败: {e}")
                self.root.after(0, self.rename_finished, "错误", str(e))
                return

            preview_done = threading.Event()
            preview_result = [None]

            def preview_callback(modified):
                preview_result[0] = modified
                preview_done.set()

            self.root.after(0, lambda: self.show_preview(pending_files, translations, preview_callback))
            preview_done.wait()

            final_translations = preview_result[0]
            if final_translations is None:
                self.log("用户取消重命名")
                self.root.after(0, self.rename_finished, "取消", "操作已取消")
                return

            for rel, new_trans in zip(pending_files, final_translations):
                original_clean = clean_names[pending_files.index(rel)]
                if original_clean != new_trans:
                    self.cache_mgr.set(original_clean, new_trans)

            self.log("开始重命名文件...")
            renamed = 0
            errors = []
            total = len(pending_files)

            update_step = max(1, min(10, total // 100 + 1))
            next_update = update_step

            for i, (rel_path, chinese) in enumerate(zip(pending_files, final_translations)):
                if self.stop_event.is_set():
                    self.log("用户取消重命名")
                    break

                try:
                    old_abs = os.path.join(folder, rel_path)
                    dir_name = os.path.dirname(rel_path)
                    base_filename = os.path.basename(rel_path)

                    new_filename = FileProcessor.generate_new_name(base_filename, chinese, mode)
                    if dir_name:
                        new_rel = os.path.join(dir_name, new_filename)
                    else:
                        new_rel = new_filename
                    new_abs = os.path.join(folder, new_rel)

                    if os.path.normcase(old_abs) == os.path.normcase(new_abs):
                        self.log(f"跳过 {rel_path} (翻译后无变化)")
                        renamed += 1
                    else:
                        base, ext = os.path.splitext(new_abs)
                        counter = 1
                        while os.path.exists(new_abs):
                            new_abs = f"{base}_{counter}{ext}"
                            counter += 1
                            new_rel = os.path.relpath(new_abs, folder)

                        os.rename(old_abs, new_abs)
                        renamed += 1
                        self.log(f"重命名: {rel_path} -> {new_rel}")

                    self.progress_data[rel_path] = 'renamed'

                except Exception as e:
                    errors.append(f"{rel_path}: {e}")
                    self.log(f"重命名失败: {rel_path} - {e}")

                if i % 10 == 0 or i == total-1:
                    self.save_progress()

                if i + 1 >= next_update or i + 1 == total:
                    self.update_progress(i+1, total, "重命名中...")
                    next_update += update_step

            self.log(f"完成！成功重命名 {renamed} 个文件。")
            if errors:
                self.log("以下文件重命名失败：")
                for err in errors:
                    self.log(f"  {err}")

            # 如果启用了文件夹翻译，则执行文件夹重命名（包括所有子文件夹）
            if self.config_mgr.translate_folders:
                self.log("开始翻译文件夹名称（包括所有子文件夹）...")
                self.translate_folders_worker(folder, api_key)

            self.audio_files = []
            self.root.after(0, self.rename_finished, "完成", f"成功重命名 {renamed} 个文件")

        except Exception as e:
            self.log(f"发生未预期错误: {e}")
            self.root.after(0, self.rename_finished, "错误", str(e))

    def translate_folders_worker(self, root_folder, api_key):
        """翻译并重命名所有文件夹（根文件夹 + 所有子文件夹）"""
        try:
            # 收集所有文件夹信息
            folders = []  # 元素为 (rel_path, name, abs_path)
            # 添加根文件夹本身
            root_name = os.path.basename(root_folder)
            folders.append(("", root_name, root_folder))

            # 遍历所有子文件夹
            for dirpath, dirnames, filenames in os.walk(root_folder):
                if self.stop_event.is_set():
                    self.log("文件夹翻译已取消")
                    return
                rel = os.path.relpath(dirpath, root_folder)
                if rel == ".":
                    continue  # 根文件夹已处理
                name = os.path.basename(dirpath)
                folders.append((rel, name, dirpath))

            if not folders:
                self.log("没有找到任何文件夹")
                return

            self.log(f"共找到 {len(folders)} 个文件夹需要处理（包含根目录和所有子文件夹）")

            # 准备需要翻译的文件夹名（可能经过驼峰拆分）
            names_to_translate = []
            for rel, name, abs_path in folders:
                if self.config_mgr.enable_camel_split:
                    clean_name = FileProcessor.split_camel_case(name)
                else:
                    clean_name = name
                names_to_translate.append(clean_name)

            # 去重，减少API调用
            unique_names = list(set(names_to_translate))
            self.log(f"需要翻译的独立文件夹名有 {len(unique_names)} 个")

            # 创建翻译器
            translator = Translator(api_key, self.cache_mgr, self.log, self.config_mgr)
            translator.stop_event = self.stop_event

            try:
                translations = translator.translate_batch(unique_names)
            except Exception as e:
                if self.stop_event.is_set():
                    self.log("文件夹翻译已取消")
                else:
                    self.log(f"文件夹翻译失败: {e}")
                return

            # 构建原始名称到翻译的映射
            name_to_trans = dict(zip(unique_names, translations))

            # 按深度排序（从深到浅），确保先处理子文件夹
            # 使用 Path(rel).parts 的长度来计算深度，避免分隔符不一致的问题
            folders.sort(key=lambda x: len(Path(x[0]).parts), reverse=True)

            # 重命名文件夹
            renamed_count = 0
            errors = []
            total_folders = len(folders)

            for idx, (rel, orig_name, abs_path) in enumerate(folders):
                if self.stop_event.is_set():
                    self.log("文件夹重命名已取消")
                    break

                # 获取翻译后的名称（基于原始名称的clean版本）
                if self.config_mgr.enable_camel_split:
                    clean_name = FileProcessor.split_camel_case(orig_name)
                else:
                    clean_name = orig_name
                translated = name_to_trans[clean_name]

                # 构建新路径（只替换最后一级）
                parent_dir = os.path.dirname(abs_path)
                new_abs = os.path.join(parent_dir, translated)

                # 冲突处理：如果新路径已存在且不是当前文件夹，则添加后缀
                if os.path.exists(new_abs) and os.path.normcase(new_abs) != os.path.normcase(abs_path):
                    base = new_abs
                    counter = 1
                    while os.path.exists(new_abs):
                        new_abs = f"{base}_{counter}"
                        counter += 1
                    self.log(f"文件夹重命名冲突，使用 {os.path.basename(new_abs)}")

                try:
                    os.rename(abs_path, new_abs)
                    renamed_count += 1
                    self.log(f"文件夹重命名: {rel or os.path.basename(root_folder)} -> {os.path.basename(new_abs)}")
                except Exception as e:
                    errors.append(f"{rel}: {e}")
                    self.log(f"文件夹重命名失败 {rel}: {e}")

                # 更新进度显示
                if (idx + 1) % 5 == 0 or idx + 1 == total_folders:
                    self.update_progress(idx + 1, total_folders, "文件夹重命名中...")

            self.log(f"文件夹重命名完成，成功 {renamed_count} 个，失败 {len(errors)} 个")
            if errors:
                self.log("失败的文件夹：")
                for err in errors:
                    self.log(f"  {err}")

            # 文件夹重命名后，删除进度文件，因为所有文件路径已改变
            if PROGRESS_FILE.exists():
                PROGRESS_FILE.unlink()
                self.progress_data = {}
                self.log("已清除断点续传进度文件")

        except Exception as e:
            self.log(f"文件夹翻译过程中出现异常: {e}")

    def show_preview(self, file_list, translations, callback):
        PreviewWindow(self.root, file_list, translations, callback)

    def cancel_rename(self):
        self.stop_event.set()
        self.log("用户请求取消，正在停止...")
        self.cancel_button.config(state=tk.DISABLED)

    def rename_cancelled(self):
        self.scanning = False
        self.scan_button.config(state=tk.NORMAL)
        self.start_button.config(state=tk.NORMAL)
        self.cancel_button.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="0/0")
        self.log("扫描已取消")

    def rename_finished(self, title, message):
        self.start_button.config(state=tk.NORMAL)
        self.scan_button.config(state=tk.NORMAL)
        self.cancel_button.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="0/0")
        self.cache_label.config(text=f"缓存条目: {len(self.cache_mgr)}")
        self.update_stats_display()
        messagebox.showinfo(title, message)

    def on_closing(self):
        self.cache_mgr.stop_auto_save()
        self.cache_mgr.save(force=True)
        self.save_progress()
        self.root.destroy()

# ========== 启动 ==========
if __name__ == "__main__":
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        print("提示: 如需拖放文件夹功能，请安装 tkinterdnd2: pip install tkinterdnd2")
    app = TranslationApp(root)
    root.mainloop()
