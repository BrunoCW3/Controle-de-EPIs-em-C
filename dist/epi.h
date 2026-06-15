#ifndef EPI_H
#define EPI_H

#include <sqlite3.h>
#include <time.h>

#define DB_PATH "epis.db"

struct EPI {
    int  id;
    int  CA;
    int  QNTD;
    int  QNTD_ALOCADA;
    char DESCRICAO[100];
};

struct EMPRESTIMO {
    char   nome_funcionario[50];
    time_t horario_emprestimo;
    int    CA_alocado;
};

/* DB lifecycle */
sqlite3 *db_open(void);
void     db_close(sqlite3 *db);
void     db_init(sqlite3 *db);

/* Core operations */
void CADASTRAR_EPI (sqlite3 *db);
void MOSTRAR_LISTA (sqlite3 *db);
void EXCLUIR_EPI   (sqlite3 *db);
void emprestar     (sqlite3 *db);

/* Validation */
int  filtro_descricao(const char *desc);

#endif
