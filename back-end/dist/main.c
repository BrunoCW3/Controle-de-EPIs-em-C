#include <stdio.h>
#include <stdlib.h>
#include "epi.h"

int main(void) {
    sqlite3 *db = db_open();
    db_init(db);

    int opcao = 0;
    while (opcao != 5) {
        printf("\n===== GERENCIADOR DE EPIs =====\n");
        printf("1 - Cadastrar EPI\n");
        printf("2 - Listar EPIs\n");
        printf("3 - Excluir EPI\n");
        printf("4 - Designar EPI\n");
        printf("5 - Sair\n");
        printf("Opcao: ");
        scanf("%d", &opcao);
        getchar();

        switch (opcao) {
            case 1: CADASTRAR_EPI(db); break;
            case 2: MOSTRAR_LISTA(db); break;
            case 3: EXCLUIR_EPI(db);   break;
            case 4: emprestar(db);     break;
            case 5: printf("Encerrando...\n"); break;
            default: printf("Opcao invalida.\n");
        }
    }

    db_close(db);
    return 0;
}
