# Gerenciador de EPIs

## Arquitetura

```
epis.db  (SQLite — banco compartilhado)
   ├── programa.exe   — backend C (console)
   └── epi_manager.py — GUI Python (tkinter)
```

## Arquivos

| Arquivo          | Funcao                                        |
|------------------|-----------------------------------------------|
| epi.h            | Declaracoes de structs e funcoes (C)          |
| epi.c            | Logica principal (C)                          |
| main.c           | Menu console (C)                              |
| epi_manager.py   | Interface grafica (Python)                    |
| epis.db          | Criado automaticamente na primeira execucao   |

## Rodar a GUI Python

### Dependencias (instalar uma vez)
```
pip install requests beautifulsoup4
```

### Executar
```
python epi_manager.py
```

## Compilar o backend C (Windows, GCC/MinGW)

1. Baixe o SQLite amalgamation em https://www.sqlite.org/download.html
2. Extraia sqlite3.c e sqlite3.h na mesma pasta dos arquivos .c
3. Compile:
```
gcc main.c epi.c sqlite3.c -o programa.exe -I. -lpthread
```

## Gerar .exe standalone (sem precisar de Python)

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "GerenciadorEPIs" epi_manager.py
```

O executavel estara em dist/GerenciadorEPIs.exe
Copie junto com o epis.db para rodar em qualquer Windows.

## Funcionalidades da GUI

- Cadastro de EPIs com validacao de CA em tempo real (consultaca.com)
- Bloqueio de CA inexistente ou vencido no cadastro
- Verificacao de vencimento em background para todos os EPIs cadastrados
- Badges coloridos na tabela: vencido (vermelho), vencendo em breve (laranja), inexistente (roxo)
- Popup de alerta na inicializacao para CAs com problema
- Aba de Alocacoes com historico, busca e devolucao de EPIs
- Banco SQLite compartilhado com o backend C
- Auto-refresh a cada 2 segundos
