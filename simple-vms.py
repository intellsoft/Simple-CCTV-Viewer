import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
import threading
import os
import time
from cryptography.fernet import Fernet
from PIL import Image, ImageTk
import json
import webbrowser
from onvif import ONVIFCamera
from urllib.parse import urlparse
import traceback
import sys

# کلید ثابت برای رمزنگاری
ENCRYPTION_KEY = b'pRmgMa8T0INjEAfksaq2aafzoZXEuwKIyAd7eDZnpG8='
cipher_suite = Fernet(ENCRYPTION_KEY)
SETTINGS_FILE = "app_settings.enc"

class RTSPViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Simple cctv viewer")
        self.root.geometry("{0}x{1}+0+0".format(root.winfo_screenwidth(), root.winfo_screenheight()))
        self.root.state('zoomed')

        # مقادیر پیش‌فرض تنظیمات با فیلدهای جدید برای RTSP
        self.settings = {
            "ip": "192.168.1.10",
            "port": "554",
            "username": "admin",
            "password": "",
            "rtsp_path": "cam/realmonitor?channel=1&subtype=0",
            "rtsp_url": "",  # این فیلد توسط برنامه ساخته می‌شود
            "save_path": os.path.join(os.path.expanduser("~"), "RTSP_Recordings"),
            "video_filename": "recording_{timestamp}.avi",
            "image_filename": "screenshot_{timestamp}.png",
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

        # اعمال استایل برای خوانا شدن Tooltip
        style = ttk.Style(self.root)
        style.configure("Tooltip.TLabel", padding=(10, 5), relief="solid", borderwidth=1)

        self.create_widgets()
        
        # --- اصلاحیه کلیدی ---
        # برنامه را مجبور می‌کنیم تا ابعاد ویجت‌ها را قبل از شروع استریم محاسبه کند
        self.root.update_idletasks()
        self.video_label_width = self.video_label.winfo_width()
        self.video_label_height = self.video_label.winfo_height()
        
        self.root.bind("<Map>", self.on_window_restore)
        self.video_label.bind("<Configure>", self.on_video_label_resize)

        self.load_settings()

    def on_video_label_resize(self, event):
        self.video_label_width = event.width
        self.video_label_height = event.height
        
    def create_widgets(self):
        # هدر اصلی دیگر دکمه‌ای ندارد
        header = ttk.Frame(self.root, padding=10)
        header.pack(fill=tk.X)
        
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # --- تب نمایش زنده (تغییر یافته) ---
        self.video_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.video_tab, text="نمایش زنده")
        
        # فریم اصلی برای ویدئو که در مرکز قرار می‌گیرد
        video_main_frame = ttk.Frame(self.video_tab)
        video_main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)  # اضافه کردن padding برای فضای بیشتر
        video_main_frame.grid_rowconfigure(0, weight=1)
        video_main_frame.grid_columnconfigure(0, weight=1)

        # تغییر به tk.Label برای کنترل بهتر اندازه تصویر
        self.video_label = tk.Label(video_main_frame, bg='black', bd=0) 
        self.video_label.grid(row=0, column=0, sticky="nsew")  # تغییر برای پر کردن فضای موجود

        # ایجاد تصویر اولیه با اندازه مناسب
        placeholder = Image.new('RGB', (800, 600), (0, 0, 0))
        self.placeholder_img = ImageTk.PhotoImage(placeholder)
        self.video_label.config(image=self.placeholder_img)
        self.video_label.image = self.placeholder_img  # نگه داشتن رفرنس

        # فریم برای دکمه‌های زیر ویدئو
        video_controls_frame = ttk.Frame(self.video_tab)
        video_controls_frame.pack(fill=tk.X, pady=10)
        
        # دکمه‌ها در یک فریم داخلی دیگر برای وسط‌چین شدن
        buttons_inner_frame = ttk.Frame(video_controls_frame)
        buttons_inner_frame.pack()

        self.record_btn = ttk.Button(buttons_inner_frame, text="شروع ضبط", command=self.toggle_recording)
        self.record_btn.pack(side=tk.LEFT, padx=5)
        self.snapshot_btn = ttk.Button(buttons_inner_frame, text="ذخیره تصویر", command=self.take_snapshot)
        self.snapshot_btn.pack(side=tk.LEFT, padx=5)

        # --- تب تنظیمات ---
        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_tab, text="تنظیمات")
        self.create_settings_form()

        # --- تب درباره من ---
        self.setup_about_tab()

        self.status_var = tk.StringVar(value="آماده")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Tooltip با استایل جدید برای خوانایی
        self.tooltip_var = tk.StringVar()
        self.tooltip_label = ttk.Label(
            self.root, textvariable=self.tooltip_var,
            style="Tooltip.TLabel"  # استفاده از استایل تعریف شده
        )
        self.tooltip_label.place_forget()

    def setup_about_tab(self):
        frame = ttk.Frame(self.notebook, padding=(20, 10))
        self.notebook.add(frame, text="درباره من") 

        frame.columnconfigure(0, weight=1) 
        row_idx = 0

        # Header
        name_label = ttk.Label(
            frame, 
            text="I'm Ali Abbaspor", 
            font=("Arial", 14, "bold"),
            anchor="center"
        )
        name_label.grid(row=row_idx, column=0, pady=(0, 15), sticky="ew")
        row_idx += 1

        # Bio text
        bio_text = (
            "I transform surveillance cameras into creative tools. With over 15 years of experience "
            "in video analytics and CCTV software development, I help you maximize your camera's potential. "
            "My specialty is developing custom software that turns ordinary surveillance systems into "
            "intelligent solutions for retail analytics, safety monitoring, and behavioral studies."
        )
        bio_label = ttk.Label(
            frame, 
            text=bio_text,
            wraplength=600,
            justify="center",
            font=("Arial", 10)
        )
        bio_label.grid(row=row_idx, column=0, pady=5, sticky="ew")
        row_idx += 1

        # Key capabilities
        capabilities = [
            "• Real-time object detection and tracking",
            "• Cross-camera people counting solutions",
            "• Custom ANPR (Automatic Number Plate Recognition)",
            "• AI-powered retail analytics",
            "• Behavior analysis through motion patterns"
        ]
        capa_label = ttk.Label(
            frame, 
            text="\n".join(capabilities),
            justify="center",
            padding=(10, 15)
        )
        capa_label.grid(row=row_idx, column=0, sticky="ew")
        row_idx += 1

        # Philosophy
        philosophy = (
            "I believe security cameras should go beyond surveillance. My mission is to transform "
            "passive recording devices into active business intelligence tools that generate insights "
            "and automate decisions."
        )
        phi_label = ttk.Label(
            frame, 
            text=philosophy,
            wraplength=550,
            font=("Arial", 9, "italic"),
            justify="center",
            foreground="#555555"
        )
        phi_label.grid(row=row_idx, column=0, pady=(10, 15), sticky="ew")
        row_idx += 1

        # Website link
        link_url = "https://intellsoft.ir"
        link_label = ttk.Label(
            frame, 
            text=link_url, 
            foreground="blue", 
            cursor="hand2", 
            font=("Arial", 9, "underline")
        )
        link_label.grid(row=row_idx, column=0, pady=(10, 5))
        link_label.bind("<Button-1>", lambda e, url=link_url: self.open_link(url))
        row_idx += 1

        # Dynamic resizing
        def rewrap_desc(event):
            new_wraplength = event.width - 40 
            if new_wraplength > 0:
                bio_label.config(wraplength=new_wraplength)
                phi_label.config(wraplength=new_wraplength)
        frame.bind("<Configure>", rewrap_desc)

    def open_link(self, url):
        try:
            webbrowser.open_new(url)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open link: {e}", parent=self.root)

    def create_settings_form(self):
        form_frame = ttk.Frame(self.settings_tab, padding=20)
        form_frame.pack(fill=tk.BOTH, expand=True)
        form_frame.columnconfigure(1, weight=1)

        # --- بخش جدید تنظیمات RTSP ---
        rtsp_group = ttk.LabelFrame(form_frame, text="اطلاعات اتصال RTSP", padding=15)
        rtsp_group.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 20))
        rtsp_group.columnconfigure(1, weight=1)
        
        # IP
        ttk.Label(rtsp_group, text="آی پی دوربین:").grid(row=0, column=0, padx=(0, 10), pady=5, sticky="w")
        self.ip_entry = ttk.Entry(rtsp_group)
        self.ip_entry.grid(row=0, column=1, sticky="ew")
        self.ip_entry.bind("<KeyRelease>", self._update_constructed_url_display)
        
        # Port
        ttk.Label(rtsp_group, text="پورت RTSP:").grid(row=1, column=0, padx=(0, 10), pady=5, sticky="w")
        self.port_entry = ttk.Entry(rtsp_group)
        self.port_entry.grid(row=1, column=1, sticky="ew")
        self.port_entry.bind("<KeyRelease>", self._update_constructed_url_display)
        
        # Username
        ttk.Label(rtsp_group, text="نام کاربری:").grid(row=2, column=0, padx=(0, 10), pady=5, sticky="w")
        self.user_entry = ttk.Entry(rtsp_group)
        self.user_entry.grid(row=2, column=1, sticky="ew")
        self.user_entry.bind("<KeyRelease>", self._update_constructed_url_display)
        
        # Password
        ttk.Label(rtsp_group, text="رمز عبور:").grid(row=3, column=0, padx=(0, 10), pady=5, sticky="w")
        self.pass_entry = ttk.Entry(rtsp_group, show="*")
        self.pass_entry.grid(row=3, column=1, sticky="ew")
        self.pass_entry.bind("<KeyRelease>", self._update_constructed_url_display)
        
        # RTSP Path
        ttk.Label(rtsp_group, text="مسیر RTSP:").grid(row=4, column=0, padx=(0, 10), pady=5, sticky="w")
        self.rtsp_path_entry = ttk.Entry(rtsp_group)
        self.rtsp_path_entry.grid(row=4, column=1, sticky="ew")
        self.rtsp_path_entry.bind("<KeyRelease>", self._update_constructed_url_display)
        
        # دکمه کشف خودکار آدرس RTSP
        self.discover_btn = ttk.Button(rtsp_group, text="کشف خودکار آدرس RTSP", command=self.discover_rtsp_url)
        self.discover_btn.grid(row=4, column=2, padx=(5, 0))
        
        # Constructed URL (Read-only)
        ttk.Label(rtsp_group, text="لینک نهایی:").grid(row=5, column=0, padx=(0, 10), pady=(15, 5), sticky="w")
        self.constructed_url_entry = ttk.Entry(rtsp_group, state="readonly")
        self.constructed_url_entry.grid(row=5, column=1, columnspan=2, sticky="ew", pady=(15, 5))


        # --- سایر تنظیمات ---
        other_group = ttk.LabelFrame(form_frame, text="تنظیمات ذخیره‌سازی", padding=15)
        other_group.grid(row=1, column=0, columnspan=2, sticky='ew')
        other_group.columnconfigure(1, weight=1)
        
        # Save Path
        ttk.Label(other_group, text="مسیر ذخیره‌سازی:").grid(row=0, column=0, padx=(0, 10), pady=5, sticky="w")
        self.path_entry = ttk.Entry(other_group, width=50)
        self.path_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(other_group, text="مرور...", command=self.browse_save_path).grid(row=0, column=2, padx=(5, 0))
        
        # Video Filename
        ttk.Label(other_group, text="قالب نام فایل ویدئو:").grid(row=1, column=0, padx=(0, 10), pady=5, sticky="w")
        self.video_entry = ttk.Entry(other_group)
        self.video_entry.grid(row=1, column=1, columnspan=2, sticky="ew")
        
        # Image Filename
        ttk.Label(other_group, text="قالب نام فایل تصویر:").grid(row=2, column=0, padx=(0, 10), pady=5, sticky="w")
        self.image_entry = ttk.Entry(other_group)
        self.image_entry.grid(row=2, column=1, columnspan=2, sticky="ew")
        
        # Buttons Frame
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=2, column=0, columnspan=2, sticky='e', pady=20)
        ttk.Button(btn_frame, text="ذخیره تنظیمات", command=self.save_settings).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="بازنشانی", command=self.reset_settings).pack(side=tk.RIGHT, padx=5)

    def discover_rtsp_url(self):
        """کشف خودکار آدرس RTSP با استفاده از پروتکل ONVIF"""
        ip = self.ip_entry.get()
        port = self.port_entry.get()
        username = self.user_entry.get()
        password = self.pass_entry.get()
        
        if not ip:
            self.show_tooltip("لطفاً آدرس IP دوربین را وارد کنید")
            return
            
        # استفاده از پورت پیش‌فرض ONVIF (80)
        onvif_port = 80
        
        try:
            # ایجاد اتصال به دوربین با استفاده از ONVIF
            self.status_var.set("در حال اتصال به دوربین از طریق ONVIF...")
            self.discover_btn.config(state=tk.DISABLED, text="در حال کشف...")
            
            # اجرای عملیات کشف در یک thread جداگانه
            threading.Thread(target=self._perform_onvif_discovery, 
                             args=(ip, onvif_port, username, password),
                             daemon=True).start()
            
        except Exception as e:
            error_msg = f"خطا در کشف آدرس: {str(e)}"
            self.status_var.set(error_msg)
            self.show_tooltip(error_msg)
            self.discover_btn.config(state=tk.NORMAL, text="کشف خودکار آدرس RTSP")
            print(f"ONVIF discovery error: {traceback.format_exc()}")

    def _perform_onvif_discovery(self, ip, port, username, password):
        """انجام عملیات کشف آدرس RTSP در یک thread جداگانه"""
        try:
            # ایجاد اتصال به دوربین با پورت ONVIF ثابت (80)
            camera = ONVIFCamera(ip, port, username, password)
            
            # دریافت سرویس مدیا
            media_service = camera.create_media_service()
            
            # دریافت پروفایل‌ها
            profiles = media_service.GetProfiles()
            
            if not profiles:
                self.root.after(0, lambda: self.status_var.set("هیچ پروفایلی یافت نشد"))
                self.root.after(0, lambda: self.show_tooltip("دوربین هیچ پروفایل ویدئویی ندارد"))
                return
                
            # استفاده از اولین پروفایل
            profile_token = profiles[0].token
            
            # دریافت آدرس استریم
            stream_uri = media_service.GetStreamUri({
                'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': 'RTSP'},
                'ProfileToken': profile_token
            })
            
            # تجزیه آدرس RTSP
            rtsp_url = stream_uri.Uri
            parsed = urlparse(rtsp_url)
            
            # استخراج مسیر RTSP (بدون پارامترهای احراز هویت)
            rtsp_path = parsed.path
            if rtsp_path.startswith('/'):
                rtsp_path = rtsp_path[1:]
                
            # اضافه کردن کوئری استرینگ اگر وجود داشته باشد
            if parsed.query:
                rtsp_path += '?' + parsed.query
                
            # به‌روزرسانی رابط کاربری در thread اصلی
            self.root.after(0, lambda: self._update_rtsp_path(rtsp_path))
            self.root.after(0, lambda: self.status_var.set("آدرس RTSP با موفقیت کشف شد"))
            self.root.after(0, lambda: self.show_tooltip(f"آدرس کشف شده: {rtsp_path}"))
            
        except Exception as e:
            error_msg = f"خطا در کشف آدرس: {str(e)}"
            self.root.after(0, lambda: self.status_var.set(error_msg))
            self.root.after(0, lambda: self.show_tooltip(error_msg))
            print(f"ONVIF discovery error: {traceback.format_exc()}")
        finally:
            self.root.after(0, lambda: self.discover_btn.config(state=tk.NORMAL, text="کشف خودکار آدرس RTSP"))

    def _update_rtsp_path(self, rtsp_path):
        """به‌روزرسانی فیلد مسیر RTSP با مقدار کشف شده"""
        self.rtsp_path_entry.delete(0, tk.END)
        self.rtsp_path_entry.insert(0, rtsp_path)
        self._update_constructed_url_display()

    def _build_rtsp_url(self):
        """لینک RTSP را از فیلدهای ورودی می‌سازد."""
        ip = self.ip_entry.get()
        port = self.port_entry.get()
        user = self.user_entry.get()
        password = self.pass_entry.get()
        path = self.rtsp_path_entry.get()

        auth_part = ""
        if user:
            auth_part = f"{user}:{password}@"
        
        port_part = ""
        if port:
            port_part = f":{port}"

        # حذف اسلش اضافی در ابتدای مسیر اگر وجود داشته باشد
        if path.startswith('/'):
            path = path[1:]
            
        return f"rtsp://{auth_part}{ip}{port_part}/{path}"

    def _update_constructed_url_display(self, event=None):
        """فیلد نمایش لینک ساخته شده را به‌روز می‌کند."""
        url = self._build_rtsp_url()
        self.constructed_url_entry.config(state="normal")
        self.constructed_url_entry.delete(0, tk.END)
        self.constructed_url_entry.insert(0, url)
        self.constructed_url_entry.config(state="readonly")

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
                for key, value in loaded_settings_data.items():
                    self.settings[key] = value
                loaded_successfully = True
            except Exception as e:
                if self.root.winfo_exists():
                    self.status_var.set(f"خطا در بارگیری تنظیمات: {str(e)}. استفاده از پیش‌فرض‌ها.")
        
        # Populate UI elements
        self.ip_entry.delete(0, tk.END); self.ip_entry.insert(0, self.settings.get("ip", "192.168.1.10"))
        self.port_entry.delete(0, tk.END); self.port_entry.insert(0, self.settings.get("port", "554"))
        self.user_entry.delete(0, tk.END); self.user_entry.insert(0, self.settings.get("username", "admin"))
        self.pass_entry.delete(0, tk.END); self.pass_entry.insert(0, self.settings.get("password", ""))
        self.rtsp_path_entry.delete(0, tk.END); self.rtsp_path_entry.insert(0, self.settings.get("rtsp_path", "cam/realmonitor?channel=1&subtype=0"))
        
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, self.settings.get("save_path", os.path.join(os.path.expanduser("~"), "RTSP_Recordings")))
        self.video_entry.delete(0, tk.END)
        self.video_entry.insert(0, self.settings.get("video_filename", "recording_{timestamp}.avi"))
        self.image_entry.delete(0, tk.END)
        self.image_entry.insert(0, self.settings.get("image_filename", "screenshot_{timestamp}.png"))
        
        self._update_constructed_url_display() # نمایش لینک ساخته شده اولیه

        try:
            save_path_val = self.settings.get("save_path")
            if save_path_val and not os.path.exists(save_path_val):
                os.makedirs(save_path_val)
        except Exception as e_dir:
            if self.root.winfo_exists():
                self.status_var.set(f"خطا در ایجاد پوشه ذخیره‌سازی: {str(e_dir)}")
        
        if loaded_successfully and self.root.winfo_exists():
            self.status_var.set("تنظیمات بارگیری شد")
        
        self.settings["rtsp_url"] = self._build_rtsp_url() # ساخت لینک برای اولین اجرا
        self.start_stream()

    def save_settings(self):
        current_rtsp_url = self.settings.get("rtsp_url")
        
        # Update self.settings from UI
        self.settings["ip"] = self.ip_entry.get()
        self.settings["port"] = self.port_entry.get()
        self.settings["username"] = self.user_entry.get()
        self.settings["password"] = self.pass_entry.get()
        self.settings["rtsp_path"] = self.rtsp_path_entry.get()
        self.settings["rtsp_url"] = self._build_rtsp_url() # ساخت لینک جدید
        self._update_constructed_url_display() # به‌روزرسانی نمایشگر لینک
        
        self.settings["save_path"] = self.path_entry.get()
        self.settings["video_filename"] = self.video_entry.get()
        self.settings["image_filename"] = self.image_entry.get()

        try:
            if self.settings["save_path"] and not os.path.exists(self.settings["save_path"]):
                os.makedirs(self.settings["save_path"])
            
            json_data = json.dumps(self.settings).encode()
            encrypted_data = cipher_suite.encrypt(json_data)
            with open(SETTINGS_FILE, 'wb') as f:
                f.write(encrypted_data)
            
            if self.root.winfo_exists():
                self.status_var.set("تنظیمات با موفقیت ذخیره شد")
                self.show_tooltip("تنظیمات با موفقیت ذخیره شد")
            
            capture_is_alive = self.capture_thread and self.capture_thread.is_alive()
            if current_rtsp_url != self.settings["rtsp_url"] or not capture_is_alive:
                self.start_stream()
        except Exception as e:
            if self.root.winfo_exists():
                self.status_var.set(f"خطا در ذخیره تنظیمات: {str(e)}")

    def reset_settings(self):
        default_save_path = os.path.join(os.path.expanduser("~"), "RTSP_Recordings")
        self.settings = {
            "ip": "192.168.1.10", "port": "554", "username": "admin", "password": "", 
            "rtsp_path": "cam/realmonitor?channel=1&subtype=0", "rtsp_url": "",
            "save_path": default_save_path,
            "video_filename": "recording_{timestamp}.avi",
            "image_filename": "screenshot_{timestamp}.png",
        }
        
        # Update UI elements
        self.load_settings() # ساده‌ترین راه برای بازنشانی UI و ساخت URL
        self.save_settings() # ذخیره تنظیمات بازنشانی شده

    def _capture_frames_thread(self):
        cap = None
        while not self.capture_stop_event.is_set():
            current_rtsp_url = self.settings.get("rtsp_url", "")
            if cap is None or not cap.isOpened():
                if self.root.winfo_exists():
                    self.root.after(0, lambda url=current_rtsp_url: self.status_var.set(f"درحال اتصال به {url}..."))
                try:
                    if cap: cap.release()
                    cap = cv2.VideoCapture(current_rtsp_url)
                    if not cap.isOpened():
                        if cap: cap.release()
                        cap = None
                        if self.root.winfo_exists():
                            self.root.after(0, lambda url=current_rtsp_url: self.status_var.set(f"اتصال به {url} ناموفق بود. تلاش مجدد..."))
                        time.sleep(5)
                        continue
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 5)
                    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps_cap = cap.get(cv2.CAP_PROP_FPS)
                    self.frame_rate = int(fps_cap if fps_cap and fps_cap > 0 else 30)
                    if self.root.winfo_exists():
                        self.root.after(0, lambda w=width, h=height, fr=self.frame_rate: self.status_var.set(f"متصل شد. ({w}x{h} @ {fr}fps)"))
                except Exception as e:
                    if cap: cap.release(); cap = None
                    if self.root.winfo_exists(): self.root.after(0, lambda err=str(e): self.status_var.set(f"خطا در اتصال: {err}. تلاش مجدد..."))
                    time.sleep(5)
                    continue
            if cap and cap.isOpened():
                ret, frame = cap.read()
                if self.capture_stop_event.is_set(): break
                if ret and frame is not None and frame.size > 0:
                    with self.frame_lock: self.latest_raw_frame = frame
                elif not ret: 
                    if self.root.winfo_exists(): self.root.after(0, self.status_var.set("خطا در دریافت فریم. اتصال مجدد..."))
                    if cap: cap.release(); cap = None
                    time.sleep(0.5) 
            else: 
                if cap: cap.release(); cap = None
                time.sleep(1) 
        if cap: cap.release()
        with self.frame_lock: self.latest_raw_frame = None
        if self.root.winfo_exists(): self.root.after(0, self.status_var.set("جریان ویدئو متوقف شد."))

    def start_stream(self):
        if self.thread and self.thread.is_alive(): self.stop_event.set(); self.thread.join(timeout=1.0)
        self.thread = None; self.stop_event.clear()
        if self.capture_thread and self.capture_thread.is_alive(): self.capture_stop_event.set(); self.capture_thread.join(timeout=2.0)
        self.capture_thread = None; self.capture_stop_event.clear()
        with self.frame_lock: self.latest_raw_frame = None
        self.capture_thread = threading.Thread(target=self._capture_frames_thread, daemon=True)
        self.capture_thread.start()
        self.thread = threading.Thread(target=self.update_video, daemon=True)
        self.thread.start()
        if self.root.winfo_exists(): self.status_var.set("در حال آماده‌سازی جریان ویدئو...")

    def stop_stream(self):
        self.capture_stop_event.set()
        if self.capture_thread and self.capture_thread.is_alive(): self.capture_thread.join(timeout=2.0)
        self.capture_thread = None
        self.stop_event.set()
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=1.0)
        self.thread = None
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
            if self.root.winfo_exists():
                self.record_btn.config(text="شروع ضبط")
            self.recording = False
        if self.root.winfo_exists():
            self.root.after(0, lambda: self.video_label.config(image=self.placeholder_img))  # بازگشت به تصویر اولیه
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
            time.sleep(sleep_needed)
            self.last_frame_time = time.time()
            if self.stop_event.is_set(): break
        if self.root.winfo_exists():
            self.root.after(0, lambda: self.video_label.config(image=self.placeholder_img))  # بازگشت به تصویر اولیه
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
    
    # استفاده از تم پیش‌فرض سیستم که معمولاً روشن است
    style = ttk.Style(root)
    available_themes = style.theme_names()
    if "clam" in available_themes: 
        style.theme_use("clam")
    else:
        # اگر تم clam وجود نداشت، از اولین تم موجود استفاده کنید
        style.theme_use(available_themes[0] if available_themes else "default")

    app = RTSPViewerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
