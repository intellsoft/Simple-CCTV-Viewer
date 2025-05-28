import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
import threading
import os
import time
from cryptography.fernet import Fernet
from PIL import Image, ImageTk
import json
import webbrowser # برای باز کردن لینک در مرورگر

# کلید ثابت برای رمزنگاری
ENCRYPTION_KEY = b'pRmgMa8T0INjEAfksaq2aafzoZXEuwKIyAd7eDZnpG8='
cipher_suite = Fernet(ENCRYPTION_KEY)
SETTINGS_FILE = "app_settings.enc"
THEME_FILE = "sun-valley.tcl" # نام فایل پوسته

class RTSPViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Simple CCTV Viewer") # عنوان پنجره اصلی بدون متن اضافی
        self.root.geometry("{0}x{1}+0+0".format(root.winfo_screenwidth(), root.winfo_screenheight()))
        self.root.state('zoomed')

        self.settings = {
            "rtsp_url": "rtsp://example.com/live",
            "save_path": os.path.join(os.path.expanduser("~"), "RTSP_Recordings"),
            "video_filename": "recording_{timestamp}.avi",
            "image_filename": "screenshot_{timestamp}.png",
            "theme": "dark"  # پوسته پیش‌فرض
        }

        self.recording = False
        self.video_writer = None
        
        self.thread = None 
        self.stop_event = threading.Event()

        self.capture_thread = None
        self.capture_stop_event = threading.Event()
        self.latest_raw_frame = None 
        self.frame_lock = threading.Lock()

        self.current_frame = None 
        self.tooltip_window = None
        self.last_frame_time = 0
        self.frame_rate = 30  
        
        self._tooltip_after_id = None
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "dark"))


        self.create_widgets() # ویجت‌ها از جمله theme_combo در اینجا ایجاد می‌شوند
        self.root.bind("<Map>", self.on_window_restore)
        
        self.video_label_width = 1 
        self.video_label_height = 1
        if hasattr(self, 'video_label'):
            self.video_label.bind("<Configure>", self.on_video_label_resize)

        self.load_settings() # این متد پوسته ذخیره شده را بارگیری و اعمال می‌کند

    def on_video_label_resize(self, event):
        self.video_label_width = event.width
        self.video_label_height = event.height

    def apply_theme(self, theme_name):
        """پوسته انتخاب شده را اعمال می‌کند."""
        try:
            if theme_name not in ["light", "dark"]:
                theme_name = "dark" # بازگشت به حالت پیش‌فرض در صورت نامعتبر بودن
            
            theme_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), THEME_FILE)
            if os.path.exists(theme_file_path):
                self.root.tk.call("set_theme", theme_name)
                self.settings["theme"] = theme_name # به‌روزرسانی تنظیمات داخلی
                if hasattr(self, 'status_var') and self.root.winfo_exists(): # بررسی وجود status_var
                     self.status_var.set(f"پوسته به {theme_name} تغییر یافت.")
            else:
                if hasattr(self, 'status_var') and self.root.winfo_exists():
                    self.status_var.set(f"فایل پوسته '{THEME_FILE}' یافت نشد.")
                print(f"فایل پوسته '{THEME_FILE}' در مسیر '{theme_file_path}' یافت نشد.")
        except tk.TclError as e:
            print(f"خطا در اعمال پوسته {theme_name}: {e}")
            if hasattr(self, 'status_var') and self.root.winfo_exists():
                self.status_var.set(f"خطا در اعمال پوسته: {theme_name}")
        except Exception as e_gen:
            print(f"خطای ناشناخته در اعمال پوسته {theme_name}: {e_gen}")


    def on_theme_change(self, event=None):
        """زمانی که کاربر پوسته را از کمبوباکس تغییر می‌دهد فراخوانی می‌شود."""
        new_theme = self.theme_var.get()
        self.apply_theme(new_theme)
        # self.settings["theme"] = new_theme # اطمینان از اینکه تنظیمات برای ذخیره‌سازی به‌روز است
                                         # save_settings این کار را با خواندن از theme_var انجام می‌دهد

    def create_widgets(self):
        header = ttk.Frame(self.root, padding=10)
        header.pack(fill=tk.X)
        # title_label حذف شد
        toolbar = ttk.Frame(header)
        toolbar.pack(side=tk.RIGHT) # چیدمان تولبار در سمت راست هدر
        
        # برای اینکه دکمه‌ها در مرکز هدر قرار گیرند، می‌توانیم هدر را به چند ستون تقسیم کنیم
        # یا از یک فریم دیگر در مرکز استفاده کنیم. فعلا ساده نگه می‌داریم.
        # header.columnconfigure(0, weight=1) # ستون خالی سمت چپ
        # header.columnconfigure(1, weight=0) # ستون برای تولبار
        # header.columnconfigure(2, weight=1) # ستون خالی سمت راست
        # toolbar.grid(row=0, column=1) # قرار دادن تولبار در ستون وسطی


        self.record_btn = ttk.Button(toolbar, text="شروع ضبط", command=self.toggle_recording)
        self.record_btn.pack(side=tk.LEFT, padx=5)
        self.snapshot_btn = ttk.Button(toolbar, text="ذخیره تصویر", command=self.take_snapshot)
        self.snapshot_btn.pack(side=tk.LEFT, padx=5)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.video_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.video_tab, text="نمایش زنده")
        self.video_label = ttk.Label(self.video_tab) 
        self.video_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_tab, text="تنظیمات")
        self.create_settings_form() # theme_var باید قبل از این تعریف شده باشد

        self.setup_about_tab()

        self.status_var = tk.StringVar(value="آماده")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.tooltip_var = tk.StringVar()
        self.tooltip_label = ttk.Label(
            self.root, textvariable=self.tooltip_var, background="#ffffe0",
            relief="solid", borderwidth=1, padding=(10, 5)
        )
        self.tooltip_label.place_forget()

    def setup_about_tab(self):
        frame = ttk.Frame(self.notebook, padding=(20, 10))
        self.notebook.add(frame, text="درباره من") 

        frame.columnconfigure(0, weight=1) 
        row_idx = 0

        name_label = ttk.Label(frame, text="من علی عباسپور هستم", font=("Arial", 14, "bold"), anchor="e", justify=tk.RIGHT)
        name_label.grid(row=row_idx, column=0, pady=(10, 5), sticky='ew')
        row_idx += 1

        desc_text = "تبدیل دوربین‌ مداربسته به ابزارهای خلاقانه. من به شما کمک می‌کنم تا با نرم افزارهای تخصصی، با تصویر دوربین مداربسته کارهای خارق العاده ای انجام دهید و از دوربین‌ خود بیشترین بهره را ببرید."
        desc_label = ttk.Label(frame, text=desc_text, wraplength=self.root.winfo_width() // 2, justify=tk.RIGHT, anchor="e")
        desc_label.grid(row=row_idx, column=0, pady=5, sticky='ew')
        row_idx += 1
        
        def rewrap_desc(event):
            new_wraplength = event.width - 40 
            if new_wraplength > 0 :
                 desc_label.config(wraplength=new_wraplength)
        frame.bind("<Configure>", rewrap_desc)

        link_url = "https://intellsoft.ir"
        link_label = ttk.Label(frame, text=link_url, foreground="blue", cursor="hand2", anchor="e", justify=tk.RIGHT)
        link_label.grid(row=row_idx, column=0, pady=(10, 5), sticky='ew')
        link_label.bind("<Button-1>", lambda e, url=link_url: self.open_link(url))
        row_idx += 1

    def open_link(self, url):
        try:
            webbrowser.open_new(url)
        except Exception as e:
            messagebox.showerror("خطا", f"امکان باز کردن لینک وجود نداشت: {e}", parent=self.root)

    def create_settings_form(self):
        form_frame = ttk.Frame(self.settings_tab, padding=20)
        form_frame.pack(fill=tk.BOTH, expand=True)
        
        # RTSP Link
        rtsp_frame = ttk.Frame(form_frame)
        rtsp_frame.pack(fill=tk.X, pady=5)
        rtsp_frame.columnconfigure(1, weight=1)
        ttk.Label(rtsp_frame, text="لینک RTSP:").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.rtsp_entry = ttk.Entry(rtsp_frame, width=60)
        self.rtsp_entry.grid(row=0, column=1, sticky="ew")
        
        # Save Path
        path_frame = ttk.Frame(form_frame)
        path_frame.pack(fill=tk.X, pady=5)
        path_frame.columnconfigure(1, weight=1)
        ttk.Label(path_frame, text="مسیر ذخیره‌سازی:").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.path_entry = ttk.Entry(path_frame, width=50)
        self.path_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(path_frame, text="مرور...", command=self.browse_save_path).grid(row=0, column=2, padx=(10, 0))
        
        # Video Filename
        video_frame = ttk.Frame(form_frame)
        video_frame.pack(fill=tk.X, pady=5)
        video_frame.columnconfigure(1, weight=1)
        ttk.Label(video_frame, text="قالب نام فایل ویدئو:").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.video_entry = ttk.Entry(video_frame, width=30)
        self.video_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(video_frame, text="(از {timestamp} برای زمان‌مهر استفاده کنید)").grid(row=0, column=2, padx=(10, 0), sticky="w")
        
        # Image Filename
        image_frame = ttk.Frame(form_frame)
        image_frame.pack(fill=tk.X, pady=5)
        image_frame.columnconfigure(1, weight=1)
        ttk.Label(image_frame, text="قالب نام فایل تصویر:").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.image_entry = ttk.Entry(image_frame, width=30)
        self.image_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(image_frame, text="(از {timestamp} برای زمان‌مهر استفاده کنید)").grid(row=0, column=2, padx=(10, 0), sticky="w")

        # Theme Selection
        theme_settings_frame = ttk.Frame(form_frame)
        theme_settings_frame.pack(fill=tk.X, pady=5)
        theme_settings_frame.columnconfigure(1, weight=1)
        ttk.Label(theme_settings_frame, text="پوسته برنامه:").grid(row=0, column=0, padx=(0, 10), sticky="w")
        # self.theme_var is already initialized in __init__
        self.theme_combo = ttk.Combobox(theme_settings_frame, textvariable=self.theme_var, values=["dark", "light"], state="readonly", width=27)
        self.theme_combo.grid(row=0, column=1, sticky="ew")
        self.theme_combo.bind("<<ComboboxSelected>>", self.on_theme_change)
        
        # Buttons Frame
        btn_frame = ttk.Frame(form_frame)
        btn_frame.pack(fill=tk.X, pady=20)
        ttk.Button(btn_frame, text="ذخیره تنظیمات", command=self.save_settings).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="بازنشانی", command=self.reset_settings).pack(side=tk.RIGHT, padx=5)

    def browse_save_path(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, path)

    def load_settings(self):
        loaded_successfully = False
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'rb') as f:
                    encrypted_data = f.read()
                decrypted_data = cipher_suite.decrypt(encrypted_data)
                loaded_settings_data = json.loads(decrypted_data.decode())
                # Merge loaded settings with defaults, giving precedence to loaded ones
                for key, value in loaded_settings_data.items():
                    self.settings[key] = value
                loaded_successfully = True
            except Exception as e:
                if self.root.winfo_exists():
                    self.status_var.set(f"خطا در بارگیری تنظیمات: {str(e)}. استفاده از پیش‌فرض‌ها.")
        
        # Populate UI elements from self.settings
        self.rtsp_entry.delete(0, tk.END)
        self.rtsp_entry.insert(0, self.settings.get("rtsp_url", "rtsp://example.com/live"))
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, self.settings.get("save_path", os.path.join(os.path.expanduser("~"), "RTSP_Recordings")))
        self.video_entry.delete(0, tk.END)
        self.video_entry.insert(0, self.settings.get("video_filename", "recording_{timestamp}.avi"))
        self.image_entry.delete(0, tk.END)
        self.image_entry.insert(0, self.settings.get("image_filename", "screenshot_{timestamp}.png"))

        # Load and apply theme
        loaded_theme = self.settings.get("theme", "dark") # Default to dark if not in settings
        self.theme_var.set(loaded_theme) # Update combobox variable
        self.apply_theme(loaded_theme) # Apply the theme

        try:
            save_path_val = self.settings.get("save_path")
            if save_path_val and not os.path.exists(save_path_val):
                os.makedirs(save_path_val)
        except Exception as e_dir:
            if self.root.winfo_exists():
                self.status_var.set(f"خطا در ایجاد پوشه ذخیره‌سازی: {str(e_dir)}")
                self.show_tooltip(f"خطا در ایجاد پوشه: {self.settings.get('save_path')}")

        if loaded_successfully and self.root.winfo_exists():
            self.status_var.set("تنظیمات بارگیری شد")
        
        self.start_stream() # Start stream after settings are loaded

    def save_settings(self):
        current_rtsp_url = self.settings.get("rtsp_url")
        
        # Update self.settings directly from UI elements before saving
        self.settings["rtsp_url"] = self.rtsp_entry.get()
        self.settings["save_path"] = self.path_entry.get()
        self.settings["video_filename"] = self.video_entry.get()
        self.settings["image_filename"] = self.image_entry.get()
        self.settings["theme"] = self.theme_var.get() # Get theme from combobox

        try:
            save_path_val = self.settings["save_path"]
            if save_path_val and not os.path.exists(save_path_val):
                os.makedirs(save_path_val)
            
            json_data = json.dumps(self.settings).encode()
            encrypted_data = cipher_suite.encrypt(json_data)
            with open(SETTINGS_FILE, 'wb') as f:
                f.write(encrypted_data)
            
            if self.root.winfo_exists():
                self.status_var.set("تنظیمات با موفقیت ذخیره شد")
                self.show_tooltip("تنظیمات با موفقیت ذخیره شد")
            
            # Apply theme if it was changed through combobox and then saved
            # self.apply_theme(self.settings["theme"]) # Already applied by on_theme_change

            capture_is_alive = self.capture_thread and self.capture_thread.is_alive()
            if current_rtsp_url != self.settings["rtsp_url"] or not capture_is_alive:
                self.start_stream()
        except Exception as e:
            if self.root.winfo_exists():
                self.status_var.set(f"خطا در ذخیره تنظیمات: {str(e)}")
                self.show_tooltip(f"خطا در ذخیره تنظیمات: {str(e)}")

    def reset_settings(self):
        # Reset to default values
        default_save_path = os.path.join(os.path.expanduser("~"), "RTSP_Recordings")
        self.settings = {
            "rtsp_url": "rtsp://example.com/live",
            "save_path": default_save_path,
            "video_filename": "recording_{timestamp}.avi",
            "image_filename": "screenshot_{timestamp}.png",
            "theme": "dark" 
        }
        
        # Update UI elements
        self.rtsp_entry.delete(0, tk.END)
        self.rtsp_entry.insert(0, self.settings["rtsp_url"])
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, self.settings["save_path"])
        self.video_entry.delete(0, tk.END)
        self.video_entry.insert(0, self.settings["video_filename"])
        self.image_entry.delete(0, tk.END)
        self.image_entry.insert(0, self.settings["image_filename"])
        self.theme_var.set(self.settings["theme"])
        
        self.apply_theme(self.settings["theme"]) # Apply the default theme
        self.save_settings() # Save reset settings and potentially restart stream

    def _capture_frames_thread(self):
        cap = None
        # Fetch rtsp_url from self.settings at the start of each connection attempt
        # to ensure it's up-to-date if changed via settings tab.
        
        while not self.capture_stop_event.is_set():
            current_rtsp_url = self.settings.get("rtsp_url", "") # Get latest URL

            if cap is None or not cap.isOpened():
                if self.root.winfo_exists():
                    self.root.after(0, lambda url=current_rtsp_url: self.status_var.set(f"درحال اتصال به {url}..."))
                try:
                    if cap: cap.release()
                    cap = cv2.VideoCapture(current_rtsp_url) # Use the latest URL
                    if not cap.isOpened():
                        if cap: cap.release()
                        cap = None
                        if self.root.winfo_exists():
                            self.root.after(0, lambda url=current_rtsp_url: self.status_var.set(f"اتصال به {url} ناموفق بود. تلاش مجدد طی 5 ثانیه..."))
                        for _ in range(50): # 5 seconds
                            if self.capture_stop_event.is_set(): break
                            time.sleep(0.1)
                        if self.capture_stop_event.is_set(): break
                        continue
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 5)
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps_cap = cap.get(cv2.CAP_PROP_FPS)
                    self.frame_rate = int(fps_cap if fps_cap and fps_cap > 0 else 30)
                    if self.root.winfo_exists():
                        self.root.after(0, lambda w=width, h=height, fr=self.frame_rate: self.status_var.set(f"متصل شد. ({w}x{h} @ {fr}fps)"))
                except Exception as e:
                    if cap: cap.release()
                    cap = None
                    if self.root.winfo_exists():
                        self.root.after(0, lambda err=str(e): self.status_var.set(f"خطا در اتصال: {err}. تلاش مجدد..."))
                    for _ in range(50): # 5 seconds
                        if self.capture_stop_event.is_set(): break
                        time.sleep(0.1)
                    if self.capture_stop_event.is_set(): break
                    continue
            if cap and cap.isOpened():
                ret, frame = cap.read()
                if self.capture_stop_event.is_set(): break
                if ret and frame is not None and frame.size > 0:
                    with self.frame_lock:
                        self.latest_raw_frame = frame
                elif not ret: 
                    if self.root.winfo_exists():
                         self.root.after(0, self.status_var.set("خطا در دریافت فریم یا پایان استریم. تلاش برای اتصال مجدد..."))
                    if cap: cap.release()
                    cap = None
                    time.sleep(0.5) 
            else: 
                if cap: cap.release()
                cap = None
                time.sleep(1) 
        if cap: cap.release()
        with self.frame_lock:
            self.latest_raw_frame = None
        if self.root.winfo_exists():
            self.root.after(0, self.status_var.set("جریان ویدئو متوقف شد."))

    def start_stream(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=1.0)
        self.thread = None 
        self.stop_event.clear()
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_stop_event.set()
            self.capture_thread.join(timeout=2.0)
        self.capture_thread = None
        self.capture_stop_event.clear()
        with self.frame_lock:
            self.latest_raw_frame = None
        self.capture_thread = threading.Thread(target=self._capture_frames_thread, daemon=True)
        self.capture_thread.start()
        self.thread = threading.Thread(target=self.update_video, daemon=True)
        self.thread.start()
        if self.root.winfo_exists():
             self.status_var.set("در حال آماده‌سازی جریان ویدئو...")

    def stop_stream(self):
        self.capture_stop_event.set()
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        self.capture_thread = None
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
            if self.root.winfo_exists():
                self.record_btn.config(text="شروع ضبط")
            self.recording = False
        if self.root.winfo_exists():
            self.root.after(0, lambda: self.video_label.config(image=''))
            if hasattr(self.video_label, 'imgtk'): 
                self.video_label.imgtk = None

    def update_video(self):
        self.last_frame_time = time.time()
        while not self.stop_event.is_set():
            current_frame_to_process = None
            with self.frame_lock:
                if self.latest_raw_frame is not None:
                    current_frame_to_process = self.latest_raw_frame.copy()

            if current_frame_to_process is not None:
                self.current_frame = current_frame_to_process
                try:
                    frame_rgb = cv2.cvtColor(current_frame_to_process, cv2.COLOR_BGR2RGB)
                except cv2.error as e:
                    if self.root.winfo_exists():
                        self.root.after(0, lambda err=str(e): self.status_var.set(f"خطای تبدیل رنگ: {err}"))
                    time.sleep(0.01)
                    continue
                if not self.root.winfo_exists(): break
                
                img_pil = Image.fromarray(frame_rgb)
                
                label_w = self.video_label_width
                label_h = self.video_label_height

                if label_w > 1 and label_h > 1: 
                    img_w, img_h = img_pil.size
                    if img_w > 0 and img_h > 0:
                        label_aspect = label_w / label_h
                        img_aspect = img_w / img_h
                        if img_aspect > label_aspect:
                            new_w = label_w
                            new_h = int(new_w / img_aspect)
                        else:
                            new_h = label_h
                            new_w = int(new_h * img_aspect)
                        new_w = max(1, new_w)
                        new_h = max(1, new_h)
                        try:
                            resample_method = Image.Resampling.LANCZOS if hasattr(Image.Resampling, 'LANCZOS') else Image.ANTIALIAS
                            img_pil = img_pil.resize((new_w, new_h), resample_method)
                        except Exception as resize_err:
                            if self.root.winfo_exists():
                                self.root.after(0, lambda err=str(resize_err): self.status_var.set(f"خطا در تغییر اندازه تصویر: {err}"))
                imgtk = ImageTk.PhotoImage(image=img_pil)
                if self.root.winfo_exists():
                    self.video_label.config(image=imgtk)
                    self.video_label.imgtk = imgtk
                if self.recording and self.video_writer:
                    try:
                        self.video_writer.write(self.current_frame)
                    except Exception as e_write:
                        def update_status_on_record_error(err_msg):
                            if not self.root.winfo_exists(): return
                            self.recording = False
                            self.record_btn.config(text="شروع ضبط")
                            self.status_var.set(f"خطا در نوشتن فریم ضبط: {err_msg}")
                            self.show_tooltip(f"خطا در نوشتن فریم ضبط: {err_msg}")
                            if self.video_writer:
                                self.video_writer.release()
                                self.video_writer = None
                        if self.root.winfo_exists():
                            self.root.after(0, update_status_on_record_error, str(e_write))
            current_frame_rate = self.frame_rate if self.frame_rate > 0 else 30.0
            target_frame_duration = 1.0 / current_frame_rate
            elapsed_since_last_cycle = time.time() - self.last_frame_time
            sleep_needed = max(0, target_frame_duration - elapsed_since_last_cycle)
            sleep_interval = 0.005
            num_sleep_intervals = int(sleep_needed / sleep_interval)
            for _ in range(num_sleep_intervals):
                if self.stop_event.is_set(): break
                time.sleep(sleep_interval)
            if self.stop_event.is_set(): break
            remaining_sleep = sleep_needed - (num_sleep_intervals * sleep_interval)
            if remaining_sleep > 0 and not self.stop_event.is_set():
                 time.sleep(remaining_sleep)
            self.last_frame_time = time.time()
            if self.stop_event.is_set(): break
        if self.root.winfo_exists():
            self.root.after(0, lambda: self.video_label.config(image=''))
            if hasattr(self.video_label, 'imgtk'):
                self.video_label.imgtk = None

    def toggle_recording(self):
        if self.current_frame is None:
            if self.root.winfo_exists():
                self.show_tooltip("استریم فعال نیست یا فریمی برای ضبط وجود ندارد.")
                self.status_var.set("ضبط ناموفق: فریمی موجود نیست.")
            return
        if not self.recording:
            try:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = self.settings["video_filename"].format(timestamp=timestamp)
                save_path_dir = self.settings["save_path"]
                if not os.path.exists(save_path_dir):
                    os.makedirs(save_path_dir)
                full_save_path = os.path.join(save_path_dir, filename)
                frame_height, frame_width, _ = self.current_frame.shape 
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
                effective_frame_rate = float(self.frame_rate if self.frame_rate > 0 else 30.0)
                self.video_writer = cv2.VideoWriter(full_save_path, fourcc, effective_frame_rate, (frame_width, frame_height))
                if not self.video_writer.isOpened():
                    raise Exception(f"امکان ایجاد فایل ویدئو وجود ندارد: {full_save_path}")
                self.recording = True
                if self.root.winfo_exists():
                    self.record_btn.config(text="توقف ضبط")
                    self.status_var.set(f"در حال ضبط: {filename}")
                    self.show_tooltip(f"ضبط ویدئو شروع شد: {filename}")
            except Exception as e:
                self.recording = False
                if hasattr(self, 'record_btn') and self.root.winfo_exists():
                    self.record_btn.config(text="شروع ضبط")
                if self.root.winfo_exists():
                    self.status_var.set(f"خطا در شروع ضبط: {str(e)}")
                    self.show_tooltip(f"خطا در شروع ضبط: {str(e)}")
                if self.video_writer:
                    self.video_writer.release()
                    self.video_writer = None
        else:
            self.recording = False
            if hasattr(self, 'record_btn') and self.root.winfo_exists():
                self.record_btn.config(text="شروع ضبط")
            if self.video_writer:
                self.video_writer.release()
                self.video_writer = None
            if self.root.winfo_exists():
                self.status_var.set("ضبط متوقف شد")
                self.show_tooltip("ضبط ویدئو متوقف شد")

    def take_snapshot(self):
        if self.current_frame is not None:
            try:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = self.settings["image_filename"].format(timestamp=timestamp)
                save_path_dir = self.settings["save_path"]
                if not os.path.exists(save_path_dir):
                    os.makedirs(save_path_dir)
                full_save_path = os.path.join(save_path_dir, filename)
                cv2.imwrite(full_save_path, self.current_frame)
                if self.root.winfo_exists():
                    self.status_var.set(f"تصویر ذخیره شد: {filename}")
                    self.show_tooltip(f"تصویر ذخیره شد: {filename}")
            except Exception as e:
                 if self.root.winfo_exists():
                    self.status_var.set(f"خطا در ذخیره تصویر: {str(e)}")
                    self.show_tooltip(f"خطا در ذخیره تصویر: {str(e)}")
        else:
            if self.root.winfo_exists():
                self.status_var.set("هیچ فریمی برای ذخیره وجود ندارد")
                self.show_tooltip("هیچ فریمی برای ذخیره وجود ندارد")

    def show_tooltip(self, message):
        if not self.root.winfo_exists(): return
        self.tooltip_var.set(message)
        self.tooltip_label.place(relx=0.5, rely=0.95, anchor=tk.S)
        if self._tooltip_after_id:
            self.root.after_cancel(self._tooltip_after_id)
        self._tooltip_after_id = self.root.after(3000, self.hide_tooltip)

    def hide_tooltip(self):
        if not self.root.winfo_exists(): return
        self.tooltip_label.place_forget()
        self._tooltip_after_id = None

    def on_closing(self):
        self.stop_stream()
        if self._tooltip_after_id:
            if self.root.winfo_exists():
                self.root.after_cancel(self._tooltip_after_id)
            self._tooltip_after_id = None
        self.root.destroy()

    def on_window_restore(self, event=None):
        capture_running = self.capture_thread and self.capture_thread.is_alive()
        gui_running = self.thread and self.thread.is_alive()
        if not capture_running or not gui_running:
            if self.root.winfo_exists():
                self.root.after(100, self.start_stream) 

