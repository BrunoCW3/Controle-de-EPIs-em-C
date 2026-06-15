"""
Diagnostico rapido — mostra exatamente o que requests recebe do site.
Uso:
    python debug_ca.py 38664
    python debug_ca.py 99999999
"""
import sys
import re
import requests
from bs4 import BeautifulSoup

CA = sys.argv[1] if len(sys.argv) > 1 else "38664"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

print(f"\n=== Testando CA {CA} ===")
url = f"https://consultaca.com/{CA}"

resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)

print(f"Status HTTP : {resp.status_code}")
print(f"URL final   : {resp.url}")
print(f"Tamanho     : {len(resp.text)} caracteres")

soup      = BeautifulSoup(resp.text, "html.parser")
full_text = soup.get_text(separator="\n")

print(f"\n=== PRIMEIROS 1500 CARACTERES ===")
print(full_text[:1500])

print(f"\n=== LINHAS COM 'validade' ===")
found = [l.strip() for l in full_text.splitlines() if "validade" in l.lower() and l.strip()]
print("\n".join(found) if found else "NENHUMA")

print(f"\n=== LINHAS COM 'situa' ===")
found = [l.strip() for l in full_text.splitlines() if "situa" in l.lower() and l.strip()]
print("\n".join(found) if found else "NENHUMA")

print(f"\n=== DATAS DD/MM/YYYY ===")
dates = re.findall(r"\d{2}/\d{2}/\d{4}", full_text)
print(dates if dates else "NENHUMA")

print("\n=== FIM ===")
