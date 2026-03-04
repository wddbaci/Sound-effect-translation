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

# ========== 常量配置 ==========
APP_NAME = "WavTranslator"
# 配置文件目录：Unix-like 使用 ~/.config/WavTranslator，Windows 使用 %APPDATA%\WavTranslator
if sys.platform == "win32":
    CONFIG_DIR = Path(os.environ.get("APPDATA", ".")) / APP_NAME
else:
    CONFIG_DIR = Path.home() / ".config" / APP_NAME

CACHE_FILE = CONFIG_DIR / "translation_cache.json"
MAX_RETRIES = 3
RETRY_DELAY = 2          # 基础重试延迟（秒）
BATCH_SIZE = 20           # 每批翻译数量
CONCURRENT_BATCHES = 2    # 并发批次数（避免触发限流）
CACHE_AUTO_SAVE_INTERVAL = 30  # 缓存自动保存间隔（秒）

# ========== 支持的音频格式 ==========
SUPPORTED_AUDIO_EXTS = [
    '.wav', '.mp3', '.flac', '.aac', '.m4a', '.ogg', '.wma',
    '.aiff', '.ape', '.opus'
]

# ========== 配置管理（仅文件存储，移除 keyring）==========
class ConfigManager:
    """管理 API Key 和用户设置，以文件形式存储（带权限保护）"""
    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = None
        self.load()

    def load(self):
        """从文件加载 API Key"""
        config_file = self.config_dir / "config.json"
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.api_key = data.get("api_key", "")
            except Exception:
                self.api_key = ""
        else:
            self.api_key = ""

    def save(self, api_key):
        """保存 API Key 到文件，并设置权限为仅当前用户读写"""
        self.api_key = api_key
        try:
            config_file = self.config_dir / "config.json"
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump({"api_key": api_key}, f, indent=2)
            # 设置文件权限为仅当前用户读写（Unix-like）
            if sys.platform != "win32":
                os.chmod(config_file, 0o600)
            return True
        except Exception as e:
            print(f"保存配置文件失败: {e}")
            return False