if __name__ == "__main__":
    root = tk.Tk()
    # پوسته اولیه قبل از ایجاد اپلیکیشن و بارگیری تنظیمات اعمال می‌شود
    initial_theme = "dark" # پیش‌فرض اولیه
    
    # تلاش برای خواندن پوسته ذخیره شده از فایل تنظیمات، اگر وجود داشته باشد
    # این کار برای جلوگیری از چشمک زدن پوسته هنگام بارگیری انجام می‌شود
    # اگر فایل تنظیمات یا پوسته در آن موجود نباشد، از initial_theme استفاده می‌شود
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'rb') as f_temp:
                encrypted_data_temp = f_temp.read()
            decrypted_data_temp = cipher_suite.decrypt(encrypted_data_temp)
            temp_settings = json.loads(decrypted_data_temp.decode())
            initial_theme = temp_settings.get("theme", "dark")
        except Exception:
            # در صورت خطا در خواندن فایل تنظیمات، از پوسته پیش‌فرض اولیه استفاده کن
            pass 

    try:
        theme_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), THEME_FILE)
        if os.path.exists(theme_file_path):
            root.tk.call("source", theme_file_path)
            root.tk.call("set_theme", initial_theme) # استفاده از پوسته اولیه یا ذخیره شده
        else:
            print(f"فایل تم '{THEME_FILE}' در مسیر '{theme_file_path}' یافت نشد. از تم پیش‌فرض استفاده می‌شود.")
            # در صورت عدم وجود فایل پوسته، می‌توان از پوسته‌های داخلی ttk استفاده کرد
            style = ttk.Style(root)
            available_themes = style.theme_names()
            if "clam" in available_themes: style.theme_use("clam")
            elif "alt" in available_themes: style.theme_use("alt")
            elif "default" in available_themes: style.theme_use("default")
            elif available_themes: style.theme_use(available_themes[0])
    except tk.TclError as e:
        print(f"خطای Tcl در هنگام اعمال تم: {e}. از تم پیش‌فرض استفاده می‌شود.")
    except Exception as e_theme: 
        print(f"خطای ناشناخته در اعمال تم: {e_theme}. از تم پیش‌فرض استفاده می‌شود.")

    app = RTSPViewerApp(root) # load_settings در داخل __init__ فراخوانی و پوسته را مجددا اعمال می‌کند
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
