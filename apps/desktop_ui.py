"""Build the Tkinter desktop dashboard and its SARAH chat window."""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ai_core.SARAH import SarahV0_2
from ai_core.voice_io import LokalStemme
from apps.program_manager import ProgramManager


class SarahChatWindow(tk.Toplevel):
    """Integreret Chat-vindue til SARAH v0.2 med Home Assistant support."""

    def __init__(self, parent, sarah: SarahV0_2):
        super().__init__(parent)
        self.sarah = sarah
        self.title("Lokalsystemet - SARAH AI Chat")
        self.geometry("800x620")
        self.minsize(520, 400)
        self.configure(bg="#212121")
        self.request_in_progress = False
        self.speak_responses = tk.BooleanVar(self, value=True)

        # Chat historik visning
        self.chat_display = scrolledtext.ScrolledText(
            self, wrap=tk.WORD, bg="#2d2d2d", fg="#ffffff", font=("Consolas", 10), state='disabled'
        )
        self.chat_display.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        # Tag-farver til chatten
        self.chat_display.tag_config("user", foreground="#4fc3f7", font=("Consolas", 10, "bold"))
        self.chat_display.tag_config("sarah", foreground="#81c784", font=("Consolas", 10, "bold"))
        self.chat_display.tag_config("system", foreground="#ffb74d", font=("Consolas", 9, "italic"))

        tk.Checkbutton(
            self,
            text="Læs SARAHs svar højt med lokal dansk stemme",
            variable=self.speak_responses,
            bg="#212121",
            fg="#ffffff",
            selectcolor="#333333",
            activebackground="#212121",
            activeforeground="#ffffff",
        ).pack(anchor=tk.W, padx=10, pady=(0, 8))

        # Input felt og knap
        input_frame = tk.Frame(self, bg="#212121")
        input_frame.pack(padx=10, pady=(0, 10), fill=tk.X)

        self.entry_msg = tk.Entry(input_frame, bg="#333333", fg="#ffffff", insertbackground="white",
                                  font=("Segoe UI", 10))
        self.entry_msg.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.entry_msg.bind("<Return>", lambda event: self.send_message())

        self.send_button = tk.Button(input_frame, text="Send", command=self.send_message, bg="#388e3c", fg="white",
                                     relief=tk.FLAT, width=10)
        self.send_button.pack(side=tk.RIGHT)

        self.append_text("SYSTEM",
                         f"SARAH v0.2 er klar. HA status: {'Online' if self.sarah.ha_online else 'Offline'}\n",
                         "system")

    def append_text(self, sender, text, tag):
        self.chat_display.config(state='normal')
        self.chat_display.insert(tk.END, f"{sender}: ", tag)
        self.chat_display.insert(tk.END, f"{text}\n\n")
        self.chat_display.see(tk.END)
        self.chat_display.config(state='disabled')

    def send_message(self):
        if self.request_in_progress:
            return
        msg = self.entry_msg.get().strip()
        if not msg:
            return

        self.request_in_progress = True
        speak_response = self.speak_responses.get()
        self.entry_msg.delete(0, tk.END)
        self.append_text("Dig", msg, "user")
        self.entry_msg.config(state=tk.DISABLED)
        self.send_button.config(state=tk.DISABLED, text="Venter…")

        threading.Thread(
            target=self._process_ai_response,
            args=(msg, speak_response),
            daemon=True,
        ).start()

    def _process_ai_response(self, msg, speak_response):
        try:
            svar = self.sarah.snak(msg)
            if speak_response:
                self.after(0, lambda: self.append_text("SARAH", svar, "sarah"))
                try:
                    LokalStemme.sig_lokalt(svar)
                except RuntimeError as error:
                    message = str(error)
                    self.after(0, lambda: self.append_text("SYSTEM", message, "system"))
                self.after(0, self._enable_input)
            else:
                self.after(0, lambda: self._finish_response("SARAH", svar, "sarah"))
        except Exception as e:
            error = str(e)
            self.after(0, lambda: self._finish_response("SYSTEM", f"Fejl under beregning: {error}", "system"))

    def _finish_response(self, sender, text, tag):
        self.append_text(sender, text, tag)
        self._enable_input()

    def _enable_input(self):
        self.request_in_progress = False
        self.entry_msg.config(state=tk.NORMAL)
        self.send_button.config(state=tk.NORMAL, text="Send")
        self.entry_msg.focus_set()


