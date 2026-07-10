import os
import sys
import threading
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText

# Optional: native OS drag & drop (right card). Degrades to click-to-browse.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False

# Add local path to import modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import converter
import signer
import verify

class UDFConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("UYAP UDF İşlemleri")
        self.root.geometry("960x760")

        # Center the window on screen
        self.center_window(960, 760)

        # Apply standard padding
        self.root.configure(padx=20, pady=20)

        # Style variables
        self.theme_var = tk.StringVar(value="flatly")

        # Common DLL list
        self.dll_paths = self.find_common_dlls()

        # UI State variables — Sol kart (Dönüştürme)
        self.input_file_path = tk.StringVar()
        self.output_file_path = tk.StringVar()
        # UI State variables — Sağ kart (Sadece İmzalama)
        self.sign_input_path = tk.StringVar()
        self.sign_output_path = tk.StringVar()
        # Akıllı kart ayarları (modal pencerede düzenlenir)
        self.dll_path = tk.StringVar(value=self.dll_paths[0] if self.dll_paths else "")
        self.pin_code = tk.StringVar()
        self.show_pin_var = tk.BooleanVar(value=False)
        self.is_running = False
        self.action_buttons = []
        self._card_win = None

        self.create_widgets()
        self.log_info("Uygulama başarıyla başlatıldı.")
        self.log_info("Sol kart: belge → UDF dönüştürme.  Sağ kart: hazır UDF'i olduğu gibi imzalama.")
        if not self.dll_paths:
            self.log_warn("Sistemde kurulu standart PKCS#11 DLL (AKiS/SafeNet vb.) bulunamadı. Akıllı Kart Ayarları'ndan elle DLL yolunu belirtin.")
        else:
            self.log_success(f"Sistemde {len(self.dll_paths)} adet PKCS#11 DLL kütüphanesi otomatik tespit edildi.")
        if not DND_AVAILABLE:
            self.log_warn("Sürükle-bırak modülü (tkinterdnd2) yüklü değil; sağ karta dosyayı tıklayarak seçebilirsiniz.")

    def center_window(self, width, height):
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def find_common_dlls(self):
        potential_paths = [
            r"C:\Windows\System32\akisp11.dll",
            r"C:\Program Files\Akis Kart Izleme Araci\akisp11.dll",
            r"C:\Program Files (x86)\Akis Kart Izleme Araci\akisp11.dll",
            r"C:\Windows\System32\etpkcs11.dll",
            r"C:\Program Files\SafeNet\Authentication\SAC\x64\IDPrimePKCS1164.dll",
            r"C:\Program Files\Sentrigo\SentrigoPKCS11\sentrigop11.dll",
            r"C:\Windows\System32\kisp11.dll"
        ]
        return [p for p in potential_paths if os.path.exists(p)]

    def create_widgets(self):
        # -------------------------------------------------------------
        # Header Section
        # -------------------------------------------------------------
        header_frame = tb.Frame(self.root)
        header_frame.pack(fill=X, pady=(0, 18))

        # App Title with modern icon representation
        title_label = tb.Label(
            header_frame,
            text="⚖ UYAP UDF İşlemleri",
            font=("Segoe UI", 18, "bold"),
            bootstyle=PRIMARY
        )
        title_label.pack(side=LEFT)

        # Right side controls: Akıllı Kart Ayarları + Tema
        right_ctrls = tb.Frame(header_frame)
        right_ctrls.pack(side=RIGHT)

        self.card_settings_btn = tb.Button(
            right_ctrls,
            text="🔑 Akıllı Kart Ayarları",
            bootstyle="warning-outline",
            command=self.open_card_settings,
            padding=(12, 6)
        )
        self.card_settings_btn.pack(side=LEFT, padx=(0, 14))

        tb.Label(right_ctrls, text="Tema: ", font=("Segoe UI", 9)).pack(side=LEFT, padx=(0, 4))
        theme_combo = tb.Combobox(
            right_ctrls,
            values=["flatly", "cosmo", "superhero", "darkly", "litera"],
            textvariable=self.theme_var,
            width=10,
            state="readonly"
        )
        theme_combo.pack(side=LEFT)
        theme_combo.bind("<<ComboboxSelected>>", self.change_theme)

        # -------------------------------------------------------------
        # İki kart: Sol = Dönüştürme, Sağ = Sadece İmzalama
        # -------------------------------------------------------------
        cards = tb.Frame(self.root)
        cards.pack(fill=X, pady=(0, 14))
        cards.columnconfigure(0, weight=1, uniform="cards")
        cards.columnconfigure(1, weight=1, uniform="cards")

        self._build_convert_card(cards)
        self._build_sign_card(cards)

        # Progress Spinner Indication
        self.progress = tb.Progressbar(self.root, mode="indeterminate", bootstyle=SUCCESS)

        # -------------------------------------------------------------
        # Logs / Status Console Section
        # -------------------------------------------------------------
        logs_lf = tb.Labelframe(self.root, text=" 📜 İşlem Günlüğü ve Doğrulama Raporu ", padding=10)
        logs_lf.pack(fill=BOTH, expand=True)

        self.log_text = ScrolledText(logs_lf, height=12, wrap=tk.WORD, font=("Consolas", 10))
        self.log_text.pack(fill=BOTH, expand=True)

        # Text tags for colorful logs
        self.log_text.text.tag_config("info", foreground="#0d6efd")
        self.log_text.text.tag_config("warn", foreground="#fd7e14")
        self.log_text.text.tag_config("error", foreground="#dc3545", font=("Consolas", 10, "bold"))
        self.log_text.text.tag_config("success", foreground="#198754", font=("Consolas", 10, "bold"))
        self.log_text.text.tag_config("bold", font=("Consolas", 10, "bold"))

    # -------------------------------------------------------------
    # Sol kart — Belge → UDF dönüştürme (+ isteğe bağlı imzalama)
    # -------------------------------------------------------------
    def _build_convert_card(self, parent):
        lf = tb.Labelframe(parent, text=" 📄 Dönüştür ", padding=15)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        tb.Label(lf, text="Word / PDF belgesini UYAP UDF biçimine dönüştürür.",
                 font=("Segoe UI", 9), bootstyle=SECONDARY, wraplength=380, justify=LEFT).pack(fill=X, pady=(0, 12))

        # Giriş dosyası
        tb.Label(lf, text="Giriş Dosyası (.docx, .doc, .pdf):", anchor=W).pack(fill=X)
        in_row = tb.Frame(lf)
        in_row.pack(fill=X, pady=(2, 10))
        self.input_entry = tb.Entry(in_row, textvariable=self.input_file_path)
        self.input_entry.pack(side=LEFT, fill=X, expand=True)
        tb.Button(in_row, text="Gözat...", bootstyle=SECONDARY,
                  command=self.browse_input_file).pack(side=LEFT, padx=(6, 0))

        # Çıkış dosyası
        tb.Label(lf, text="Çıkış UDF Dosyası (.udf):", anchor=W).pack(fill=X)
        out_row = tb.Frame(lf)
        out_row.pack(fill=X, pady=(2, 14))
        self.output_entry = tb.Entry(out_row, textvariable=self.output_file_path)
        self.output_entry.pack(side=LEFT, fill=X, expand=True)
        tb.Button(out_row, text="Gözat...", bootstyle=SECONDARY,
                  command=self.browse_output_file).pack(side=LEFT, padx=(6, 0))

        btns = tb.Frame(lf)
        btns.pack(fill=X)
        self.sign_btn = tb.Button(
            btns, text="✍ Dönüştür ve İmzala", bootstyle=SUCCESS,
            command=self.start_convert_and_sign, padding=10
        )
        self.sign_btn.pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        self.convert_only_btn = tb.Button(
            btns, text="📄 Sadece Dönüştür", bootstyle=INFO,
            command=self.start_convert_only, padding=10
        )
        self.convert_only_btn.pack(side=LEFT, fill=X, expand=True)

        self.action_buttons += [self.sign_btn, self.convert_only_btn]

    # -------------------------------------------------------------
    # Sağ kart — Hazır UDF'i olduğu gibi imzalama (sürükle-bırak)
    # -------------------------------------------------------------
    def _build_sign_card(self, parent):
        lf = tb.Labelframe(parent, text=" ✍ İmzala ", padding=15)
        lf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        tb.Label(lf, text="Hazır bir UDF dosyasını dönüştürmeden, olduğu gibi e-imza ile imzalar.",
                 font=("Segoe UI", 9), bootstyle=SECONDARY, wraplength=380, justify=LEFT).pack(fill=X, pady=(0, 12))

        # Sürükle-bırak / tıkla-seç alanı
        self.drop_zone = tk.Frame(lf, height=92, cursor="hand2",
                                  highlightthickness=2, bd=0)
        self.drop_zone.pack(fill=X, pady=(0, 10))
        self.drop_zone.pack_propagate(False)
        self._dz_idle = "#adb5bd"
        self._dz_active = "#198754"
        self.drop_zone.configure(highlightbackground=self._dz_idle,
                                 highlightcolor=self._dz_idle, bg="#f8f9fa")

        hint = "⤓  UDF dosyasını buraya sürükleyin" if DND_AVAILABLE else "📂  Tıklayıp UDF dosyası seçin"
        self.drop_label = tk.Label(self.drop_zone, text=hint + "\n(veya tıklayıp seçin)",
                                   bg="#f8f9fa", fg="#6c757d",
                                   font=("Segoe UI", 10), justify=CENTER)
        self.drop_label.pack(expand=True)

        for w in (self.drop_zone, self.drop_label):
            w.bind("<Button-1>", lambda e: self.browse_sign_input())

        if DND_AVAILABLE:
            try:
                self.drop_zone.drop_target_register(DND_FILES)
                self.drop_zone.dnd_bind("<<Drop>>", self.on_drop)
                self.drop_zone.dnd_bind("<<DropEnter>>", lambda e: self._set_drop_active(True))
                self.drop_zone.dnd_bind("<<DropLeave>>", lambda e: self._set_drop_active(False))
            except Exception:
                pass

        # Seçilen dosya
        tb.Label(lf, text="İmzalanacak UDF:", anchor=W).pack(fill=X)
        in_row = tb.Frame(lf)
        in_row.pack(fill=X, pady=(2, 10))
        self.sign_in_entry = tb.Entry(in_row, textvariable=self.sign_input_path)
        self.sign_in_entry.pack(side=LEFT, fill=X, expand=True)
        tb.Button(in_row, text="Gözat...", bootstyle=SECONDARY,
                  command=self.browse_sign_input).pack(side=LEFT, padx=(6, 0))

        # Çıkış dosyası
        tb.Label(lf, text="İmzalı Çıkış UDF:", anchor=W).pack(fill=X)
        out_row = tb.Frame(lf)
        out_row.pack(fill=X, pady=(2, 14))
        self.sign_out_entry = tb.Entry(out_row, textvariable=self.sign_output_path)
        self.sign_out_entry.pack(side=LEFT, fill=X, expand=True)
        tb.Button(out_row, text="Gözat...", bootstyle=SECONDARY,
                  command=self.browse_sign_output).pack(side=LEFT, padx=(6, 0))

        self.sign_only_btn = tb.Button(
            lf, text="🔐 UDF'i İmzala", bootstyle=SUCCESS,
            command=self.start_sign_udf, padding=10
        )
        self.sign_only_btn.pack(fill=X)
        self.action_buttons.append(self.sign_only_btn)

    def _set_drop_active(self, on):
        col = self._dz_active if on else self._dz_idle
        bg = "#eafaf1" if on else "#f8f9fa"
        self.drop_zone.configure(highlightbackground=col, highlightcolor=col, bg=bg)
        self.drop_label.configure(bg=bg)

    def on_drop(self, event):
        self._set_drop_active(False)
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        if not paths:
            return
        path = paths[0]
        if not path.lower().endswith(".udf"):
            self.log_warn(f"Sürüklenen dosya .udf değil: {os.path.basename(path)}")
            messagebox.showwarning("Geçersiz Dosya", "Lütfen yalnızca .udf uzantılı bir dosya bırakın.")
            return
        self._set_sign_input(path)

    # -------------------------------------------------------------
    # Akıllı Kart Ayarları — modal açılır pencere
    # -------------------------------------------------------------
    def open_card_settings(self):
        if self._card_win is not None and self._card_win.winfo_exists():
            self._card_win.lift()
            self._card_win.focus_force()
            return

        win = tb.Toplevel(self.root)
        win.title("🔑 E-İmza Akıllı Kart Ayarları")
        win.transient(self.root)
        win.resizable(False, False)
        win.configure(padx=20, pady=20)
        self._card_win = win

        tb.Label(win, text="E-İmza Akıllı Kart Ayarları", font=("Segoe UI", 13, "bold"),
                 bootstyle=PRIMARY).pack(anchor=W, pady=(0, 4))
        tb.Label(win, text="Bu ayarlar hem dönüştürüp imzalama hem de hazır UDF imzalama için kullanılır.",
                 font=("Segoe UI", 9), bootstyle=SECONDARY, wraplength=420, justify=LEFT).pack(anchor=W, pady=(0, 14))

        # DLL yolu
        tb.Label(win, text="PKCS#11 DLL Yolu:", anchor=W).pack(fill=X)
        dll_row = tb.Frame(win)
        dll_row.pack(fill=X, pady=(2, 12))
        dll_combo = tb.Combobox(dll_row, textvariable=self.dll_path, values=self.dll_paths, width=42)
        dll_combo.pack(side=LEFT, fill=X, expand=True)
        tb.Button(dll_row, text="Gözat...", bootstyle=SECONDARY,
                  command=self.browse_dll_file).pack(side=LEFT, padx=(6, 0))

        # PIN
        tb.Label(win, text="Kart PIN Kodu:", anchor=W).pack(fill=X)
        pin_row = tb.Frame(win)
        pin_row.pack(fill=X, pady=(2, 16))
        self.pin_entry = tb.Entry(pin_row, textvariable=self.pin_code, show="*", width=22)
        self.pin_entry.pack(side=LEFT)
        tb.Checkbutton(pin_row, text="PIN Göster", variable=self.show_pin_var,
                       command=self.toggle_pin_visibility,
                       bootstyle="round-toggle").pack(side=LEFT, padx=(12, 0))

        tb.Button(win, text="Tamam", bootstyle=SUCCESS, padding=8,
                  command=win.destroy).pack(fill=X)

        # Apply current show-pin state on (re)open
        self.toggle_pin_visibility()

        def _on_close():
            self._card_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)
        win.bind("<Destroy>", lambda e: setattr(self, "_card_win", None) if e.widget is win else None)

        # Center over parent
        win.update_idletasks()
        px = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        py = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(px, 0)}+{max(py, 0)}")
        win.grab_set()

    def change_theme(self, event=None):
        theme_name = self.theme_var.get()
        style = tb.Style()
        style.theme_use(theme_name)
        self.log_info(f"Arayüz teması '{theme_name}' olarak değiştirildi.")

    # -------------------------------------------------------------
    # Browse / dosya seçim yardımcıları
    # -------------------------------------------------------------
    def browse_input_file(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("Belgeler (.docx, .doc, .pdf)", "*.docx;*.doc;*.pdf"),
                       ("Word Belgeleri", "*.docx;*.doc"),
                       ("PDF Belgeleri", "*.pdf")]
        )
        if file_path:
            self.input_file_path.set(file_path)
            base, _ = os.path.splitext(file_path)
            self.output_file_path.set(base + ".udf")
            self.log_info(f"Giriş dosyası seçildi: {file_path}")

    def browse_output_file(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".udf",
            filetypes=[("UYAP Dokümanı (.udf)", "*.udf")]
        )
        if file_path:
            self.output_file_path.set(file_path)
            self.log_info(f"Çıkış dosyası seçildi: {file_path}")

    def browse_sign_input(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("UYAP Dokümanı (.udf)", "*.udf"), ("Tüm Dosyalar", "*.*")]
        )
        if file_path:
            self._set_sign_input(file_path)

    def _set_sign_input(self, path):
        self.sign_input_path.set(path)
        base, _ = os.path.splitext(path)
        self.sign_output_path.set(base + "_imzali.udf")
        self.drop_label.configure(text="✔  " + os.path.basename(path) + "\n(değiştirmek için tıklayın/sürükleyin)")
        self.log_info(f"İmzalanacak UDF seçildi: {path}")

    def browse_sign_output(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".udf",
            filetypes=[("UYAP Dokümanı (.udf)", "*.udf")]
        )
        if file_path:
            self.sign_output_path.set(file_path)
            self.log_info(f"İmzalı çıkış dosyası: {file_path}")

    def browse_dll_file(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("Kütüphane Dosyası (.dll)", "*.dll"), ("Tüm Dosyalar", "*.*")]
        )
        if file_path:
            self.dll_path.set(file_path)
            self.log_info(f"PKCS#11 DLL seçildi: {file_path}")

    def toggle_pin_visibility(self):
        if not hasattr(self, "pin_entry") or not self.pin_entry.winfo_exists():
            return
        self.pin_entry.config(show="" if self.show_pin_var.get() else "*")

    # -------------------------------------------------------------
    # Logging Methods
    # -------------------------------------------------------------
    def log_write(self, message, tag=None):
        self.log_text.text.config(state=NORMAL)
        if tag:
            self.log_text.text.insert(tk.END, message + "\n", tag)
        else:
            self.log_text.text.insert(tk.END, message + "\n")
        self.log_text.text.see(tk.END)
        self.log_text.text.config(state=DISABLED)

    def log_info(self, msg):
        self.log_write(f"[BİLGİ] {msg}", "info")

    def log_warn(self, msg):
        self.log_write(f"[UYARI] {msg}", "warn")

    def log_error(self, msg):
        self.log_write(f"[HATA] {msg}", "error")

    def log_success(self, msg):
        self.log_write(f"[BAŞARILI] {msg}", "success")

    # -------------------------------------------------------------
    # Background Thread Execution Control
    # -------------------------------------------------------------
    def set_ui_state(self, running):
        self.is_running = running
        if running:
            for b in self.action_buttons:
                b.config(state=DISABLED)
            self.progress.pack(fill=X, pady=(0, 10))
            self.progress.start()
        else:
            for b in self.action_buttons:
                b.config(state=NORMAL)
            self.progress.stop()
            self.progress.pack_forget()

    def _require_card(self):
        """Akıllı kart ayarları eksikse uyarır ve ayar penceresini açar."""
        if not self.dll_path.get().strip():
            messagebox.showerror("Eksik Bilgi", "Lütfen akıllı kart sürücüsü (PKCS#11 DLL) yolunu belirtin.")
            self.open_card_settings()
            return False
        if not self.pin_code.get().strip():
            messagebox.showerror("Eksik Bilgi", "Lütfen kart PIN kodunu girin.")
            self.open_card_settings()
            return False
        return True

    # ---- Sol kart: sadece dönüştür ----
    def start_convert_only(self):
        input_p = self.input_file_path.get().strip()
        output_p = self.output_file_path.get().strip()
        if not input_p or not output_p:
            messagebox.showerror("Eksik Bilgi", "Lütfen giriş ve çıkış dosyalarını seçin.")
            return
        self.set_ui_state(True)
        threading.Thread(target=self.run_convert_only, args=(input_p, output_p), daemon=True).start()

    def run_convert_only(self, input_p, output_p):
        try:
            self.log_info(f"Dönüştürme başlatılıyor: {os.path.basename(input_p)}...")
            xml_bytes = converter.convert_to_udf_xml(input_p)
            self.log_success("Dosya içeriği başarıyla UDF XML yapısına dönüştürüldü.")
            with zipfile.ZipFile(output_p, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("content.xml", xml_bytes)
            self.log_success(f"Dönüştürülen (İmzasız) UDF başarıyla kaydedildi: {output_p}")
            messagebox.showinfo("Dönüşüm Tamamlandı", "Dosya imzasız UDF olarak başarıyla dönüştürüldü.")
        except Exception as e:
            self.log_error(f"Dönüştürme sırasında hata oluştu: {str(e)}")
            messagebox.showerror("Hata", f"Dönüştürme Hatası:\n{str(e)}")
        finally:
            self.root.after(0, lambda: self.set_ui_state(False))

    # ---- Sol kart: dönüştür ve imzala ----
    def start_convert_and_sign(self):
        input_p = self.input_file_path.get().strip()
        output_p = self.output_file_path.get().strip()
        if not input_p or not output_p:
            messagebox.showerror("Eksik Bilgi", "Lütfen giriş ve çıkış dosyalarını seçin.")
            return
        if not self._require_card():
            return
        dll_p = self.dll_path.get().strip()
        pin = self.pin_code.get().strip()
        self.set_ui_state(True)
        threading.Thread(target=self.run_convert_and_sign, args=(input_p, output_p, dll_p, pin), daemon=True).start()

    def run_convert_and_sign(self, input_p, output_p, dll_p, pin):
        try:
            self.log_info(f"Dosya dönüştürülüyor: {os.path.basename(input_p)}...")
            xml_bytes = converter.convert_to_udf_xml(input_p)
            self.log_success("Dosya içeriği başarıyla UDF XML yapısına dönüştürüldü.")

            self.log_info("E-İmza akıllı kart bağlantısı kuruluyor ve imzalama işlemi başlatılıyor...")
            signer.sign_udf(dll_p, pin, xml_bytes, output_p)
            self.log_success(f"İmzalı UDF başarıyla kaydedildi: {output_p}")

            self._verify_and_report(output_p, "Belge başarıyla dönüştürüldü, e-imza ile imzalandı ve doğrulamadan geçti!")
        except Exception as e:
            self.log_error(f"İşlem sırasında hata oluştu: {str(e)}")
            messagebox.showerror("Hata", f"Dönüştürme ve İmzalama Hatası:\n{str(e)}")
        finally:
            self.root.after(0, lambda: self.set_ui_state(False))

    # ---- Sağ kart: hazır UDF'i olduğu gibi imzala ----
    def start_sign_udf(self):
        input_p = self.sign_input_path.get().strip()
        output_p = self.sign_output_path.get().strip()
        if not input_p:
            messagebox.showerror("Eksik Bilgi", "Lütfen imzalanacak UDF dosyasını sürükleyin veya seçin.")
            return
        if not input_p.lower().endswith(".udf"):
            messagebox.showerror("Geçersiz Dosya", "Lütfen geçerli bir .udf dosyası seçin.")
            return
        if not os.path.exists(input_p):
            messagebox.showerror("Bulunamadı", "Seçilen UDF dosyası bulunamadı.")
            return
        if not output_p:
            base, _ = os.path.splitext(input_p)
            output_p = base + "_imzali.udf"
            self.sign_output_path.set(output_p)
        if not self._require_card():
            return
        dll_p = self.dll_path.get().strip()
        pin = self.pin_code.get().strip()
        self.set_ui_state(True)
        threading.Thread(target=self.run_sign_udf, args=(input_p, output_p, dll_p, pin), daemon=True).start()

    def run_sign_udf(self, input_p, output_p, dll_p, pin):
        try:
            self.log_info(f"Hazır UDF olduğu gibi imzalanıyor: {os.path.basename(input_p)}...")
            self.log_info("E-İmza akıllı kart bağlantısı kuruluyor...")
            signer.sign_existing_udf(dll_p, pin, input_p, output_p)
            self.log_success(f"İmzalı UDF başarıyla kaydedildi: {output_p}")

            self._verify_and_report(output_p, "UDF başarıyla e-imza ile imzalandı ve doğrulamadan geçti!")
        except Exception as e:
            self.log_error(f"İmzalama sırasında hata oluştu: {str(e)}")
            messagebox.showerror("Hata", f"İmzalama Hatası:\n{str(e)}")
        finally:
            self.root.after(0, lambda: self.set_ui_state(False))

    # ---- Ortak doğrulama + rapor ----
    def _verify_and_report(self, output_p, ok_message):
        self.log_info("Üretilen UDF paketinin imzası ve veri bütünlüğü doğrulanıyor...")
        success, verify_logs = verify.verify_udf_log(output_p)
        for vlog in verify_logs:
            if "HATA" in vlog:
                self.log_error(vlog)
            elif "OK" in vlog or "TEBRİKLER" in vlog:
                self.log_success(vlog)
            else:
                self.log_write(vlog)
        if success:
            messagebox.showinfo("İşlem Başarılı", ok_message)
        else:
            messagebox.showwarning(
                "Doğrulama Hatası",
                "UDF oluşturuldu ve imzalandı fakat imza doğrulama testinden geçemedi!\n"
                "Lütfen işlem günlüklerini inceleyin."
            )

def main():
    # Set default theme; tkinterdnd2 varsa kök pencereyi DnD destekli oluştur.
    if DND_AVAILABLE:
        try:
            root = TkinterDnD.Tk()
            style = tb.Style(theme="flatly")
        except Exception:
            style = tb.Style(theme="flatly")
            root = style.master
    else:
        style = tb.Style(theme="flatly")
        root = style.master

    app = UDFConverterApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
