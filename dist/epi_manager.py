"""
EPI Manager — Python GUI
Banco de dados SQLite compartilhado (epis.db) com backend C.

Instale uma vez:
    pip install requests beautifulsoup4
"""

import re
import os
import sys
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Caminhos
# ─────────────────────────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "epis.db")

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

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

DESC_KEYWORDS = {
    "capacete":           ["capacete", "helmet"],
    "luva":               ["luva", "glove"],
    "bota":               ["bota", "calcado", "botina", "sapato", "boot"],
    "cinto de seguranca": ["cinto", "talabarte", "trava", "cinturao"],
    "mangote":            ["mangote", "sleeve", "manga"],
    "oculos":             ["oculos", "goggle", "visor"],
    "protetor auricular": ["protetor auricular", "protetor", "auricular", "earplug"],
}

WARN_DAYS = 30

# ─────────────────────────────────────────────────────────────────────────────
# Banco de dados
# ─────────────────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn

def init_db():
    with get_conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS epis (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                descricao    TEXT    NOT NULL,
                ca           INTEGER NOT NULL,
                qntd         INTEGER NOT NULL CHECK(qntd >= 0),
                qntd_alocada INTEGER NOT NULL DEFAULT 0 CHECK(qntd_alocada >= 0),
                ca_validade  TEXT    DEFAULT NULL,
                ca_status    TEXT    DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS emprestimos (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_funcionario TEXT NOT NULL,
                horario          TEXT NOT NULL,
                ca_alocado       INTEGER NOT NULL
            );
        """)
        existing = {r[1] for r in c.execute("PRAGMA table_info(epis)")}
        if "ca_validade" not in existing:
            c.execute("ALTER TABLE epis ADD COLUMN ca_validade TEXT DEFAULT NULL")
        if "ca_status" not in existing:
            c.execute("ALTER TABLE epis ADD COLUMN ca_status  TEXT DEFAULT NULL")
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='emprestimos'"
        ).fetchone()
        if row and "FOREIGN KEY" in (row[0] or "").upper():
            c.executescript("""
                ALTER TABLE emprestimos RENAME TO emprestimos_old;
                CREATE TABLE emprestimos (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome_funcionario TEXT NOT NULL,
                    horario          TEXT NOT NULL,
                    ca_alocado       INTEGER NOT NULL
                );
                INSERT INTO emprestimos SELECT id, nome_funcionario, horario, ca_alocado
                    FROM emprestimos_old;
                DROP TABLE emprestimos_old;
            """)

def db_fetch_epis():
    with get_conn() as c:
        return c.execute(
            "SELECT id, descricao, ca, qntd, qntd_alocada, ca_validade, ca_status "
            "FROM epis ORDER BY id"
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
        if qty_remove < qty_current:
            c.execute("UPDATE epis SET qntd = qntd - ? WHERE id = ?", (qty_remove, eid))
            c.execute(
                "UPDATE epis SET qntd_alocada = MIN(qntd_alocada, qntd) WHERE id = ?",
                (eid,),
            )
        else:
            c.execute("DELETE FROM epis WHERE id = ?", (eid,))

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

def db_update_ca_status(eid, validade, status):
    with get_conn() as c:
        c.execute(
            "UPDATE epis SET ca_validade=?, ca_status=? WHERE id=?",
            (validade, status, eid),
        )

def db_fetch_emprestimos():
    with get_conn() as c:
        return c.execute(
            """
            SELECT emp.id, emp.nome_funcionario, emp.horario,
                   emp.ca_alocado, epi.descricao
            FROM emprestimos emp
            LEFT JOIN epis epi ON epi.ca = emp.ca_alocado
            ORDER BY emp.id DESC
            """
        ).fetchall()

def db_delete_emprestimo(emp_id, ca):
    with get_conn() as c:
        c.execute("DELETE FROM emprestimos WHERE id = ?", (emp_id,))
        c.execute(
            "UPDATE epis SET qntd_alocada = MAX(0, qntd_alocada - 1) WHERE ca = ?",
            (ca,),
        )

# ─────────────────────────────────────────────────────────────────────────────
# Scraper de CA — requests + BeautifulSoup
# Deteccao de CA inexistente: o site redireciona para a pagina inicial quando
# o CA nao existe. Verificamos se a URL final ainda contem o numero do CA.
# ─────────────────────────────────────────────────────────────────────────────

def fetch_ca_info(ca_number):
    """
    Retorna dict: found, validade, situacao, descricao, error
    """
    url    = f"https://consultaca.com/{ca_number}"
    ca_str = str(ca_number)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        return {"found": None, "error": "Sem conexao com a internet."}
    except requests.exceptions.Timeout:
        return {"found": None, "error": "Tempo esgotado ao conectar."}
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        if code == 404:
            return {"found": False, "error": f"CA {ca_number} nao existe (404)."}
        return {"found": None, "error": f"Erro HTTP {code}."}
    except Exception as e:
        return {"found": None, "error": f"Erro inesperado: {e}"}

    soup      = BeautifulSoup(resp.text, "html.parser")
    full_text = soup.get_text(separator="\n")
    full_low  = full_text.lower()

    # ── Verificar se CA existe ────────────────────────────────────────────────
    # Quando CA valido: a pagina tem o bloco:
    #   "N° CA:"
    #   "38664"       <- linha seguinte contem EXATAMENTE o numero
    # Quando CA invalido: o numero pode aparecer em outros contextos
    # (outros produtos, comentarios) mas NUNCA logo apos "N° CA:".
    lines = full_text.splitlines()
    ca_found_in_context = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Linha de label do campo CA
        if re.search(r"n[°o]?\s*ca\s*:?\s*$", stripped.lower()):
            # Verificar se a proxima linha nao vazia contem o numero do CA
            for j in range(i + 1, min(i + 4, len(lines))):
                next_line = lines[j].strip()
                if next_line == ca_str:
                    ca_found_in_context = True
                    break
                elif next_line:  # linha nao vazia diferente do CA
                    break
        if ca_found_in_context:
            break

    if not ca_found_in_context:
        return {
            "found":     False,
            "validade":  None,
            "situacao":  None,
            "descricao": None,
            "error":     f"CA {ca_number} nao encontrado no sistema do MTE.",
        }

    result = {
        "found":     True,
        "validade":  None,
        "situacao":  None,
        "descricao": None,
        "error":     None,
    }

    # Descricao
    h1 = soup.find("h1")
    if h1:
        result["descricao"] = h1.get_text(strip=True)

    # Validade e Situacao — label numa linha, valor na proxima
    # Ex: "Validade:" -> "29/04/2026venceu ha 41 dias"
    #     "Situacao:" -> "VENCIDO"
    for i, line in enumerate(lines):
        low = line.strip().lower()

        # Validade
        if not result["validade"] and "validade" in low:
            m = re.search(r"\d{2}/\d{2}/\d{4}", line)
            if m:
                result["validade"] = m.group()
            else:
                for j in range(i + 1, min(i + 4, len(lines))):
                    m = re.search(r"\d{2}/\d{2}/\d{4}", lines[j])
                    if m:
                        result["validade"] = m.group()
                        break
                    elif lines[j].strip():
                        break

        # Situacao
        if not result["situacao"] and "situa" in low:
            if "vencido" in low:
                result["situacao"] = "VENCIDO"
            elif "v" in low and "lido" in low:
                result["situacao"] = "VALIDO"
            else:
                for j in range(i + 1, min(i + 4, len(lines))):
                    nxt = lines[j].strip().lower()
                    if "vencido" in nxt:
                        result["situacao"] = "VENCIDO"
                        break
                    elif "v" in nxt and "lido" in nxt:
                        result["situacao"] = "VALIDO"
                        break
                    elif nxt:
                        break

        if result["validade"] and result["situacao"]:
            break

    if not result["validade"] and not result["situacao"]:
        result["error"] = "CA encontrado mas sem dados de validade legiveis."

    return result


def check_validity(validade_str):
    try:
        exp  = datetime.strptime(validade_str, "%d/%m/%Y")
        days = (exp - datetime.today()).days
        return days >= 0, days
    except ValueError:
        return None, None


def ca_row_tag(ca_validade, ca_status):
    if ca_status == "VENCIDO":
        return "expired"
    if ca_status == "INEXISTENTE":
        return "invalid"
    if ca_validade:
        ok, days = check_validity(ca_validade)
        if ok is False:
            return "expired"
        if ok is True and days <= WARN_DAYS:
            return "expiring"
    return ""

# ─────────────────────────────────────────────────────────────────────────────
# Aplicacao
# ─────────────────────────────────────────────────────────────────────────────

class EPIApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gerenciador de EPIs")
        self.geometry("950x660")
        self.minsize(780, 560)
        self.configure(bg="#F5F4F0")
        self._ca_check_done = False
        init_db()
        self._setup_styles()
        self._build_ui()
        self.refresh()
        self._auto_refresh()
        if SCRAPE_OK:
            threading.Thread(target=self._check_all_ca_expiry, daemon=True).start()

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
            "pur_fg": "#4B0082",
            "pur_bg": "#F0E6FF",
        }
        C = self.C
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",           background=C["bg"])
        s.configure("TLabel",           background=C["bg"],   foreground=C["text"], font=("Segoe UI", 10))
        s.configure("Title.TLabel",     background=C["bg"],   foreground=C["text"], font=("Segoe UI", 16, "bold"))
        s.configure("Sub.TLabel",       background=C["bg"],   foreground=C["muted"], font=("Segoe UI", 9))
        s.configure("Stat.TLabel",      background=C["surf"], foreground=C["text"], font=("Segoe UI", 22, "bold"))
        s.configure("StatLbl.TLabel",   background=C["surf"], foreground=C["muted"], font=("Segoe UI", 9))
        s.configure("TButton",          background=C["surf"], foreground=C["text"],
                                        font=("Segoe UI", 10), borderwidth=1, relief="solid", padding="10 6")
        s.map("TButton",                background=[("active", "#F1EFE8")])
        s.configure("Primary.TButton",  background=C["text"], foreground="#FFFFFF",
                                        borderwidth=0, padding="12 7", font=("Segoe UI", 10))
        s.map("Primary.TButton",        background=[("active", "#444441")])
        s.configure("Danger.TButton",   background=C["red_bg"], foreground=C["red_fg"],
                                        borderwidth=1, relief="solid", padding="10 6", font=("Segoe UI", 10))
        s.map("Danger.TButton",         background=[("active", "#F7C1C1")])
        s.configure("TNotebook",        background=C["bg"], borderwidth=0)
        s.configure("TNotebook.Tab",    background=C["bg"], foreground=C["muted"],
                                        font=("Segoe UI", 10), padding="12 6")
        s.map("TNotebook.Tab",          background=[("selected", C["surf"])],
                                        foreground=[("selected", C["text"])])
        s.configure("Treeview",         font=("Segoe UI", 10), rowheight=30,
                                        background=C["surf"], fieldbackground=C["surf"],
                                        foreground=C["text"], borderwidth=0)
        s.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#F1EFE8",
                                        foreground=C["muted"], relief="flat")
        s.map("Treeview",               background=[("selected", "#E6F1FB")],
                                        foreground=[("selected", C["text"])])

    def _build_ui(self):
        C = self.C
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        hdr = ttk.Frame(root)
        hdr.pack(fill="x", pady=(0, 14))
        ttk.Label(hdr, text="Gerenciador de EPIs", style="Title.TLabel").pack(side="left")
        ttk.Label(hdr, text="  Controle de estoque e designacao de EPIs",
                  style="Sub.TLabel").pack(side="left", pady=6)

        sf = ttk.Frame(root)
        sf.pack(fill="x", pady=(0, 14))
        sf.columnconfigure((0, 1, 2), weight=1, uniform="s")
        self.sv = {
            "tipos":   self._stat_card(sf, "Total de tipos",   0),
            "total":   self._stat_card(sf, "Total em estoque", 1),
            "alocado": self._stat_card(sf, "Total alocados",   2),
        }

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)

        tab_estoque = ttk.Frame(nb, padding=4)
        nb.add(tab_estoque, text="  Estoque  ")
        tab_estoque.columnconfigure(0, weight=0, minsize=255)
        tab_estoque.columnconfigure(1, weight=1)
        tab_estoque.rowconfigure(0, weight=1)
        self._build_form(tab_estoque)
        self._build_table(tab_estoque)

        tab_alloc = ttk.Frame(nb, padding=4)
        nb.add(tab_alloc, text="  Alocacoes  ")
        tab_alloc.rowconfigure(0, weight=1)
        tab_alloc.columnconfigure(0, weight=1)
        self._build_alloc_table(tab_alloc)

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

        tk.Label(f, text="Cadastrar novo EPI", font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["surf"], anchor="w").pack(fill="x")

        lbl("Descricao")
        self.combo_desc = ttk.Combobox(f, values=VALID_EPIS, state="readonly", font=("Segoe UI", 10))
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
                                        style="Primary.TButton", command=self.cadastrar)
        self.btn_cadastrar.pack(fill="x", pady=(12, 0))

        self.lbl_cad_msg = tk.Label(f, text="", font=("Segoe UI", 9),
                                    fg=C["grn_fg"], bg=C["surf"], wraplength=220)
        self.lbl_cad_msg.pack(fill="x", pady=(4, 0))

        tk.Frame(f, bg=C["border"], height=1).pack(fill="x", pady=12)

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

        leg = tk.Frame(f, bg=C["surf"])
        leg.pack(fill="x", pady=(0, 8))
        tk.Label(leg, text="Estoque", font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["surf"]).pack(side="left")
        tk.Label(leg, text="  CA INEXISTENTE", font=("Segoe UI", 8, "bold"),
                 fg=C["pur_fg"], bg=C["pur_bg"], padx=6, pady=2).pack(side="right", padx=(4, 0))
        tk.Label(leg, text="  CA VENCIDO", font=("Segoe UI", 8, "bold"),
                 fg=C["red_fg"], bg=C["red_bg"], padx=6, pady=2).pack(side="right", padx=(4, 0))
        tk.Label(leg, text="  Vence em breve", font=("Segoe UI", 8, "bold"),
                 fg=C["amb_fg"], bg=C["amb_bg"], padx=6, pady=2).pack(side="right", padx=(4, 0))

        cols    = ("#", "Descricao", "CA", "Qtd", "Alocados", "Disponivel", "CA Validade")
        widths  = [30, 130, 75, 50, 75, 95, 110]
        anchors = ["center", "w", "center", "center", "center", "center", "center"]

        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for col, w, a in zip(cols, widths, anchors):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor=a, minwidth=28)

        self.tree.tag_configure("expired",  background=C["red_bg"], foreground=C["red_fg"])
        self.tree.tag_configure("expiring", background=C["amb_bg"], foreground=C["amb_fg"])
        self.tree.tag_configure("invalid",  background=C["pur_bg"], foreground=C["pur_fg"])

        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bf = tk.Frame(outer, bg=C["surf"], padx=14)
        bf.pack(fill="x", pady=8)
        ttk.Button(bf, text="Excluir selecionado",
                   style="Danger.TButton", command=self.excluir).pack(side="left")

        self.lbl_sync = tk.Label(outer, text="", font=("Segoe UI", 8),
                                 fg=C["muted"], bg=C["surf"], anchor="e")
        self.lbl_sync.pack(fill="x", padx=14, pady=(0, 6))

    def _build_alloc_table(self, parent):
        C = self.C
        outer = tk.Frame(parent, bg=C["surf"],
                         highlightbackground=C["border"], highlightthickness=1)
        outer.grid(row=0, column=0, sticky="nsew")
        f = tk.Frame(outer, bg=C["surf"], padx=14, pady=14)
        f.pack(fill="both", expand=True)

        top = tk.Frame(f, bg=C["surf"])
        top.pack(fill="x", pady=(0, 10))
        tk.Label(top, text="Lista de Alocacoes", font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["surf"]).pack(side="left")
        tk.Label(top, text="Buscar:", font=("Segoe UI", 9),
                 fg=C["muted"], bg=C["surf"]).pack(side="right", padx=(8, 2))
        self.entry_search = tk.Entry(top, font=("Segoe UI", 10), bg="#F5F4F0",
                                     fg=C["text"], relief="solid", bd=1, width=22)
        self.entry_search.pack(side="right", ipady=4)
        self.entry_search.bind("<KeyRelease>", lambda e: self.refresh_alloc())

        cols    = ("#", "Funcionario", "CA", "Descricao", "Data/Hora")
        widths  = [35, 180, 80, 180, 150]
        anchors = ["center", "w", "center", "w", "center"]

        self.alloc_tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for col, w, a in zip(cols, widths, anchors):
            self.alloc_tree.heading(col, text=col)
            self.alloc_tree.column(col, width=w, anchor=a, minwidth=28)

        sb = ttk.Scrollbar(f, orient="vertical", command=self.alloc_tree.yview)
        self.alloc_tree.configure(yscrollcommand=sb.set)
        self.alloc_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bf = tk.Frame(outer, bg=C["surf"], padx=14)
        bf.pack(fill="x", pady=8)
        ttk.Button(bf, text="Devolver selecionado",
                   style="Danger.TButton", command=self.devolver).pack(side="left")
        self.lbl_alloc_count = tk.Label(bf, text="", font=("Segoe UI", 9),
                                        fg=C["muted"], bg=C["surf"])
        self.lbl_alloc_count.pack(side="right", padx=8)

    # ── Verificacao de vencimento em background ───────────────────────────────

    def _check_all_ca_expiry(self):
        epis = db_fetch_epis()
        if not epis:
            self.after(0, lambda: self._on_expiry_check_done([]))
            return
        alerts = []

        def check_one(e):
            return e, fetch_ca_info(e["ca"])

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(check_one, e): e for e in epis}
            for future in as_completed(futures, timeout=60):
                try:
                    e, info = future.result()
                except Exception:
                    continue

                if info.get("found") is False:
                    db_update_ca_status(e["id"], None, "INEXISTENTE")
                    alerts.append({"desc": e["descricao"].capitalize(),
                                   "ca": e["ca"], "status": "INEXISTENTE",
                                   "days": None, "validade": "-"})
                    continue

                if info.get("found") is None:
                    continue

                validade = info.get("validade")
                situacao = info.get("situacao")
                is_valid, days = check_validity(validade) if validade else (None, None)
                if situacao == "VENCIDO":
                    is_valid = False
                elif situacao == "VALIDO" and is_valid is None:
                    is_valid = True

                if is_valid is False:
                    status = "VENCIDO"
                elif is_valid is True and days is not None and days <= WARN_DAYS:
                    status = "EXPIRANDO"
                else:
                    status = "OK"

                db_update_ca_status(e["id"], validade, status)
                if status in ("VENCIDO", "EXPIRANDO"):
                    alerts.append({"desc": e["descricao"].capitalize(),
                                   "ca": e["ca"], "validade": validade or "?",
                                   "days": days, "status": status})

        self.after(0, lambda: self._on_expiry_check_done(alerts))

    def _on_expiry_check_done(self, alerts):
        self._ca_check_done = True
        self.refresh()
        if not alerts:
            return
        lines = ["Os seguintes EPIs possuem problemas:\n"]
        for a in alerts:
            if a["status"] == "INEXISTENTE":
                lines.append(f"  [CA INEXISTENTE]  {a['desc']}  CA {a['ca']}")
            elif a["status"] == "VENCIDO":
                lines.append(f"  [VENCIDO]  {a['desc']}  CA {a['ca']}  — venceu em {a['validade']}")
            else:
                lines.append(f"  [VENCE EM {a['days']} DIAS]  {a['desc']}  CA {a['ca']}  — {a['validade']}")
        lines.append("\nVerifique e providencie a renovacao ou substituicao.")
        messagebox.showwarning("Atencao — Problemas de CA", "\n".join(lines))

    # ── CRUD ─────────────────────────────────────────────────────────────────

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
            self._finish_cadastrar(desc, int(ca_s), int(qty_s), None)
            return
        self.btn_cadastrar.config(state="disabled", text="Verificando CA...")
        self._msg(self.lbl_cad_msg, "Consultando consultaca.com...")
        threading.Thread(target=self._check_before_save,
                         args=(desc, int(ca_s), int(qty_s)), daemon=True).start()

    def _check_before_save(self, desc, ca, qntd):
        info = fetch_ca_info(ca)
        self.after(0, lambda: self._on_ca_checked(desc, ca, qntd, info))

    def _on_ca_checked(self, desc, ca, qntd, info):
        self.btn_cadastrar.config(state="normal", text="+ Cadastrar")
        self._msg(self.lbl_cad_msg, "")

        # CA nao existe
        if info.get("found") is False:
            messagebox.showerror(
                "Cadastro bloqueado",
                f"O CA {ca} nao existe no sistema do MTE.\n"
                "Verifique o numero e tente novamente."
            )
            return

        # Sem conexao
        if info.get("found") is None:
            proceed = messagebox.askyesno(
                "Verificacao indisponivel",
                f"Nao foi possivel verificar o CA {ca}:\n{info.get('error','')}\n\n"
                "Deseja cadastrar mesmo assim?"
            )
            if proceed:
                self._finish_cadastrar(desc, ca, qntd, None)
            return

        errors = []

        # Vencimento
        situacao = info.get("situacao")
        validade = info.get("validade")
        is_valid, days = check_validity(validade) if validade else (None, None)
        if situacao == "VENCIDO":
            is_valid = False
        elif situacao == "VALIDO" and is_valid is None:
            is_valid = True
        if is_valid is False:
            date_str = f" (venceu em {validade})" if validade else ""
            errors.append(f"CA {ca} esta VENCIDO{date_str}.\n"
                          "EPIs com CA vencido nao podem ser cadastrados.")

        # Descricao incompativel
        page_title = (info.get("descricao") or "").lower()
        norm = page_title
        for s, d in [("\xe1","a"),("\xe3","a"),("\xe2","a"),("\xe9","e"),("\xea","e"),
                     ("\xed","i"),("\xf3","o"),("\xf5","o"),("\xf4","o"),("\xfa","u"),("\xe7","c")]:
            norm = norm.replace(s, d)
        keywords = DESC_KEYWORDS.get(desc, [])
        if norm and keywords and not any(kw in norm for kw in keywords):
            errors.append(
                f"Descricao incompativel: voce selecionou '{desc.capitalize()}',\n"
                f"mas o CA {ca} corresponde a:\n\"{(info.get('descricao') or '')[:90]}\""
            )

        if errors:
            messagebox.showerror("Cadastro bloqueado",
                                 "O EPI nao pode ser cadastrado:\n\n" + "\n\n".join(errors))
            return

        self._finish_cadastrar(desc, ca, qntd, info)

    def _finish_cadastrar(self, desc, ca, qntd, info=None):
        db_insert(desc, ca, qntd)

        # Se ja temos a info do CA (veio da validacao), salvar validade agora
        # assim a data aparece imediatamente sem precisar reiniciar
        if info and info.get("found") is True:
            validade = info.get("validade")
            situacao = info.get("situacao")
            is_valid, days = check_validity(validade) if validade else (None, None)
            if situacao == "VENCIDO":
                is_valid = False
            elif situacao == "VALIDO" and is_valid is None:
                is_valid = True
            if is_valid is False:
                status = "VENCIDO"
            elif is_valid is True and days is not None and days <= WARN_DAYS:
                status = "EXPIRANDO"
            else:
                status = "OK"
            # Buscar o id do EPI recem inserido
            import sqlite3 as _sq
            with get_conn() as c:
                row = c.execute(
                    "SELECT id FROM epis WHERE ca=? ORDER BY id DESC LIMIT 1", (ca,)
                ).fetchone()
                if row:
                    db_update_ca_status(row["id"], validade, status)

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
        try:
            eid  = int(sel[0])
            qntd = int(values[3])
        except (ValueError, IndexError):
            messagebox.showerror("Erro", "Nao foi possivel ler os dados da linha.")
            return
        desc_txt = values[1]
        ca_txt   = values[2]
        if qntd > 1:
            qty = simpledialog.askinteger(
                "Excluir EPI",
                f"{desc_txt} ({ca_txt}) tem {qntd} unidades.\n"
                f"Quantas deseja excluir? (1 a {qntd})",
                minvalue=1, maxvalue=qntd, parent=self)
            if qty is None:
                return
        else:
            if not messagebox.askyesno("Excluir EPI",
                                       f"Confirmar exclusao de '{desc_txt}' ({ca_txt})?"):
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

    def devolver(self):
        sel = self.alloc_tree.selection()
        if not sel:
            messagebox.showinfo("Atencao", "Selecione uma alocacao para devolver.")
            return
        values = self.alloc_tree.item(sel[0], "values")
        nome   = values[1]
        ca_txt = values[2]
        desc   = values[3]
        emp_id = int(sel[0])
        ca_num = int(ca_txt.replace("CA", "").strip())
        if not messagebox.askyesno("Devolver EPI",
                                   f"Confirmar devolucao de '{desc}' (CA {ca_num}) por {nome}?"):
            return
        db_delete_emprestimo(emp_id, ca_num)
        self.refresh()
        self.refresh_alloc()

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        tipos, total, alocados = db_stats()
        self.sv["tipos"]["text"]   = str(tipos)
        self.sv["total"]["text"]   = str(total)
        self.sv["alocado"]["text"] = str(alocados)

        selected_eid = None
        sel = self.tree.selection()
        if sel:
            try:
                selected_eid = int(sel[0])
            except ValueError:
                pass

        for row in self.tree.get_children():
            self.tree.delete(row)

        epis = db_fetch_epis()
        for pos, e in enumerate(epis, start=1):
            disp = e["qntd"] - e["qntd_alocada"]
            if disp > 3:
                stock_status = f"OK  {disp} disp."
            elif disp > 0:
                stock_status = f"!   {disp} disp."
            else:
                stock_status = "ESGOTADO"
            ca_val = e["ca_validade"] or ("verificando..." if SCRAPE_OK else "-")
            if e["ca_status"] == "INEXISTENTE":
                ca_val = "INEXISTENTE"
            tag = ca_row_tag(e["ca_validade"], e["ca_status"])
            iid = self.tree.insert("", "end",
                iid=str(e["id"]),
                values=(pos, e["descricao"].capitalize(), f"CA {e['ca']}",
                        e["qntd"], e["qntd_alocada"], stock_status, ca_val),
                tags=(tag,) if tag else ())
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
        if hasattr(self, "alloc_tree"):
            self.refresh_alloc()

    def refresh_alloc(self):
        query = self.entry_search.get().strip().lower()
        rows  = db_fetch_emprestimos()
        for row in self.alloc_tree.get_children():
            self.alloc_tree.delete(row)
        shown = 0
        for pos, r in enumerate(rows, start=1):
            desc = (r["descricao"] or "?").capitalize()
            nome = r["nome_funcionario"]
            ca   = str(r["ca_alocado"])
            if query and query not in nome.lower() and query not in ca and query not in desc.lower():
                continue
            self.alloc_tree.insert("", "end", iid=str(r["id"]),
                values=(pos, nome, f"CA {ca}", desc, r["horario"]))
            shown += 1
        total = len(rows)
        self.lbl_alloc_count.config(
            text=f"{shown} de {total} registro(s)" if query else f"{total} registro(s)")

    def _auto_refresh(self):
        self.refresh()
        self.after(2000, self._auto_refresh)

    def _msg(self, label, text, err=False):
        C = self.C
        label.config(text=text, fg=C["red_fg"] if err else C["grn_fg"])
        if text:
            self.after(4000, lambda: label.config(text=""))


if __name__ == "__main__":
    app = EPIApp()
    app.mainloop()