# ========== 缓存管理（线程安全、自动定时保存）==========
class CacheManager:
    def __init__(self, log_callback=None):
        self.cache_file = CACHE_FILE
        self.cache = {}
        self.dirty = False
        self.lock = threading.Lock()
        self.log_callback = log_callback or (lambda msg: None)
        self._stop_auto_save = threading.Event()
        self._auto_save_thread = None
        self.load()
        self._start_auto_save()

    def load(self):
        """从文件加载缓存"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
                self.log_callback(f"已加载缓存，共 {len(self.cache)} 条记录")
            except Exception as e:
                self.log_callback(f"加载缓存失败: {e}")
                self.cache = {}

    def _start_auto_save(self):
        """启动自动保存线程"""
        def auto_save_loop():
            while not self._stop_auto_save.wait(CACHE_AUTO_SAVE_INTERVAL):
                self.save()
        self._auto_save_thread = threading.Thread(target=auto_save_loop, daemon=True)
        self._auto_save_thread.start()
        # 注册程序退出时强制保存
        atexit.register(self.save, force=True)

    def stop_auto_save(self):
        """停止自动保存线程（通常不需要手动调用）"""
        self._stop_auto_save.set()
        if self._auto_save_thread and self._auto_save_thread.is_alive():
            self._auto_save_thread.join(timeout=1)

    def save(self, force=False):
        """保存缓存到文件（如果数据有变动）"""
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
            return self.cache.get(key)

    def set(self, key, value):
        with self.lock:
            self.cache[key] = value
            self.dirty = True

    def set_batch(self, items):
        """批量设置缓存"""
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

# ========== 翻译器（支持并发批次、智能重试）==========
class Translator:
    def __init__(self, api_key, cache_mgr, log_callback):
        self.api_key = api_key
        self.cache = cache_mgr
        self.log = log_callback
        self.stop_event = threading.Event()
        self.url = "https://api.deepseek.com/v1/chat/completions"

    def translate_batch(self, texts):
        """
        批量翻译英文短语列表，返回对应的中文翻译列表（顺序一致）
        利用缓存，并发执行未缓存的批次
        """
        results = [None] * len(texts)
        # 分离已缓存和未翻译
        to_translate_indices = []
        for i, t in enumerate(texts):
            cached = self.cache.get(t)
            if cached is not None:
                results[i] = cached
            else:
                to_translate_indices.append(i)

        if not to_translate_indices:
            return results

        # 需要翻译的文本列表
        need_translate = [texts[i] for i in to_translate_indices]

        # 分批
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

        # 合并结果并更新缓存
        for batch_idx, batch in enumerate(batches):
            translations = batch_results[batch_idx]
            start = batch_idx * BATCH_SIZE
            items_to_cache = []
            for i, tgt in zip(to_translate_indices[start:start+len(batch)], translations):
                results[i] = tgt
                items_to_cache.append((texts[i], tgt))
            if items_to_cache:
                self.cache.set_batch(items_to_cache)

        # 自动保存由 CacheManager 定时处理，此处不再手动保存
        return results

    def _translate_single_batch(self, batch, batch_num, total):
        """翻译单个批次，带指数退避重试"""
        prompt = (
            "请将以下英文音效名称翻译成简体中文。"
            "以 JSON 数组格式返回，每个元素是一个字符串，直接对应输入的每个短语。"
            "不要包含任何其他内容，只输出 JSON 数组。\n"
            + "\n".join(batch)
        )

        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是一个专业音效命名助手，将英文翻译成简洁准确的中文。"},
                {"role": "user", "content": prompt}
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

                # 解析 JSON 数组
                try:
                    translations = json.loads(content)
                    if not isinstance(translations, list) or len(translations) != len(batch):
                        raise ValueError("返回格式不是长度匹配的数组")
                except json.JSONDecodeError:
                    # 尝试从文本中提取 JSON
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
        """驼峰命名拆分为空格分隔的单词（增强版，保留数字和特殊字符）"""
        s = re.sub(r'(?<=[a-zA-Z])(?=\d)', ' ', name)
        s = re.sub(r'(?<=\d)(?=[a-zA-Z])', ' ', s)
        s = re.sub(r'(?<!^)(?=[A-Z][a-z])', ' ', s)
        s = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', s)
        s = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', ' ', s)
        return s

    @staticmethod
    def generate_new_name(original_filename, chinese_translation, mode):
        base, ext = os.path.splitext(original_filename)
        if mode == "中文_英文":
            new_name = f"{chinese_translation}_{original_filename}"
        elif mode == "只中文":
            new_name = f"{chinese_translation}{ext}"
        elif mode == "英文_中文":
            new_name = f"{base}_{chinese_translation}{ext}"
        else:
            new_name = f"{chinese_translation}_{original_filename}"
        return new_name

# ========== GUI 应用程序 ==========
class TranslationApp:
    def __init__(self, root):
        self.root = root
        root.title("AI 音频批量翻译工具 (支持多格式)")
        root.geometry("720x620")
        root.resizable(True, True)

        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.progress_var = tk.IntVar(value=0)

        self.config_mgr = ConfigManager()
        self.cache_mgr = CacheManager(log_callback=self.log)
        self.translator = None

        self.create_widgets()
        self.process_log_queue()

        # 程序退出时确保缓存保存
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        # 文件夹选择
        tk.Label(self.root, text="选择包含音频文件的文件夹：").pack(pady=(10, 0))
        folder_frame = tk.Frame(self.root)
        folder_frame.pack(pady=5, fill="x", padx=10)
        self.folder_path = tk.StringVar()
        tk.Entry(folder_frame, textvariable=self.folder_path, width=60).pack(side=tk.LEFT, expand=True, fill="x")
        tk.Button(folder_frame, text="浏览...", command=self.choose_folder).pack(side=tk.RIGHT, padx=5)

        # API Key 输入
        key_frame = tk.Frame(self.root)
        key_frame.pack(pady=10, fill="x", padx=10)
        tk.Label(key_frame, text="API Key：").pack(side=tk.LEFT)
        self.api_key_var = tk.StringVar(value=self.config_mgr.api_key)
        self.key_entry = tk.Entry(key_frame, textvariable=self.api_key_var, width=50, show="*")
        self.key_entry.pack(side=tk.LEFT, expand=True, fill="x", padx=5)
        self.show_key_btn = tk.Button(key_frame, text="👁", command=self.toggle_key_visibility, width=3)
        self.show_key_btn.pack(side=tk.LEFT)
        tk.Button(key_frame, text="保存 Key", command=self.save_key).pack(side=tk.LEFT, padx=5)

        # 缓存信息
        cache_frame = tk.Frame(self.root)
        cache_frame.pack(pady=5, fill="x", padx=10)
        self.cache_label = tk.Label(cache_frame, text=f"缓存条目: {len(self.cache_mgr)}")
        self.cache_label.pack(side=tk.LEFT)
        tk.Button(cache_frame, text="清空缓存", command=self.clear_cache).pack(side=tk.LEFT, padx=5)
        tk.Button(cache_frame, text="打开缓存文件夹", command=self.open_cache_folder).pack(side=tk.LEFT, padx=5)

        # 命名模式
        mode_frame = tk.Frame(self.root)
        mode_frame.pack(pady=5, fill="x", padx=10)
        tk.Label(mode_frame, text="命名模式：").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="中文_英文")
        modes = ["中文_英文", "只中文", "英文_中文"]
        mode_menu = ttk.Combobox(mode_frame, textvariable=self.mode_var, values=modes, state="readonly", width=15)
        mode_menu.pack(side=tk.LEFT, padx=5)

        # 进度条
        progress_frame = tk.Frame(self.root)
        progress_frame.pack(pady=5, fill="x", padx=10)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.LEFT, expand=True, fill="x", padx=(0, 10))
        self.progress_label = tk.Label(progress_frame, text="0/0")
        self.progress_label.pack(side=tk.RIGHT)

        # 按钮区域
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=10)
        self.start_button = tk.Button(btn_frame, text="开始翻译重命名", command=self.start_rename, width=15)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.cancel_button = tk.Button(btn_frame, text="取消", command=self.cancel_rename, state=tk.DISABLED, width=10)
        self.cancel_button.pack(side=tk.LEFT, padx=5)

        # 日志文本框
        log_frame = tk.Frame(self.root)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tk.Label(log_frame, text="日志：").pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=15, wrap=tk.WORD)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill="y")
        self.log_text.pack(side=tk.LEFT, fill="both", expand=True)

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path.set(folder)

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
        if self.config_mgr.save(new_key):
            messagebox.showinfo("成功", "API Key 已保存")
        else:
            messagebox.showerror("错误", "保存失败，请检查权限")

    def clear_cache(self):
        if not messagebox.askyesno("确认", "确定清空翻译缓存吗？\n已翻译的短语将需要重新翻译。"):
            return
        self.cache_mgr.clear()
        self.cache_label.config(text=f"缓存条目: 0")
        self.log("缓存已清空。")

    def open_cache_folder(self):
        cache_path = self.cache_mgr.cache_file
        folder_path = cache_path.parent
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

    def start_rename(self):
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror("错误", "请输入 API Key")
            return

        folder = self.folder_path.get().strip()
        if not folder:
            messagebox.showerror("错误", "请选择文件夹")
            return

        mode = self.mode_var.get()

        if api_key != self.config_mgr.api_key:
            self.config_mgr.save(api_key)

        self.start_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self.stop_event.clear()

        self.log_text.delete(1.0, tk.END)

        self.thread = threading.Thread(target=self.rename_worker, args=(folder, mode, api_key))
        self.thread.daemon = True
        self.thread.start()

    def cancel_rename(self):
        self.stop_event.set()
        self.log("用户请求取消，正在停止...")
        self.cancel_button.config(state=tk.DISABLED)

    def rename_worker(self, folder, mode, api_key):
        try:
            all_files = os.listdir(folder)
            audio_files = []
            for f in all_files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_AUDIO_EXTS:
                    audio_files.append(f)

            if not audio_files:
                self.log(f"文件夹中没有支持的音频文件。支持的格式: {', '.join(SUPPORTED_AUDIO_EXTS)}")
                self.root.after(0, self.rename_finished, "提示", "文件夹中没有支持的音频文件")
                return

            self.log(f"找到 {len(audio_files)} 个音频文件")
            self.update_progress(0, len(audio_files), "准备中...")

            clean_names = []
            for f in audio_files:
                name_without_ext = os.path.splitext(f)[0]
                clean = FileProcessor.split_camel_case(name_without_ext)
                clean_names.append(clean)

            translator = Translator(api_key, self.cache_mgr, self.log)
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

            self.log("开始重命名文件...")
            renamed = 0
            errors = []
            total = len(audio_files)

            # 降低进度更新频率：每处理 min(10, total//100+1) 个文件更新一次
            update_step = max(1, min(10, total // 100 + 1))
            next_update = update_step

            for i, (filename, chinese) in enumerate(zip(audio_files, translations)):
                if self.stop_event.is_set():
                    self.log("用户取消重命名")
                    break

                try:
                    old_path = os.path.join(folder, filename)
                    new_name = FileProcessor.generate_new_name(filename, chinese, mode)
                    new_path = os.path.join(folder, new_name)

                    if os.path.normcase(old_path) == os.path.normcase(new_path):
                        self.log(f"跳过 {filename} (翻译后无变化)")
                        renamed += 1
                    else:
                        base, ext = os.path.splitext(new_name)
                        counter = 1
                        while os.path.exists(new_path):
                            new_name = f"{base}_{counter}{ext}"
                            new_path = os.path.join(folder, new_name)
                            counter += 1

                        os.rename(old_path, new_path)
                        renamed += 1
                        self.log(f"重命名: {filename} -> {new_name}")

                except Exception as e:
                    errors.append(f"{filename}: {e}")
                    self.log(f"重命名失败: {filename} - {e}")

                # 按步长更新进度
                if i + 1 >= next_update or i + 1 == total:
                    self.update_progress(i+1, total, "重命名中...")
                    next_update += update_step

            self.log(f"完成！成功重命名 {renamed} 个文件。")
            if errors:
                self.log("以下文件重命名失败：")
                for err in errors:
                    self.log(f"  {err}")
            self.root.after(0, self.rename_finished, "完成", f"成功重命名 {renamed} 个文件")

        except Exception as e:
            self.log(f"发生未预期错误: {e}")
            self.root.after(0, self.rename_finished, "错误", str(e))

    def rename_finished(self, title, message):
        self.start_button.config(state=tk.NORMAL)
        self.cancel_button.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="0/0")
        self.cache_label.config(text=f"缓存条目: {len(self.cache_mgr)}")
        messagebox.showinfo(title, message)

    def on_closing(self):
        """窗口关闭时停止自动保存并保存缓存"""
        self.cache_mgr.stop_auto_save()
        self.cache_mgr.save(force=True)
        self.root.destroy()

# ========== 启动 ==========
if __name__ == "__main__":
    root = tk.Tk()
    app = TranslationApp(root)
    root.mainloop()
