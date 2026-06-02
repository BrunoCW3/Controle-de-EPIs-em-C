#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "epi.h"

/* ── Valid EPI descriptions (unchanged from original) ───────────────────── */

static const char *VALIDOS[] = {
    "capacete", "luva", "bota", "cinto de seguranca",
    "mangote", "oculos", "protetor auricular", NULL
};

int filtro_descricao(const char *desc) {
    for (int i = 0; VALIDOS[i] != NULL; i++)
        if (strcmp(desc, VALIDOS[i]) == 0) return 1;
    return 0;
}

/* ── DB lifecycle ───────────────────────────────────────────────────────── */

sqlite3 *db_open(void) {
    sqlite3 *db;
    if (sqlite3_open(DB_PATH, &db) != SQLITE_OK) {
        fprintf(stderr, "Erro ao abrir banco: %s\n", sqlite3_errmsg(db));
        exit(1);
    }
    return db;
}

void db_close(sqlite3 *db) {
    sqlite3_close(db);
}

void db_init(sqlite3 *db) {
    const char *sql =
        "CREATE TABLE IF NOT EXISTS epis ("
        "  id            INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  descricao     TEXT    NOT NULL,"
        "  ca            INTEGER NOT NULL,"
        "  qntd          INTEGER NOT NULL CHECK(qntd >= 0),"
        "  qntd_alocada  INTEGER NOT NULL DEFAULT 0 CHECK(qntd_alocada >= 0)"
        ");"
        "CREATE TABLE IF NOT EXISTS emprestimos ("
        "  id                INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  nome_funcionario  TEXT    NOT NULL,"
        "  horario           TEXT    NOT NULL,"
        "  ca_alocado        INTEGER NOT NULL,"
        "  FOREIGN KEY (ca_alocado) REFERENCES epis(ca)"
        ");";

    char *err = NULL;
    if (sqlite3_exec(db, sql, NULL, NULL, &err) != SQLITE_OK) {
        fprintf(stderr, "Erro ao inicializar banco: %s\n", err);
        sqlite3_free(err);
        exit(1);
    }
}

/* ── CADASTRAR_EPI ──────────────────────────────────────────────────────── */

void CADASTRAR_EPI(sqlite3 *db) {
    char desc[100];
    int  ca, qntd;

    /* Validate description */
    do {
        printf("Digite a descricao do EPI:\n");
        fgets(desc, sizeof(desc), stdin);
        strtok(desc, "\n");
        if (!filtro_descricao(desc))
            printf("Descricao invalida. EPIs validos: capacete, luva, bota, "
                   "cinto de seguranca, mangote, oculos, protetor auricular\n\n");
    } while (!filtro_descricao(desc));

    printf("Digite o CA do EPI: ");
    scanf("%d", &ca);
    getchar();

    do {
        printf("Digite a quantidade: ");
        scanf("%d", &qntd);
        getchar();
        if (qntd < 1) printf("Quantidade invalida.\n");
    } while (qntd < 1);

    const char *sql =
        "INSERT INTO epis (descricao, ca, qntd, qntd_alocada) "
        "VALUES (?, ?, ?, 0);";

    sqlite3_stmt *stmt;
    sqlite3_prepare_v2(db, sql, -1, &stmt, NULL);
    sqlite3_bind_text(stmt, 1, desc,  -1, SQLITE_STATIC);
    sqlite3_bind_int (stmt, 2, ca);
    sqlite3_bind_int (stmt, 3, qntd);

    if (sqlite3_step(stmt) == SQLITE_DONE)
        printf(qntd > 1 ? "EPIs cadastrados com sucesso!\n"
                        : "EPI cadastrado com sucesso!\n");
    else
        fprintf(stderr, "Erro ao cadastrar: %s\n", sqlite3_errmsg(db));

    sqlite3_finalize(stmt);
}

/* ── MOSTRAR_LISTA ──────────────────────────────────────────────────────── */

void MOSTRAR_LISTA(sqlite3 *db) {
    const char *sql =
        "SELECT id, descricao, ca, qntd, qntd_alocada FROM epis ORDER BY id;";

    sqlite3_stmt *stmt;
    sqlite3_prepare_v2(db, sql, -1, &stmt, NULL);

    printf("\n---------LISTA DE EPIs CADASTRADOS---------\n");
    int found = 0;
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        found = 1;
        printf("ID: %d\n",              sqlite3_column_int (stmt, 0));
        printf("Descricao: %s\n",       sqlite3_column_text(stmt, 1));
        printf("CA: %d\n",              sqlite3_column_int (stmt, 2));
        printf("Quantidade: %d\n",      sqlite3_column_int (stmt, 3));
        printf("Qtd alocada: %d\n\n",   sqlite3_column_int (stmt, 4));
    }
    if (!found) printf("Nenhum EPI cadastrado.\n");
    sqlite3_finalize(stmt);
}

