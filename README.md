# Gerenciador de EPIs — SQLite Edition

## Architecture

```
epis.db  (SQLite file — shared database)
   ├── accessed by:  programa.exe   (C backend — console)
   └── accessed by:  epi_manager.py (Python GUI)
```

Both programs read and write the **same `epis.db` file**.
They can run at the same time or independently — the database keeps everything in sync.

---

## Files

| File             | Purpose                                      |
|------------------|----------------------------------------------|
| `epi.h`          | Structs, constants, function declarations    |
| `epi.c`          | Core logic (cadastrar, excluir, designar...) |
| `main.c`         | Console menu entry point                     |
| `epi_manager.py` | Tkinter GUI — reads/writes epis.db directly  |
| `epis.db`        | Created automatically on first run           |

---

## 1 — Build the C program (Windows, using GCC / MinGW)

You need **SQLite3** for Windows:
1. Download the amalgamation from https://www.sqlite.org/download.html
   → "sqlite-amalgamation-XXXXXXX.zip"
2. Extract `sqlite3.c` and `sqlite3.h` into the same folder as your .c files.

Then compile:
```bash
gcc main.c epi.c sqlite3.c -o programa.exe -I. -lpthread
```

Run:
```bash
programa.exe
```

---

## 2 — Run the Python GUI

No extra installs needed (sqlite3 and tkinter are built into Python).

```bash
python epi_manager.py
```

> Place `epi_manager.py` in the **same folder** as `epis.db` (or let it create one there automatically).

---

## How sync works

- Python uses `PRAGMA journal_mode=WAL` so reads never block writes from C.
- The GUI auto-refreshes every 2 seconds — changes made via the C console appear in the window automatically.
- Both programs use the same table schema, so either one can create the database first.