class DashboardApp(tk.Tk):
    def __init__(self, hardware_lager_only=False):
        super().__init__()
        self.title("Lokalsystemet - Central Kontrol")
        self.geometry("600x540")
        self.minsize(440, 400)
        self.configure(bg="#2b2b2b")
        self.resizable(True, True)

        self.pm = ProgramManager()
        self.sarah = None

        # Style opsætning
        style = ttk.Style(self)
        style.theme_use('clam')

        self._build_ui()

        # Initialiser SARAH i baggrunden for hurtig opstart
        threading.Thread(target=self._init_sarah, daemon=True).start()

        if hardware_lager_only:
            self.open_hardware_lager()

    def _init_sarah(self):
        try:
            self.sarah = SarahV0_2(server_mode=True)
            status_text = "HA: Online" if self.sarah.ha_online else "HA: Offline"
            self.after(0,
                       lambda: self.lbl_status.config(text=f"System Status: SARAH Klar | {status_text}", fg="#81c784"))
        except Exception as e:
            self.after(0,
                       lambda: self.lbl_status.config(text=f"System Status: Fejl ved indlæsning ({e})", fg="#e57373"))

    def _build_ui(self):
        # Overskrift
        lbl_title = tk.Label(
            self, text="Lokalsystemet Dashboard", font=("Segoe UI", 20, "bold"), fg="#ffffff", bg="#2b2b2b"
        )
        lbl_title.pack(pady=(30, 20))

        # Knap-ramme
        frame_buttons = tk.Frame(self, bg="#2b2b2b")
        frame_buttons.pack(pady=10, fill=tk.BOTH, expand=True, padx=40)

        # Style til knapper
        btn_style = {
            "font": ("Segoe UI", 11),
            "bg": "#3c3f41",
            "fg": "#ffffff",
            "activebackground": "#515456",
            "activeforeground": "#ffffff",
            "relief": tk.RAISED,
            "bd": 1,
            "height": 2
        }

        # 1. Hardware Lager
        btn_hw = tk.Button(
            frame_buttons, text="Hardware Lager", command=self.open_hardware_lager, **btn_style
        )
        btn_hw.pack(fill=tk.X, pady=8)

        # 2. Musik Arkivar & Management
        btn_music = tk.Button(
            frame_buttons, text="Musik Arkivar & Manager", command=self.open_music_archiver, **btn_style
        )
        btn_music.pack(fill=tk.X, pady=8)

        # 3. SARAH AI Core & Smart Home
        btn_sarah = tk.Button(
            frame_buttons, text="SARAH AI & Smart Home Chat", command=self.open_sarah_chat, **btn_style
        )
        btn_sarah.pack(fill=tk.X, pady=8)

        # Statusbjælke i bunden
        # Statusbjælke i bunden
        self.lbl_status = tk.Label(
            self,
            text="System Status: Initialiserer SARAH & HA...",
            font=("Segoe UI", 9),
            fg="#ffb74d",
            bg="#212121",
            anchor="w",
            padx=10  # <-- Retttet fra px=10 til padx=10
        )
        self.lbl_status.pack(side=tk.BOTTOM, fill=tk.X, ipady=5)

    def open_hardware_lager(self):
        res = self.pm.start("hardwarelager")
        if "ikke" in res.lower():
            messagebox.showinfo("Hardware Lager", res)

    def open_music_archiver(self):
        res = self.pm.start("musikarkivar")
        if "ikke" in res.lower():
            messagebox.showinfo("Musik Arkivar", res)

    def open_sarah_chat(self):
        if not self.sarah:
            messagebox.showwarning("SARAH AI", "SARAH indlæses stadig i baggrunden. Prøv igen om et øjeblik.")
            return
        SarahChatWindow(self, self.sarah)


def start_skrivebord(hardware_lager=False):
    app = DashboardApp(hardware_lager_only=hardware_lager)
    app.mainloop()


if __name__ == "__main__":
    start_skrivebord()