import psycopg2
from psycopg2.extras import DictCursor
from werkzeug.security import generate_password_hash
import os

# ============ Config. BD (Copie do seu app.py) ============
PG_HOST = os.getenv("PG_HOST", "dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB   = os.getenv("PG_DB", "programard_db")
PG_USER = os.getenv("PG_USER", "programard_db_user")
PG_PASSWORD = os.getenv("PG_PASSWORD", "hU9wJmIfgiyCg02KFQ3a4AropKSMopXr")

def get_pg_connection():
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            cursor_factory=DictCursor
        )
        return conn
    except psycopg2.Error as e:
        print(f"Erro ao conectar ao PostgreSQL: {e}")
        import sys
        sys.exit(1)

def criar_usuarios_e_migrar_saldo():
    # Lista de usuários padrão do seu sistema
    usuarios = [
        # (username,   senha_plana,      role,          setor)
        ('gestor',     '337146',         'gestor',      'servicos'),
        ('financeiro', '351073',         'financeiro',  'servicos'),
        ('solicitante','102030',         'solicitante', 'servicos'),
        ('supervisor', '223344',         'supervisor',  'servicos'),
    ]

    conn = get_pg_connection()
    cursor = conn.cursor()

    print("Iniciando cadastro de usuários na tabela 'usuarios'...")
    
    for username, senha_plana, role, setor in usuarios:
        password_hash = generate_password_hash(senha_plana)
        try:
            cursor.execute(
                """
                INSERT INTO usuarios (username, password_hash, role, setor)
                VALUES (%s, %s, %s, %s)
                """,
                (username, password_hash, role, setor)
            )
            print(f"-> Usuário '{username}' ({role} / {setor}) criado com sucesso.")
        except psycopg2.IntegrityError:
            print(f"-> Usuário '{username}' já existe. Ignorando.")
            conn.rollback() 
        except Exception as e:
            print(f"Erro ao criar usuário '{username}': {e}")
            conn.rollback()

    print("\nIniciando migração de saldo...")
    
    try:
        # 1. Busca o saldo da tabela ANTIGA
        cursor.execute("SELECT saldo FROM saldo_global LIMIT 1")
        resultado = cursor.fetchone()
        
        if resultado:
            saldo_antigo_servicos = resultado['saldo']
            print(f"Saldo encontrado na tabela 'saldo_global' antiga: {saldo_antigo_servicos}")

            # 2. Insere o saldo na tabela NOVA para o setor 'servicos'
            cursor.execute(
                """
                INSERT INTO saldo_global_por_setor (setor, saldo) 
                VALUES ('servicos', %s)
                ON CONFLICT (setor) DO UPDATE SET saldo = EXCLUDED.saldo
                """,
                (saldo_antigo_servicos,)
            )
            print(f"-> Saldo definido para 'servicos' na nova tabela 'saldo_global_por_setor'.")
        else:
            print("Tabela 'saldo_global' antiga está vazia. Definindo saldo de 'servicos' como 0.")
            cursor.execute(
                """
                INSERT INTO saldo_global_por_setor (setor, saldo) 
                VALUES ('servicos', 0)
                ON CONFLICT (setor) DO NOTHING
                """
            )

    except psycopg2.errors.UndefinedTable:
        print("Erro: A tabela 'saldo_global' antiga não foi encontrada.")
        print("Criando saldo de 'servicos' como 0 na nova tabela.")
        conn.rollback()
        try:
             cursor.execute(
                """
                INSERT INTO saldo_global_por_setor (setor, saldo) 
                VALUES ('servicos', 0)
                ON CONFLICT (setor) DO NOTHING
                """
            )
        except Exception as e_inner:
            print(f"Erro ao inserir saldo padrão: {e_inner}")
            conn.rollback()
            
    except Exception as e:
        print(f"\nErro ao migrar saldo: {e}")
        conn.rollback()
        
    conn.commit()
    cursor.close()
    conn.close()
    
    print("\nMigração de usuários e saldo concluída!")

if __name__ == "__main__":
    # Instale o 'werkzeug' antes de rodar: pip install werkzeug
    criar_usuarios_e_migrar_saldo()