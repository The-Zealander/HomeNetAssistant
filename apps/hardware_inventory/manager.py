"""Present the local hardware inventory in a searchable desktop window."""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import customtkinter as ctk

from apps.hardware_inventory.storage import DATA_FIL, HardwareLagerDB

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class HardwareLagerVindue(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Hardware Lager - Lokalsystemet")
        self.geometry("1350x800")

        # Databasen forbindes
        self.db = HardwareLagerDB(DATA_FIL)
        self.data = self.db.load_data()
        self.rediger_id = None

        # Core Variabler
        self.kategori_var = ctk.StringVar(value="Andet")
        self.brand_var = ctk.StringVar()
        self.navn_var = ctk.StringVar()
        self.release_var = ctk.StringVar()  # Nu rykket op som Core
        self.antal_var = ctk.StringVar(value="1")
        self.status_var = ctk.StringVar(value="Ledig på lager")

        # Avancerede Variabler (Metadata)
        self.specs_var = ctk.StringVar()
        self.tags_var = ctk.StringVar()
        self.purpose_var = ctk.StringVar()
        self.extra_vars = {}

        self.byg_ui()
        self.opdater_historik_lister()
        self.opdater_treeview()

    def byg_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ==================== VENSTRE PANEL (Input) ====================
        # Fast bredde så felterne ikke hopper rundt
        input_frame = ctk.CTkFrame(self, width=380, corner_radius=0)
        input_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        input_frame.grid_columnconfigure(1, weight=1)
        input_frame.grid_propagate(False)  # Forhindrer panelet i at ændre bredde

        ctk.CTkLabel(input_frame, text="Tilføj Enhed", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0,
                                                                                                      columnspan=2,
                                                                                                      pady=(20, 20),
                                                                                                      padx=20,
                                                                                                      sticky="w")

        # 1. BASALE FELTER (Altid synlige)
        basale_felter = [
            ("Kategori:", self.kategori_var, "readonly", self.db.KATEGORIER),
            ("Brand:", self.brand_var, "skrivbar", []),
            ("Model/Navn:", self.navn_var, "skrivbar", []),
            ("Årgang:", self.release_var, "entry", None),  # Årgang er nu central
            ("Antal:", self.antal_var, "entry", None),
            ("Status:", self.status_var, "readonly", self.db.STATUSER),
        ]

        self.dynamiske_lister = {}
        row_idx = 1

        for label_tekst, var, w_type, values in basale_felter:
            ctk.CTkLabel(input_frame, text=label_tekst).grid(row=row_idx, column=0, padx=20, pady=10, sticky="w")

            if w_type == "readonly":
                w = ctk.CTkComboBox(input_frame, variable=var, values=values, state="readonly", width=190)
            elif w_type == "skrivbar":
                w = ctk.CTkComboBox(input_frame, variable=var, values=values, width=190)
                self.dynamiske_lister[label_tekst] = w
            else:
                w = ctk.CTkEntry(input_frame, textvariable=var, width=190)

            w.grid(row=row_idx, column=1, padx=(0, 20), pady=10, sticky="e")
            row_idx += 1

        # 2. DYNAMISKE FELTER (Vises automatisk baseret på kategori)
        # Dette frame er altid synligt, men fyldes kun med det relevante.
        self.dyn_frame = ctk.CTkFrame(input_frame, fg_color="#333333", corner_radius=5)
        self.dyn_frame.grid(row=row_idx, column=0, columnspan=2, sticky="ew", padx=15, pady=(10, 5))
        self.dyn_frame.grid_columnconfigure(1, weight=1)
        row_idx += 1

        self.kategori_var.trace_add("write", self.opdater_dynamisk_felt)

        # 3. AVANCEREDE FELTER (Skjult metadata)
        self.vis_avanceret_var = ctk.BooleanVar(value=False)
        switch_avanceret = ctk.CTkSwitch(input_frame, text="Vis metadata (Tags, Øjemed mm.)",
                                         variable=self.vis_avanceret_var, command=self.toggle_avanceret)
        switch_avanceret.grid(row=row_idx, column=0, columnspan=2, padx=20, pady=15, sticky="w")
        row_idx += 1

        self.adv_frame = ctk.CTkFrame(input_frame, fg_color="transparent")

        adv_felter = [
            ("Fritekst Specs:", self.specs_var),
            ("Tags (komma):", self.tags_var),
            ("Øjemed/Plan:", self.purpose_var)
        ]

        for i, (l_tekst, var) in enumerate(adv_felter):
            ctk.CTkLabel(self.adv_frame, text=l_tekst).grid(row=i, column=0, padx=20, pady=8, sticky="w")
            ctk.CTkEntry(self.adv_frame, textvariable=var, width=190).grid(row=i, column=1, padx=(0, 20), pady=8,
                                                                           sticky="e")

        self.adv_row_idx = row_idx  # Gemmer rækkenummeret til at vise/skjule

        # 4. KNAPPER (Fastlåst i bunden)
        btn_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
        # Vi bruger pack indeni et grid-setup til at placere knapperne nederst
        btn_container = ctk.CTkFrame(input_frame, fg_color="transparent")
        btn_container.grid(row=100, column=0, columnspan=2, sticky="s", pady=20, padx=15)

        self.btn_gem = ctk.CTkButton(btn_container, text="Tilføj / Opdater", command=self.gem_enhed, fg_color="#2FA572",
                                     hover_color="#1F7A52", width=160)
        self.btn_gem.grid(row=0, column=0, padx=5, pady=5)

        self.btn_ryd = ctk.CTkButton(btn_container, text="Ryd Felter", command=self.ryd_felter, fg_color="transparent",
                                     border_width=1, width=160)
        self.btn_ryd.grid(row=0, column=1, padx=5, pady=5)

        self.btn_opdel = ctk.CTkButton(btn_container, text="Tag 1 ud af pulje", command=self.tag_en_i_brug,
                                       state="disabled", fg_color="#F39C12", hover_color="#C0392B", width=160)
        self.btn_opdel.grid(row=1, column=0, padx=5, pady=5)

        ctk.CTkButton(btn_container, text="Slet Enhed", command=self.slet_enhed, fg_color="transparent",
                      border_color="#E74C3C", border_width=1, text_color="#E74C3C", hover_color="#3a130f",
                      width=160).grid(row=1, column=1, padx=5, pady=5)

        # Sørg for at række 99 fylder tomrummet, så knapperne presses ned
        input_frame.grid_rowconfigure(99, weight=1)

        # ==================== HØJRE PANEL (Data View) ====================
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        right_frame.grid_rowconfigure(1, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        top_bar = ctk.CTkFrame(right_frame, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.search_var = ctk.StringVar()
        s_entry = ctk.CTkEntry(top_bar, textvariable=self.search_var,
                               placeholder_text="Søg i lager (f.eks. 'nas' eller 'ryzen')...", width=350)
        s_entry.pack(side="left")
        self.search_var.trace_add("write", lambda *args: self.opdater_treeview())

        ctk.CTkButton(top_bar, text="🤖 AI Bygge-Forslag", command=self.vis_ai_forslag).pack(side="right")

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Treeview", background="#2b2b2b", foreground="white", fieldbackground="#2b2b2b", borderwidth=0,
                        font=("Helvetica", 11), rowheight=28)
        style.configure("Treeview.Heading", background="#1f1f1f", foreground="white", borderwidth=0,
                        font=("Helvetica", 11, "bold"))
        style.map("Treeview", background=[("selected", "#1f538d")])
        style.map("Treeview.Heading", background=[("active", "#3a3a3a")])

        # Årgang er nu tilføjet som en fast kolonne for overblik!
        kolonner = ("id", "kategori", "brand", "navn", "release_year", "status", "antal")
        self.tree = ttk.Treeview(right_frame, columns=kolonner, show="headings", height=25)

        overskrifter = {"id": "ID", "kategori": "Kategori", "brand": "Brand", "navn": "Model", "release_year": "Årgang",
                        "status": "Status", "antal": "Antal"}
        bredder = {"id": 40, "kategori": 120, "brand": 150, "navn": 220, "release_year": 80, "status": 170, "antal": 60}

        for col in kolonner:
            self.tree.heading(col, text=overskrifter[col])
            self.tree.column(col, width=bredder[col], anchor="w")

        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self.indlaes_til_redigering)

        # Kald den en gang for at opsætte evt. "Andet" kategori
        self.opdater_dynamisk_felt()

    def toggle_avanceret(self):
        if self.vis_avanceret_var.get():
            self.adv_frame.grid(row=self.adv_row_idx, column=0, columnspan=2, sticky="ew")
        else:
            self.adv_frame.grid_forget()

    def opdater_dynamisk_felt(self, *args):
        for widget in self.dyn_frame.winfo_children():
            widget.destroy()
        self.extra_vars.clear()

        kat = self.kategori_var.get()
        felter = self.db.KATEGORI_SPECIFIKKE_FELTER.get(kat, {})

        if not felter:
            self.dyn_frame.grid_remove()  # Skjul boksen helt, hvis der ingen er (f.eks. "Andet")
            return

        self.dyn_frame.grid()  # Vis boksen

        for i, (key, label_text) in enumerate(felter.items()):
            ctk.CTkLabel(self.dyn_frame, text=label_text, font=ctk.CTkFont(size=11)).grid(row=i, column=0, padx=10,
                                                                                          pady=5, sticky="w")

            var = ctk.StringVar()
            if key in {"igpu", "low_profile", "external_power"}:
                w = ctk.CTkComboBox(self.dyn_frame, variable=var, values=["Ja", "Nej", "Ukendt"], state="readonly",
                                    width=160)
            else:
                w = ctk.CTkEntry(self.dyn_frame, textvariable=var, width=160)

            w.grid(row=i, column=1, padx=10, pady=5, sticky="e")
            self.extra_vars[key] = var

    def opdater_historik_lister(self):
        brands = sorted({item.get("brand") for item in self.data if item.get("brand")})
        navne = sorted({item.get("navn") for item in self.data if item.get("navn")})

        if "Brand:" in self.dynamiske_lister:
            self.dynamiske_lister["Brand:"].configure(values=brands)
        if "Model/Navn:" in self.dynamiske_lister:
            self.dynamiske_lister["Model/Navn:"].configure(values=navne)

    def _parse_antal(self):
        antal = self.antal_var.get().strip()
        if not antal: return 1
        try:
            return max(1, int(antal))
        except ValueError:
            messagebox.showerror("Fejl", "Antal skal være et heltal.")
            return None

    def gem_enhed(self):
        if not self.brand_var.get().strip() or not self.navn_var.get().strip():
            messagebox.showwarning("Fejl", "Brand og Model/Navn skal udfyldes.")
            return

        antal = self._parse_antal()
        if antal is None: return

        details = {k: v.get().strip() for k, v in self.extra_vars.items() if v.get().strip()}
        tags = [t.strip() for t in self.tags_var.get().split(",") if t.strip()]

        enhed = {
            "kategori": self.kategori_var.get().strip(),
            "brand": self.brand_var.get().strip(),
            "navn": self.navn_var.get().strip(),
            "release_year": self.release_var.get().strip(),
            "specs": self.specs_var.get().strip(),
            "antal": str(antal),
            "status": self.status_var.get().strip(),
            "purpose": self.purpose_var.get().strip(),
            "tags": tags,
            "details": details,
        }

        if self.rediger_id is not None:
            for item in self.data:
                if item.get("id") == self.rediger_id:
                    enhed["id"] = self.rediger_id
                    item.update(self.db.normalize_item(enhed))
                    break
        else:
            enhed["id"] = self.db.next_id(self.data)
            self.data.append(self.db.normalize_item(enhed))

        self.db.save_data(self.data)
        self.opdater_treeview()
        self.opdater_historik_lister()
        self.ryd_felter()

    def ryd_felter(self):
        self.rediger_id = None
        self.btn_gem.configure(text="Tilføj")
        self.kategori_var.set("Andet")
        self.brand_var.set("")
        self.navn_var.set("")
        self.release_var.set("")
        self.antal_var.set("1")
        self.status_var.set("Ledig på lager")

        self.specs_var.set("")
        self.tags_var.set("")
        self.purpose_var.set("")

        self.opdater_dynamisk_felt()
        self.btn_opdel.configure(state="disabled")

    def indlaes_til_redigering(self, event):
        valgt = self.tree.selection()
        if not valgt: return

        item_id = self.tree.item(valgt[0], "values")[0]
        for item in self.data:
            if str(item.get("id")) == str(item_id):
                self.rediger_id = item["id"]
                self.kategori_var.set(item.get("kategori", ""))
                self.brand_var.set(item.get("brand", ""))
                self.navn_var.set(item.get("navn", ""))
                self.release_var.set(item.get("release_year", ""))
                self.antal_var.set(item.get("antal", "1"))
                self.status_var.set(item.get("status", ""))

                self.specs_var.set(item.get("specs", ""))
                self.tags_var.set(", ".join(item.get("tags", [])))
                self.purpose_var.set(item.get("purpose", ""))

                self.opdater_dynamisk_felt()
                for key, var in self.extra_vars.items():
                    var.set(item.get("details", {}).get(key, ""))

                self.btn_gem.configure(text="Opdater")

                if self.specs_var.get() or self.tags_var.get() or self.purpose_var.get():
                    self.vis_avanceret_var.set(True)
                    self.toggle_avanceret()

                try:
                    if int(self.antal_var.get()) > 1:
                        self.btn_opdel.configure(state="normal")
                    else:
                        self.btn_opdel.configure(state="disabled")
                except ValueError:
                    self.btn_opdel.configure(state="disabled")
                break

    def tag_en_i_brug(self):
        if self.rediger_id is None: return
        antal = self._parse_antal()
        if not antal or antal <= 1: return

        ny_status = simpledialog.askstring("Ny Status",
                                           "Hvad er status for den ENE enhed?\n(F.eks. 'I brug (Router NAS)')",
                                           parent=self)
        if not ny_status: return

        original_enhed = next((i for i in self.data if i.get("id") == self.rediger_id), None)
        if not original_enhed: return

        original_enhed["antal"] = str(antal - 1)

        ny_enhed = dict(original_enhed)
        ny_enhed["id"] = self.db.next_id(self.data)
        ny_enhed["antal"] = "1"
        ny_enhed["status"] = ny_status.strip()
        self.data.append(self.db.normalize_item(ny_enhed))

        self.db.save_data(self.data)
        self.opdater_treeview()
        self.ryd_felter()
        messagebox.showinfo("Succes", f"1 enhed er taget ud af puljen og sat til:\n{ny_status}")

    def slet_enhed(self):
        valgt = self.tree.selection()
        if not valgt: return
        if messagebox.askyesno("Bekræft", "Vil du slette den valgte enhed?"):
            item_id = int(self.tree.item(valgt[0], "values")[0])
            self.data = [item for item in self.data if item.get("id") != item_id]
            self.db.save_data(self.data)
            self.opdater_treeview()
            self.ryd_felter()

    def vis_ai_forslag(self):
        forslag = self.search_var.get().strip() or "jeg skal bygge en lille NAS"
        anbefalinger = self.db.recommend_for_goal(forslag, self.data, limit=5)

        if not anbefalinger:
            messagebox.showinfo("AI forslag", "Ingen relevante dele blev fundet.")
            return

        tekst = "\n".join(
            f"- {i.get('brand', '')} {i.get('navn', '')} ({i.get('kategori', '')}) – antal: {i.get('antal', '1')}" for i
            in anbefalinger)
        messagebox.showinfo("AI forslag", f"Til: {forslag}\n\n{tekst}")

    def opdater_treeview(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        query = self.search_var.get().strip().lower()
        synlige = self.db.search(query, self.data) if query else list(self.data)

        for enhed in synlige:
            self.tree.insert("", tk.END, values=(
                enhed.get("id", ""), enhed.get("kategori", ""), enhed.get("brand", ""),
                enhed.get("navn", ""), enhed.get("release_year", ""), enhed.get("status", ""),
                enhed.get("antal", "")
            ))