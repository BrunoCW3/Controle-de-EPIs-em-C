"""
EPI Manager — Python GUI
Shared SQLite DB (epis.db) with C backend.
CA validation via consultaca.com on registration.

Install once:
    pip install requests beautifulsoup4
"""

import re
import os
import sqlite3
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

try:
    import requests
    from bs4 import BeautifulSoup
    SCRAPE_OK = True
except ImportError:
    SCRAPE_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "epis.db")

VALID_EPIS = [
    "capacete", "luva", "bota", "cinto de seguranca",
    "mangote", "oculos", "protetor auricular",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Keywords that should appear in the page title/description for each EPI type
DESC_KEYWORDS = {
    "capacete":           ["capacete", "helmet"],
    "luva":               ["luva", "glove"],
    "bota":               ["bota", "calcado", "calçado", "botina", "sapato", "boot"],
    "cinto de seguranca": ["cinto", "talabarte", "trava", "cinturao", "cinturão"],
    "mangote":            ["mangote", "sleeve", "manga"],
    "oculos":             ["oculos", "óculos", "goggle", "visor"],
    "protetor auricular": ["protetor auricular", "protetor", "auricular", "earplug"],
}

# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with get_conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS epis (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                descricao    TEXT    NOT NULL,
                ca           INTEGER NOT NULL,
                qntd         INTEGER NOT NULL CHECK(qntd >= 0),
                qntd_alocada INTEGER NOT NULL DEFAULT 0 CHECK(qntd_alocada >= 0)
            );
            CREATE TABLE IF NOT EXISTS emprestimos (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_funcionario TEXT    NOT NULL,
                horario          TEXT    NOT NULL,
                ca_alocado       INTEGER NOT NULL
            );
        """)
        # Migrate: if emprestimos was created with a broken FOREIGN KEY, recreate it
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=\'emprestimos\'"
        ).fetchone()
        if row and "FOREIGN KEY" in (row[0] or "").upper():
            c.executescript("""
                ALTER TABLE emprestimos RENAME TO emprestimos_old;
                CREATE TABLE emprestimos (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome_funcionario TEXT    NOT NULL,
                    horario          TEXT    NOT NULL,
                    ca_alocado       INTEGER NOT NULL
                );
                INSERT INTO emprestimos SELECT id, nome_funcionario, horario, ca_alocado
                    FROM emprestimos_old;
                DROP TABLE emprestimos_old;
            """)

def db_fetch_epis():
    with get_conn() as c:
        return c.execute(
            "SELECT id, descricao, ca, qntd, qntd_alocada FROM epis ORDER BY id"
        ).fetchall()

def db_stats():
    with get_conn() as c:
        r = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(qntd),0), COALESCE(SUM(qntd_alocada),0) FROM epis"
        ).fetchone()
        return r[0], r[1], r[2]

def db_insert(desc, ca, qntd):
    with get_conn() as c:
        c.execute(
            "INSERT INTO epis (descricao, ca, qntd, qntd_alocada) VALUES (?,?,?,0)",
            (desc, ca, qntd),
        )

def db_delete(eid, qty_remove, qty_current):
    with get_conn() as c:
        c.execute("PRAGMA foreign_keys=OFF")   # avoid FK mismatch on delete
        if qty_remove < qty_current:
            c.execute("UPDATE epis SET qntd = qntd - ? WHERE id = ?", (qty_remove, eid))
            c.execute(
                "UPDATE epis SET qntd_alocada = MIN(qntd_alocada, qntd) WHERE id = ?",
                (eid,),
            )
        else:
            c.execute("DELETE FROM epis WHERE id = ?", (eid,))
        c.execute("PRAGMA foreign_keys=ON")

def db_assign(eid, nome):
    with get_conn() as c:
        row = c.execute(
            "SELECT ca, qntd, qntd_alocada FROM epis WHERE id = ?", (eid,)
        ).fetchone()
        if not row:
            return False, "EPI nao encontrado."
        if row["qntd_alocada"] >= row["qntd"]:
            return False, "Estoque esgotado para este EPI."
        c.execute("UPDATE epis SET qntd_alocada = qntd_alocada + 1 WHERE id = ?", (eid,))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO emprestimos (nome_funcionario, horario, ca_alocado) VALUES (?,?,?)",
            (nome, now, row["ca"]),
        )
        return True, f"EPI designado para {nome}!"

# ─────────────────────────────────────────────────────────────────────────────
# CA scraper
# ─────────────────────────────────────────────────────────────────────────────

def fetch_ca_info(ca_number):
    """
    Returns dict:
      validade  : "DD/MM/YYYY" or None
      situacao  : "VALIDO" | "VENCIDO" | None
      descricao : page h1 text or None
      error     : error message or None
    """
    url = f"https://consultaca.com/{ca_number}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        return {"error": "Sem conexao com a internet."}
    except requests.exceptions.Timeout:
        return {"error": "Tempo esgotado ao conectar."}
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        if code == 404:
            return {"error": f"CA {ca_number} nao encontrado no site."}
        return {"error": f"Erro HTTP {code} ao buscar CA."}
    except Exception as e:
        return {"error": f"Erro inesperado: {e}"}

    soup = BeautifulSoup(resp.text, "html.parser")
    full_text = soup.get_text(separator="\n")

    result = {"validade": None, "situacao": None, "descricao": None, "error": None}

    # Description from h1
    h1 = soup.find("h1")
    if h1:
        result["descricao"] = h1.get_text(strip=True)

    # Validade — site writes "Validade: 03/01/2030vencera daqui..." (no space)
    for line in full_text.splitlines():
        if "validade" in line.lower():
            m = re.search(r"\d{2}/\d{2}/\d{4}", line)
            if m:
                result["validade"] = m.group()
                break
    # Fallback: any DD/MM/YYYY on the page
    if not result["validade"]:
        m = re.search(r"\d{2}/\d{2}/\d{4}", full_text)
        if m:
            result["validade"] = m.group()

    # Situacao
    for line in full_text.splitlines():
        low = line.lower()
        if "situa" in low and ":" in low:
            if "vencido" in low:
                result["situacao"] = "VENCIDO"
            elif "v" in low and "lido" in low:   # valido / válido
                result["situacao"] = "VALIDO"
            if result["situacao"]:
                break

    if not result["validade"] and not result["situacao"]:
        result["error"] = (
            "Nao foi possivel ler os dados do CA. "
            "O site pode estar indisponivel ou o CA nao existe."
        )

    return result


def check_validity(validade_str):
    """Returns (is_valid: bool, days: int) or (None, None)."""
    try:
        exp   = datetime.strptime(validade_str, "%d/%m/%Y")
        days  = (exp - datetime.today()).days
        return days >= 0, days
    except ValueError:
        return None, None

# ─────────────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────────────

class EPIApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gerenciador de EPIs")
        self.geometry("860x620")
        self.minsize(720, 520)
        self.configure(bg="#F5F4F0")

        init_db()
        self._setup_styles()
        self._build_ui()
        self.refresh()
        self._auto_refresh()

    # ── Styles ────────────────────────────────────────────────────────────────

    def _setup_styles(self):
        self.C = {
            "bg":     "#F5F4F0",
            "surf":   "#FFFFFF",
            "border": "#D3D1C7",
            "text":   "#2C2C2A",
            "muted":  "#5F5E5A",
            "grn_fg": "#27500A",
            "grn_bg": "#EAF3DE",
            "red_fg": "#A32D2D",
            "red_bg": "#FCEBEB",
            "amb_fg": "#633806",
            "amb_bg": "#FAEEDA",
        }
        C = self.C
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",           background=C["bg"])
        s.configure("TLabel",           background=C["bg"],   foreground=C["text"], font=("Segoe UI", 10))
        s.configure("Title.TLabel",     background=C["bg"],   foreground=C["text"], font=("Segoe UI", 16, "bold"))
        s.configure("Sub.TLabel",       background=C["bg"],   foreground=C["muted"],font=("Segoe UI", 9))
        s.configure("Stat.TLabel",      background=C["surf"], foreground=C["text"], font=("Segoe UI", 22, "bold"))
        s.configure("StatLbl.TLabel",   background=C["surf"], foreground=C["muted"],font=("Segoe UI", 9))
        s.configure("TButton",          background=C["surf"], foreground=C["text"],
                                        font=("Segoe UI", 10), borderwidth=1, relief="solid", padding="10 6")
        s.map("TButton",                background=[("active", "#F1EFE8")])
        s.configure("Primary.TButton",  background=C["text"], foreground="#FFFFFF",
                                        borderwidth=0, padding="12 7", font=("Segoe UI", 10))
        s.map("Primary.TButton",        background=[("active", "#444441")])
        s.configure("Danger.TButton",   background=C["red_bg"], foreground=C["red_fg"],
                                        borderwidth=1, relief="solid", padding="10 6", font=("Segoe UI", 10))
        s.map("Danger.TButton",         background=[("active", "#F7C1C1")])
        s.configure("Treeview",         font=("Segoe UI", 10), rowheight=30,
                                        background=C["surf"], fieldbackground=C["surf"],
                                        foreground=C["text"], borderwidth=0)
        s.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#F1EFE8",
                                        foreground=C["muted"], relief="flat")
        s.map("Treeview",               background=[("selected", "#E6F1FB")],
                                        foreground=[("selected", C["text"])])

    # ── UI layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        C = self.C
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        # Header
        hdr = ttk.Frame(root)
        hdr.pack(fill="x", pady=(0, 14))
        ttk.Label(hdr, text="Gerenciador de EPIs", style="Title.TLabel").pack(side="left")
        ttk.Label(hdr, text="  Controle de estoque e designacao de EPIs",
                  style="Sub.TLabel").pack(side="left", pady=6)

        # Stats row
        sf = ttk.Frame(root)
        sf.pack(fill="x", pady=(0, 14))
        sf.columnconfigure((0, 1, 2), weight=1, uniform="s")
        self.sv = {
            "tipos":   self._stat_card(sf, "Total de tipos",   0),
            "total":   self._stat_card(sf, "Total em estoque", 1),
            "alocado": self._stat_card(sf, "Total alocados",   2),
        }

        # Two-column pane
        pane = ttk.Frame(root)
        pane.pack(fill="both", expand=True)
        pane.columnconfigure(0, weight=0, minsize=255)
        pane.columnconfigure(1, weight=1)
        pane.rowconfigure(0, weight=1)

        self._build_form(pane)
        self._build_table(pane)

    def _stat_card(self, parent, label, col):
        C = self.C
        card = tk.Frame(parent, bg=C["surf"],
                        highlightbackground=C["border"], highlightthickness=1)
        card.grid(row=0, column=col, padx=(0 if col == 0 else 8, 0),
                  sticky="ew", ipady=10)
        ttk.Label(card, text=label, style="StatLbl.TLabel").pack(anchor="w", padx=14)
        val = ttk.Label(card, text="0", style="Stat.TLabel")
        val.pack(anchor="w", padx=14)
        return val

    def _build_form(self, parent):
        C = self.C
        outer = tk.Frame(parent, bg=C["surf"],
                         highlightbackground=C["border"], highlightthickness=1)
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        f = tk.Frame(outer, bg=C["surf"], padx=14, pady=14)
        f.pack(fill="both", expand=True)

        def lbl(t):
            tk.Label(f, text=t, font=("Segoe UI", 9), fg=C["muted"],
                     bg=C["surf"], anchor="w").pack(fill="x", pady=(8, 2))

        # ── Cadastrar section ──
        tk.Label(f, text="Cadastrar novo EPI", font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["surf"], anchor="w").pack(fill="x")

        lbl("Descricao")
        self.combo_desc = ttk.Combobox(f, values=VALID_EPIS,
                                       state="readonly", font=("Segoe UI", 10))
        self.combo_desc.pack(fill="x")

        lbl("CA (numero)")
        self.entry_ca = tk.Entry(f, font=("Segoe UI", 10), bg="#F5F4F0",
                                 fg=C["text"], relief="solid", bd=1)
        self.entry_ca.pack(fill="x", ipady=5)

        lbl("Quantidade")
        self.entry_qntd = tk.Entry(f, font=("Segoe UI", 10), bg="#F5F4F0",
                                   fg=C["text"], relief="solid", bd=1)
        self.entry_qntd.pack(fill="x", ipady=5)

        self.btn_cadastrar = ttk.Button(f, text="+ Cadastrar",
                                        style="Primary.TButton",
                                        command=self.cadastrar)
        self.btn_cadastrar.pack(fill="x", pady=(12, 0))

        self.lbl_cad_msg = tk.Label(f, text="", font=("Segoe UI", 9),
                                    fg=C["grn_fg"], bg=C["surf"], wraplength=220)
        self.lbl_cad_msg.pack(fill="x", pady=(4, 0))

        # ── Divider ──
        tk.Frame(f, bg=C["border"], height=1).pack(fill="x", pady=12)

        # ── Designar section ──
        tk.Label(f, text="Designar EPI", font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["surf"], anchor="w").pack(fill="x")

        lbl("Funcionario")
        self.entry_func = tk.Entry(f, font=("Segoe UI", 10), bg="#F5F4F0",
                                   fg=C["text"], relief="solid", bd=1)
        self.entry_func.pack(fill="x", ipady=5)

        lbl("EPI a designar")
        self.combo_des = ttk.Combobox(f, state="readonly", font=("Segoe UI", 10))
        self.combo_des.pack(fill="x")

        ttk.Button(f, text="Designar", style="Primary.TButton",
                   command=self.designar).pack(fill="x", pady=(10, 0))

        self.lbl_des_msg = tk.Label(f, text="", font=("Segoe UI", 9),
                                    fg=C["grn_fg"], bg=C["surf"], wraplength=220)
        self.lbl_des_msg.pack(fill="x", pady=(4, 0))

    def _build_table(self, parent):
        C = self.C
        outer = tk.Frame(parent, bg=C["surf"],
                         highlightbackground=C["border"], highlightthickness=1)
        outer.grid(row=0, column=1, sticky="nsew")

        f = tk.Frame(outer, bg=C["surf"], padx=14, pady=14)
        f.pack(fill="both", expand=True)

        tk.Label(f, text="Estoque", font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["surf"], anchor="w").pack(fill="x", pady=(0, 8))

        cols    = ("#", "Descricao", "CA", "Qtd", "Alocados", "Disponivel")
        widths  = [30, 150, 80, 55, 80, 100]
        anchors = ["center", "w", "center", "center", "center", "center"]

        self.tree = ttk.Treeview(f, columns=cols, show="headings",
                                 selectmode="browse")
        for col, w, a in zip(cols, widths, anchors):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor=a, minwidth=28)

        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Delete button — plain ASCII label to avoid Windows emoji encoding bugs
        bf = tk.Frame(outer, bg=C["surf"], padx=14)
        bf.pack(fill="x", pady=8)
        ttk.Button(bf, text="Excluir selecionado",
                   style="Danger.TButton",
                   command=self.excluir).pack(side="left")

        self.lbl_sync = tk.Label(outer, text="", font=("Segoe UI", 8),
                                 fg=C["muted"], bg=C["surf"], anchor="e")
        self.lbl_sync.pack(fill="x", padx=14, pady=(0, 6))

    # ── Actions ───────────────────────────────────────────────────────────────

    def cadastrar(self):
        desc  = self.combo_desc.get().strip()
        ca_s  = self.entry_ca.get().strip()
        qty_s = self.entry_qntd.get().strip()

        if not desc:
            return self._msg(self.lbl_cad_msg, "Selecione uma descricao.", err=True)
        if not ca_s.isdigit() or int(ca_s) < 1:
            return self._msg(self.lbl_cad_msg, "CA invalido.", err=True)
        if not qty_s.isdigit() or int(qty_s) < 1:
            return self._msg(self.lbl_cad_msg, "Quantidade invalida.", err=True)

        if not SCRAPE_OK:
            self._finish_cadastrar(desc, int(ca_s), int(qty_s))
            return

        self.btn_cadastrar.config(state="disabled", text="Verificando CA...")
        self._msg(self.lbl_cad_msg, "Consultando consultaca.com...")
        threading.Thread(
            target=self._check_before_save,
            args=(desc, int(ca_s), int(qty_s)),
            daemon=True,
        ).start()

    def _check_before_save(self, desc, ca, qntd):
        info = fetch_ca_info(ca)
        self.after(0, lambda: self._on_ca_checked(desc, ca, qntd, info))

    def _on_ca_checked(self, desc, ca, qntd, info):
        self.btn_cadastrar.config(state="normal", text="+ Cadastrar")
        self._msg(self.lbl_cad_msg, "")

        # Network error — ask user whether to proceed anyway
        if info.get("error") and not info.get("validade") and not info.get("situacao"):
            proceed = messagebox.askyesno(
                "Verificacao indisponivel",
                f"Nao foi possivel verificar o CA {ca} online:\n"
                f"{info['error']}\n\n"
                "Deseja cadastrar mesmo assim?"
            )
            if proceed:
                self._finish_cadastrar(desc, ca, qntd)
            return

        errors = []

        # 1. Validity check
        situacao = info.get("situacao")
        validade = info.get("validade")
        is_valid, days = check_validity(validade) if validade else (None, None)
        if situacao == "VENCIDO":
            is_valid = False
        elif situacao == "VALIDO" and is_valid is None:
            is_valid = True

        if is_valid is False:
            date_str = f" (venceu em {validade})" if validade else ""
            errors.append(
                f"CA {ca} esta VENCIDO{date_str}.\n"
                "EPIs com CA vencido nao podem ser cadastrados."
            )

        # 2. Description match check
        page_title = (info.get("descricao") or "").lower()
        # Normalise accents for comparison
        for src_c, dst_c in [("á","a"),("ã","a"),("â","a"),("é","e"),("ê","e"),
                              ("í","i"),("ó","o"),("õ","o"),("ô","o"),("ú","u"),("ç","c")]:
            page_title = page_title.replace(src_c, dst_c)

        keywords = DESC_KEYWORDS.get(desc, [])
        if page_title and keywords and not any(kw in page_title for kw in keywords):
            errors.append(
                f"Descricao incompativel: voce selecionou '{desc.capitalize()}',\n"
                f"mas o CA {ca} corresponde a:\n"
                f"\"{(info.get('descricao') or '')[:90]}\""
            )

        if errors:
            messagebox.showerror(
                "Cadastro bloqueado",
                "O EPI nao pode ser cadastrado:\n\n" + "\n\n".join(errors)
            )
            return

        self._finish_cadastrar(desc, ca, qntd)

    def _finish_cadastrar(self, desc, ca, qntd):
        db_insert(desc, ca, qntd)
        self.combo_desc.set("")
        self.entry_ca.delete(0, "end")
        self.entry_qntd.delete(0, "end")
        txt = "EPIs cadastrados com sucesso!" if qntd > 1 else "EPI cadastrado com sucesso!"
        self._msg(self.lbl_cad_msg, txt)
        self.refresh()

    def excluir(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Atencao", "Selecione um EPI na tabela primeiro.")
            return

        values = self.tree.item(sel[0], "values")
        # values: ('#', 'Descricao', 'CA XXXX', 'Qtd', 'Alocados', 'Disponivel')
        try:
            eid  = int(values[0])
            qntd = int(values[3])
        except (ValueError, IndexError):
            messagebox.showerror("Erro", "Nao foi possivel ler os dados da linha selecionada.")
            return

        desc_txt = values[1]
        ca_txt   = values[2]  # e.g. "CA 12345"

        if qntd > 1:
            qty = simpledialog.askinteger(
                "Excluir EPI",
                f"{desc_txt} ({ca_txt}) tem {qntd} unidades.\n"
                f"Quantas deseja excluir? (1 a {qntd})",
                minvalue=1, maxvalue=qntd, parent=self,
            )
            if qty is None:
                return
        else:
            ok = messagebox.askyesno(
                "Excluir EPI",
                f"Confirmar exclusao de '{desc_txt}' ({ca_txt})?"
            )
            if not ok:
                return
            qty = 1

        db_delete(eid, qty, qntd)
        self.refresh()

    def designar(self):
        nome = self.entry_func.get().strip()
        sel  = self.combo_des.get()
        if not nome:
            return self._msg(self.lbl_des_msg, "Informe o nome do funcionario.", err=True)
        if not sel:
            return self._msg(self.lbl_des_msg, "Selecione um EPI.", err=True)
        # combo format: "#ID  |  Descricao  (CA XXXX)  — N disp."
        try:
            eid = int(sel.split("|")[0].strip().lstrip("#"))
        except ValueError:
            return self._msg(self.lbl_des_msg, "Selecao invalida.", err=True)
        ok, txt = db_assign(eid, nome)
        self._msg(self.lbl_des_msg, txt, err=not ok)
        if ok:
            self.entry_func.delete(0, "end")
            self.combo_des.set("")
            self.refresh()

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        C = self.C
        tipos, total, alocados = db_stats()
        self.sv["tipos"]["text"]   = str(tipos)
        self.sv["total"]["text"]   = str(total)
        self.sv["alocado"]["text"] = str(alocados)

        # Remember which row ID was selected so we can restore it after rebuild
        selected_eid = None
        sel = self.tree.selection()
        if sel:
            try:
                selected_eid = int(self.tree.item(sel[0], "values")[0])
            except (ValueError, IndexError):
                pass

        for row in self.tree.get_children():
            self.tree.delete(row)

        epis = db_fetch_epis()
        for e in epis:
            disp = e["qntd"] - e["qntd_alocada"]
            if disp > 3:
                status = f"OK  {disp} disp."
            elif disp > 0:
                status = f"!   {disp} disp."
            else:
                status = "ESGOTADO"
            iid = self.tree.insert("", "end", values=(
                e["id"],
                e["descricao"].capitalize(),
                f"CA {e['ca']}",
                e["qntd"],
                e["qntd_alocada"],
                status,
            ))
            # Restore selection if this row was selected before the refresh
            if selected_eid is not None and e["id"] == selected_eid:
                self.tree.selection_set(iid)
                self.tree.see(iid)

        opts = [
            f"#{e['id']}  |  {e['descricao'].capitalize()}  (CA {e['ca']})  -- {e['qntd']-e['qntd_alocada']} disp."
            for e in epis if e["qntd"] - e["qntd_alocada"] > 0
        ]
        self.combo_des["values"] = opts

        now = datetime.now().strftime("%H:%M:%S")
        self.lbl_sync["text"] = f"Sincronizado com epis.db as {now}"

    def _auto_refresh(self):
        self.refresh()
        self.after(2000, self._auto_refresh)

    # ── Util ──────────────────────────────────────────────────────────────────

    def _msg(self, label, text, err=False):
        C = self.C
        label.config(text=text, fg=C["red_fg"] if err else C["grn_fg"])
        if text:
            self.after(4000, lambda: label.config(text=""))


if __name__ == "__main__":
    app = EPIApp()
    app.mainloop()
