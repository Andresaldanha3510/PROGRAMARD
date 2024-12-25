import sqlite3
import psycopg2
from psycopg2 import sql
import sys
import os
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv()

# Configurações do SQLite
SQLITE_DB_PATH = os.getenv('SQLITE_DB_PATH', 'database.db')  # Caminho para o seu banco de dados SQLite

# Configurações do PostgreSQL
PG_HOST = os.getenv('PG_HOST')
PG_PORT = os.getenv('PG_PORT', '5432')
PG_DB = os.getenv('PG_DB')
PG_USER = os.getenv('PG_USER')
PG_PASSWORD = os.getenv('PG_PASSWORD')

def connect_sqlite(db_path):
    try:
        conn = sqlite3.connect(db_path)
        print("Conectado ao SQLite com sucesso.")
        return conn
    except sqlite3.Error as e:
        print(f"Erro ao conectar ao SQLite: {e}")
        sys.exit(1)

def connect_postgres(host, port, dbname, user, password):
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password
        )
        print("Conectado ao PostgreSQL com sucesso.")
        return conn
    except psycopg2.Error as e:
        print(f"Erro ao conectar ao PostgreSQL: {e}")
        sys.exit(1)

def migrate_table(sqlite_conn, pg_conn, table_name, columns, primary_key=None):
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    # Seleciona todos os dados da tabela SQLite
    try:
        sqlite_cursor.execute(f"SELECT * FROM {table_name}")
        rows = sqlite_cursor.fetchall()
        print(f"Selecionados {len(rows)} registros da tabela '{table_name}' no SQLite.")
    except sqlite3.Error as e:
        print(f"Erro ao selecionar dados da tabela '{table_name}': {e}")
        return

    # Prepara a instrução SQL para inserção no PostgreSQL
    placeholders = ', '.join(['%s'] * len(columns))
    insert_query = sql.SQL("INSERT INTO {table} ({fields}) VALUES ({values})").format(
        table=sql.Identifier(table_name),
        fields=sql.SQL(', ').join(map(sql.Identifier, columns)),
        values=sql.SQL(placeholders)
    )

    # Opcional: Adicione lógica para evitar duplicatas usando ON CONFLICT
    if primary_key:
        conflict_fields = ', '.join([primary_key])
        update_fields = ', '.join([f"{col} = EXCLUDED.{col}" for col in columns if col != primary_key])
        insert_query += sql.SQL(" ON CONFLICT ({pk}) DO UPDATE SET {updates}").format(
            pk=sql.SQL(', ').join(map(sql.Identifier, [primary_key])),
            updates=sql.SQL(', ').join(map(sql.SQL, update_fields.split(', ')))
        )

    # Insere cada linha no PostgreSQL
    sucesso = 0
    falhas = 0
    for row in rows:
        try:
            pg_cursor.execute(insert_query, row)
            sucesso += 1
        except psycopg2.Error as e:
            print(f"Erro ao inserir dados na tabela '{table_name}': {e}")
            falhas += 1

    # Commit das alterações
    try:
        pg_conn.commit()
        print(f"Inseridos {sucesso} registros na tabela '{table_name}' no PostgreSQL.")
        if falhas > 0:
            print(f"{falhas} registros não foram inseridos na tabela '{table_name}'.")
    except psycopg2.Error as e:
        print(f"Erro ao commitar transações na tabela '{table_name}': {e}")
        pg_conn.rollback()

    pg_cursor.close()

def main():
    # Conectar ao SQLite
    sqlite_conn = connect_sqlite(SQLITE_DB_PATH)

    # Conectar ao PostgreSQL
    pg_conn = connect_postgres(PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD)

    # Definir tabelas e suas colunas
    tabelas = {
        'saldo_global': {
            'columns': ['id', 'saldo'],
            'primary_key': 'id'
        },
        'rd': {
            'columns': [
                'id', 'solicitante', 'funcionario', 'data', 'centro_custo',
                'valor', 'status', 'valor_adicional', 'adicional_data',
                'valor_despesa', 'saldo_devolver', 'data_fechamento',
                'arquivos', 'aprovado_data', 'liberado_data', 'valor_liberado'
            ],
            'primary_key': 'id'
        }
    }

    # Migrar cada tabela
    for tabela, detalhes in tabelas.items():
        migrate_table(
            sqlite_conn,
            pg_conn,
            tabela,
            detalhes['columns'],
            primary_key=detalhes.get('primary_key')
        )

    # Fechar conexões
    sqlite_conn.close()
    pg_conn.close()

    print("Migração concluída com sucesso!")

if __name__ == '__main__':
    main()