/* ── EXCLUIR_EPI ────────────────────────────────────────────────────────── */

void EXCLUIR_EPI(sqlite3 *db) {
    MOSTRAR_LISTA(db);

    int id;
    printf("Digite o ID do EPI que deseja excluir: ");
    scanf("%d", &id);
    getchar();

    /* Fetch current quantity */
    const char *sel = "SELECT qntd FROM epis WHERE id = ?;";
    sqlite3_stmt *stmt;
    sqlite3_prepare_v2(db, sel, -1, &stmt, NULL);
    sqlite3_bind_int(stmt, 1, id);

    if (sqlite3_step(stmt) != SQLITE_ROW) {
        printf("ID nao encontrado.\n");
        sqlite3_finalize(stmt);
        return;
    }
    int qntd_atual = sqlite3_column_int(stmt, 0);
    sqlite3_finalize(stmt);

    int qntd_excluir = qntd_atual; /* default: remove all */

    if (qntd_atual > 1) {
        do {
            printf("Quantos EPIs desse grupo deseja eliminar? (1-%d): ", qntd_atual);
            scanf("%d", &qntd_excluir);
            getchar();
            if (qntd_excluir < 1 || qntd_excluir > qntd_atual)
                printf("Quantidade invalida.\n");
        } while (qntd_excluir < 1 || qntd_excluir > qntd_atual);
    }

    if (qntd_excluir < qntd_atual) {
        /* Reduce quantity */
        const char *upd = "UPDATE epis SET qntd = qntd - ? WHERE id = ?;";
        sqlite3_prepare_v2(db, upd, -1, &stmt, NULL);
        sqlite3_bind_int(stmt, 1, qntd_excluir);
        sqlite3_bind_int(stmt, 2, id);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    } else {
        /* Delete entire row */
        const char *del = "DELETE FROM epis WHERE id = ?;";
        sqlite3_prepare_v2(db, del, -1, &stmt, NULL);
        sqlite3_bind_int(stmt, 1, id);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }

    printf("Exclusao bem-sucedida!\n");
}

/* ── emprestar ──────────────────────────────────────────────────────────── */

void emprestar(sqlite3 *db) {
    char nome[50];
    printf("Qual o nome do funcionario? ");
    fgets(nome, sizeof(nome), stdin);
    strtok(nome, "\n");

    MOSTRAR_LISTA(db);

    int id;
    printf("Selecione o ID do EPI a ser designado para %s: ", nome);
    scanf("%d", &id);
    getchar();

    /* Check availability */
    const char *sel =
        "SELECT ca, qntd, qntd_alocada FROM epis WHERE id = ?;";
    sqlite3_stmt *stmt;
    sqlite3_prepare_v2(db, sel, -1, &stmt, NULL);
    sqlite3_bind_int(stmt, 1, id);

    if (sqlite3_step(stmt) != SQLITE_ROW) {
        printf("ID nao encontrado.\n");
        sqlite3_finalize(stmt);
        return;
    }
    int ca       = sqlite3_column_int(stmt, 0);
    int qntd     = sqlite3_column_int(stmt, 1);
    int alocados = sqlite3_column_int(stmt, 2);
    sqlite3_finalize(stmt);

    if (alocados >= qntd) {
        printf("Estoque esgotado para este EPI.\n");
        return;
    }

    /* Increment qntd_alocada */
    const char *upd =
        "UPDATE epis SET qntd_alocada = qntd_alocada + 1 WHERE id = ?;";
    sqlite3_prepare_v2(db, upd, -1, &stmt, NULL);
    sqlite3_bind_int(stmt, 1, id);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    /* Log the loan with timestamp */
    time_t now = time(NULL);
    char   ts[30];
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", localtime(&now));

    const char *ins =
        "INSERT INTO emprestimos (nome_funcionario, horario, ca_alocado) "
        "VALUES (?, ?, ?);";
    sqlite3_prepare_v2(db, ins, -1, &stmt, NULL);
    sqlite3_bind_text(stmt, 1, nome, -1, SQLITE_STATIC);
    sqlite3_bind_text(stmt, 2, ts,   -1, SQLITE_STATIC);
    sqlite3_bind_int (stmt, 3, ca);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);

    printf("EPI designado com sucesso para %s!\n", nome);
}
