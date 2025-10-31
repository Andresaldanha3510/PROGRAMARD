from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
    jsonify,
)
import psycopg2
from psycopg2.extras import DictCursor
import os
from dotenv import load_dotenv


# ==========================================================
# 1. IMPORTAÇÕES ADICIONADAS
# ==========================================================
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import logging
import google.generativeai as genai  # <--- ADICIONE ISTO
import mimetypes
import requests

# ==========================================================
load_dotenv()  # Certifique-se de que a API_KEY está no .env
try:
    # 1. Tenta pegar a chave principal
    API_KEY = os.environ.get("GEMINI_API_KEY")

    # 2. Se não achar, tenta a chave antiga como fallback
    if not API_KEY:
        API_KEY = os.environ.get("GOOGLE_API_KEY")

    # 3. Se ainda assim não achar, levanta um erro claro
    if not API_KEY:
        raise ValueError(
            "Nenhuma chave de API (GEMINI_API_KEY ou GOOGLE_API_KEY) foi encontrada no seu arquivo .env"
        )

    # 4. Configura a API com a chave encontrada
    genai.configure(api_key=API_KEY)

    # 5. Define UM modelo global para visão e texto (mais novo e rápido)
    model = genai.GenerativeModel("gemini-2.0-flash")

    logging.info("Google Gemini API configurada com sucesso usando gemini-1.5-flash.")

except Exception as e:
    logging.error(f"ERRO CRÍTICO AO CONFIGURAR API DO GEMINI: {e}")
    model = None  # Define como None se falhar

# ============ Config. Cloudflare R2 ============
import boto3
from botocore.client import Config
import json
from decimal import Decimal
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.utils import secure_filename

# Ajuste se necessário
R2_ACCESS_KEY = "97060093e2382cb9b485900551b6e470"
R2_SECRET_KEY = "f82c29e70532b18b1705ffc94aea2f62fe4c2a85a8c99ad30b6894f068582970"
R2_ENDPOINT = "https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com"
R2_BUCKET_NAME = "meu-bucket-r2"
R2_PUBLIC_URL = "https://pub-1e6f8559bc2b413c889fbf4860462599.r2.dev"


def get_r2_public_url(object_name):
    return f"{R2_PUBLIC_URL}/{object_name}"


def upload_file_to_r2(file_obj, object_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )
    file_obj.seek(0)
    s3.upload_fileobj(file_obj, R2_BUCKET_NAME, object_name)


def delete_file_from_r2(object_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )
    s3.delete_object(Bucket=R2_BUCKET_NAME, Key=object_name)


# ============ Config. Excel ============
import io
import xlsxwriter
import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY", "secret123")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = secret_key
logging.debug("SECRET_KEY carregado corretamente.")

# ============ Config. BD ============
PG_HOST = os.getenv("PG_HOST", "dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB   = os.getenv("PG_DB", "programard_db")
PG_USER = os.getenv("PG_USER", "programard_db_user")
PG_PASSWORD = os.getenv("PG_PASSWORD", "hU9wJmIfgiyCg02KFQ3a4AropKSMopXr")

LISTA_CATEGORIAS_DESPESA_IA = [
    "HOTEL",
    "REFEIÇÕES",
    "ÁGUA",
    "DESLOCAMENTO (UBER, TAXI, 99)",
    "QUILOMETRAGEM (R$ 0,81/Km rodado)",
    "COMBUSTÍVEL",
    "LOCAÇÃO AUTOMÓVEL",
    "PASSAGENS AÉREAS",
    "PEDÁGIO",
    "BORRACHARI / LAVA CAR",
    "TELEFONE",
    "CORREIO",
    "MATERIAL DE EPI",
    "CARTÓRIO",
    "DESPESAS C/ COPA",
    "DESPESAS C/ PEQUENOS ATIVOS",
    "TREINAMENTO/CURSO",
    "ÁCIDO",
    "SODA",
    "VP30",
    "D70",
    "FP91",
    "ADESIVO INDUSTRIAL",
    "OUTROS",
    "ESTACIONAMENTO",
]

# Adição do filtro personalizado para validar formato de data
import re


@app.template_filter("is_date_format")
def is_date_format(value):
    if value is None:
        return False
    if isinstance(value, str):
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        return bool(re.match(pattern, value))
    return False


def get_pg_connection():
    try:
        # CORREÇÃO: Usa as variáveis individuais (PG_HOST, PG_USER, etc.)
        # que já estão definidas no topo do seu app.py (linhas 149-153)
        
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            cursor_factory=DictCursor,
        )
        return conn
    except psycopg2.Error as e:
        logging.error(f"Erro ao conectar ao PostgreSQL: {e}")
        import sys
        sys.exit(1)


# ==========================================================
# 2. FUNÇÃO init_db() TOTALMENTE REFEITA
# ==========================================================
def init_db():
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # 1. Tabela de Empresas
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS empresas (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL UNIQUE
    );
    """
    )
    cursor.execute(
        "INSERT INTO empresas (nome) VALUES ('Serviços'), ('Comercial') ON CONFLICT (nome) DO NOTHING"
    )

    # 2. Tabela de Usuários (com senhas hash)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL, -- 'gestor', 'financeiro', 'solicitante', 'supervisor', 'funcionario'
        empresa_id INTEGER NOT NULL REFERENCES empresas(id)
    );
    """
    )

    # 3. Migrar usuários antigos para a empresa "Serviços"
    cursor.execute("SELECT id FROM empresas WHERE nome = 'Serviços'")
    servicos_id_row = cursor.fetchone()

    if not servicos_id_row:
        logging.error("Falha ao encontrar a empresa 'Serviços' no BD.")
        conn.close()
        return

    servicos_id = servicos_id_row[0]

    users_to_migrate = [
        ("gestor", "337146", "gestor"),
        ("financeiro", "351073", "financeiro"),
        ("solicitante", "102030", "solicitante"),
        ("supervisor", "223344", "supervisor"),
    ]

    for user, pwd, role in users_to_migrate:
        hash_pwd = generate_password_hash(pwd)
        cursor.execute(
            "INSERT INTO usuarios (username, password_hash, role, empresa_id) VALUES (%s, %s, %s, %s) ON CONFLICT (username) DO NOTHING",
            (user, hash_pwd, role, servicos_id),
        )

    # 4. Tabela RD (com colunas de adição)
    create_rd_table = """
    CREATE TABLE IF NOT EXISTS rd (
        id TEXT NOT NULL, -- Removido PRIMARY KEY para chave composta
        solicitante TEXT NOT NULL,
        funcionario TEXT NOT NULL,
        data DATE NOT NULL,
        centro_custo TEXT NOT NULL,
        valor NUMERIC(15,2) NOT NULL,
        status TEXT DEFAULT 'Pendente',
        valor_adicional NUMERIC(15,2) DEFAULT 0,
        adicional_data DATE,
        valor_despesa NUMERIC(15,2),
        saldo_devolver NUMERIC(15,2),
        data_fechamento DATE,
        arquivos TEXT,
        aprovado_data DATE,
        liberado_data DATE,
        valor_liberado NUMERIC(15,2) DEFAULT 0,
        observacao TEXT,
        tipo TEXT DEFAULT 'credito alelo',
        unidade_negocio TEXT,
        motivo_recusa TEXT,
        adicionais_individuais TEXT,
        data_saldo_devolvido DATE,
        data_credito_solicitado DATE,
        data_credito_liberado DATE,
        data_debito_despesa DATE,
        
        gestor_aprovador_id INTEGER REFERENCES usuarios(id),
        solicitante_id INTEGER REFERENCES usuarios(id),
        funcionario_id INTEGER REFERENCES usuarios(id)
    );
    """
    cursor.execute(create_rd_table)

    # Adicionando chave primária composta (id, empresa_id) se não existir

    # 5. Tabela historico_acoes
    create_historico_acoes_table = """
    CREATE TABLE IF NOT EXISTS historico_acoes (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        data_acao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        usuario TEXT NOT NULL,
        acao TEXT NOT NULL,
        detalhes TEXT
        
    );
    """
    cursor.execute(create_historico_acoes_table)

    LISTA_CATEGORIAS_DESPESA = [
        "HOTEL",
        "REFEIÇÕES",
        "ÁGUA",
        "DESLOCAMENTO",
        "QUILOMETRAGEM",
        "COMBUSTÍVEL",
        "LOCAÇÃO AUTO",
        "PASSAGENS AÉREAS",
        "PEDÁGIO",
        "BORRACHARIA",
        "TELEFONE",
        "CORREIO",
        "MATERIAL DE ESCRITÓRIO",
        "CARTÓRIO",
        "DESPESAS C/ CORREIO",
        "DESPESAS C/ PEDÁGIO",
        "TREINAMENTO",
        "ÁCIDO",
        "SODA",
        "VP30",
        "D70",
        "FP91",
        "ADESIVO INDUSTRIAL",
        "OUTROS",
    ]

    # 6. Tabela funcionarios
    create_funcionarios_table = """
    CREATE TABLE IF NOT EXISTS funcionarios (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        centro_custo TEXT NOT NULL,
        unidade_negocio TEXT NOT NULL
        
    );
    """
    cursor.execute(create_funcionarios_table)

    # 7. Tabela historico_exclusao
    create_historico_table = """
    CREATE TABLE IF NOT EXISTS historico_exclusao (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        solicitante TEXT NOT NULL,
        valor NUMERIC(15,2) NOT NULL,
        data_exclusao DATE NOT NULL,
        usuario_excluiu TEXT NOT NULL
        
    );
    """
    cursor.execute(create_historico_table)

    # 8. Adicionar colunas extras na RD (se não existirem)
    for col in [
        "data_credito_solicitado",
        "data_credito_liberado",
        "data_debito_despesa",
        "pronto_fechamento",
        "anexo_divergente",
        "motivo_divergente",
        "gestor_aprovador_id",
        "solicitante_id",
        "funcionario_id",
    ]:
        cursor.execute(
            f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='rd' AND column_name='{col}'
        """
        )
        if not cursor.fetchone():
            if col == "pronto_fechamento" or col == "anexo_divergente":
                cursor.execute(f"ALTER TABLE rd ADD COLUMN {col} BOOLEAN DEFAULT FALSE")
            elif col == "motivo_divergente":
                cursor.execute(f"ALTER TABLE rd ADD COLUMN {col} TEXT")

            elif (
                col == "gestor_aprovador_id"
                or col == "solicitante_id"
                or col == "funcionario_id"
            ):
                cursor.execute(
                    f"ALTER TABLE rd ADD COLUMN {col} INTEGER REFERENCES usuarios(id)"
                )
            else:
                cursor.execute(f"ALTER TABLE rd ADD COLUMN {col} DATE")

    # 9. Refatorar 'saldo_global' para ser por empresa
    cursor.execute("DROP TABLE IF EXISTS saldo_global CASCADE")
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS saldo_global (
        id SERIAL PRIMARY KEY,
        saldo NUMERIC(15,2) DEFAULT 30000,
        empresa_id INTEGER NOT NULL REFERENCES empresas(id) UNIQUE
    );
    """
    )
    # Garante que ambas empresas tenham um saldo inicial
    cursor.execute(
        "INSERT INTO saldo_global (saldo, empresa_id) SELECT 30000, id FROM empresas ON CONFLICT (empresa_id) DO NOTHING"
    )

    # 10. MODIFICAÇÃO CRUCIAL: Adicionar 'empresa_id' a todas as tabelas de dados
    tables_to_update = ["rd", "funcionarios", "historico_exclusao", "historico_acoes"]
    for table in tables_to_update:
        cursor.execute(
            f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='{table}' AND column_name='empresa_id'
        """
        )
        if not cursor.fetchone():
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN empresa_id INTEGER REFERENCES empresas(id)"
            )

        # Marca todos os dados existentes como sendo da "Serviços"
        cursor.execute(
            f"UPDATE {table} SET empresa_id = %s WHERE empresa_id IS NULL",
            (servicos_id,),
        )

    cursor.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'rd_pkey'
            ) THEN
                UPDATE rd SET empresa_id = %s WHERE empresa_id IS NULL;
                ALTER TABLE rd ADD PRIMARY KEY (id, empresa_id);
            END IF;
        END $$;
    """,
        (servicos_id,),
    )

    # ==========================================================
    # 2. DEPOIS: A TABELA 'despesa_itens'
    # ==========================================================
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS despesa_itens (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        empresa_id INTEGER NOT NULL REFERENCES empresas(id),
        categoria TEXT NOT NULL,
        valor NUMERIC(15,2) NOT NULL DEFAULT 0,
        anexo_filename TEXT,
        anexo_url TEXT,
        data_lancamento TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (rd_id, empresa_id) REFERENCES rd (id, empresa_id) ON DELETE CASCADE
    );
    """
    )

    # Commit e fechamento no final de tudo
    conn.commit()
    cursor.close()
    conn.close()
    logging.info("Banco de dados inicializado com sucesso (Multi-Tenancy).")


def login_required(f):
    """
    Cria um decorador @login_required que verifica se o usuário está na sessão.
    Usa a mesma lógica da sua rota 'index'.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_role" not in session:
            flash("Acesso negado. Por favor, faça login.", "error")
            # Redireciona para 'index', que é a sua página de login
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated_function


def generate_custom_id():
    # Esta função agora gera IDs sequenciais globais, independente da empresa.
    current_year = datetime.now().year % 100
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # NÃO filtra mais por empresa_id, busca o ID máximo DO ANO em todo o banco
    cursor.execute(
        """
        SELECT id FROM rd
        WHERE split_part(id, '.', 2)::INTEGER=%s
        ORDER BY (split_part(id, '.',1))::INTEGER DESC LIMIT 1
    """,
        (current_year,),
    )

    last_id = cursor.fetchone()
    conn.close()

    if not last_id:
        return f"400.{current_year}"
    last_str = last_id[0]
    last_num_str, _ = last_str.split(".")
    last_num = int(last_num_str)
    return f"{last_num+1}.{current_year}"


def user_role():
    return session.get("user_role")


def is_solicitante():
    return user_role() == "solicitante"


def is_gestor():
    return user_role() == "gestor"


def is_financeiro():
    return user_role() == "financeiro"


def can_add():
    return user_role() in ["solicitante", "gestor", "financeiro", "funcionario"]


def can_edit(status, solicitante):  # <-- 'solicitante' agora é ignorado aqui
    if status == "Fechado":
        return False
    
    # Lógica do Solicitante corrigida (SEM CHECAGEM DE DONO)
    if is_solicitante():
        # is_owner = session.get("username") == solicitante <-- REMOVIDO
        # Pode editar se o status permitir
        return status in ["Pendente", "Fechamento Recusado"] # <-- REMOVIDO 'and is_owner'
    
    # Lógica para outros (Gestor, Financeiro, etc.)
    if (
        is_gestor()
        or is_financeiro()
        or user_role() == "supervisor"
        or user_role() == "funcionario"
    ):
        return True
    return False

def can_delete(status, solicitante):
    if status == "Fechado":
        return False
    
    # Lógica do Solicitante corrigida (SEM CHECAGEM DE DONO)
    if status == "Pendente" and is_solicitante():
        # is_owner = session.get("username") == solicitante <-- REMOVIDO
        return True  # <-- Retorna True direto, sem checar o dono
    
    # Lógica para outros (Gestor, Financeiro)
    if (is_gestor() or is_financeiro()) and status in [
        "Pendente",
        "Aprovado",
        "Liberado",
    ]:
        return True
    return False

def can_approve(status):
    if status == "Pendente" and is_gestor():
        return True
    if status == "Fechamento Solicitado" and is_gestor():
        return True
    if status == "Aprovado" and is_financeiro():
        return True
    return False


def can_request_additional(status):
    return is_solicitante() and status == "Liberado"


def can_close(status):
    return (is_solicitante() or user_role() == "funcionario") and status == "Liberado"


# ==========================================================
# 3. FUNÇÕES DE SALDO ATUALIZADAS
# ==========================================================
def get_saldo_global(empresa_id):
    if not empresa_id:
        logging.error("get_saldo_global chamado sem empresa_id")
        return 0
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute(
        "SELECT saldo FROM saldo_global WHERE empresa_id = %s LIMIT 1", (empresa_id,)
    )
    saldo_row = cursor.fetchone()
    conn.close()
    if saldo_row:
        return saldo_row[0]
    logging.warning(f"Nenhum saldo global encontrado para empresa_id {empresa_id}")
    return Decimal('0.00')


def set_saldo_global(novo_saldo, empresa_id):
    if not empresa_id:
        logging.error("set_saldo_global chamado sem empresa_id")
        return
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute(
        "UPDATE saldo_global SET saldo=%s WHERE empresa_id = %s",
        (novo_saldo, empresa_id),
    )
    conn.commit()
    conn.close()


# ==========================================================
# 4. FUNÇÃO DE HISTÓRICO ATUALIZADA
# ==========================================================
def registrar_historico(conn, rd_id, acao, detalhes=""):
    """Registra uma nova ação no histórico de uma RD."""
    try:
        usuario = session.get("user_role", "Sistema")
        # Pega o empresa_id da sessão
        empresa_id_logada = session.get("empresa_id")

        if not empresa_id_logada:
            logging.warning(
                f"Tentativa de registrar histórico sem empresa_id na sessão para RD {rd_id}"
            )
            # Tenta buscar o empresa_id pelo RD_ID como último recurso
            cursor_fallback = conn.cursor(cursor_factory=DictCursor)
            cursor_fallback.execute(
                "SELECT empresa_id FROM rd WHERE id = %s LIMIT 1", (rd_id,)
            )
            rd_row = cursor_fallback.fetchone()
            if rd_row:
                empresa_id_logada = rd_row["empresa_id"]
            else:
                logging.error(
                    f"Falha CRÍTICA: não foi possível determinar empresa_id para histórico da RD {rd_id}"
                )
                return

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO historico_acoes (rd_id, usuario, acao, detalhes, empresa_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (rd_id, usuario, acao, detalhes, empresa_id_logada),
        )
    except psycopg2.Error as e:
        logging.error(f"Falha ao registrar histórico para RD {rd_id}: {e}")
    except Exception as e:
        logging.error(f"Erro inesperado ao registrar histórico: {e}")


def format_currency(value):
    if value is None:
        return "0,00"
    s = f"{value:,.2f}"
    parts = s.split(".")
    left = parts[0].replace(",", ".")
    right = parts[1]
    return f"{left},{right}"


# Registrando no Jinja
app.jinja_env.globals.update(
    get_r2_public_url=get_r2_public_url,
    is_gestor=is_gestor,
    is_solicitante=is_solicitante,
    is_financeiro=is_financeiro,
    format_currency=format_currency,
)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Lógica de LOGIN (mantida)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM usuarios WHERE username = %s", (username,))
        user_data = cursor.fetchone()
        conn.close()

        if user_data and check_password_hash(user_data["password_hash"], password):
            session["user_role"] = user_data["role"]
            session["user_id"] = user_data["id"]
            session["username"] = user_data["username"]
            session["empresa_id"] = user_data["empresa_id"]
            flash(f"Login como {user_data['role']} bem-sucedido.")

            # ==========================================================
            # === 1. INÍCIO DA CORREÇÃO (Após o Login) ===
            # ==========================================================
            # Redirecionamento inteligente após o login
            if user_data["role"] == "funcionario" and is_mobile_device():
                return redirect(url_for("mobile_dashboard"))
            else:
                # Se for outro user OU funcionário no computador, vai para o index
                return redirect(url_for("index"))
            # ==========================================================
            # === FIM DA CORREÇÃO ===
            # ==========================================================

        else:
            flash("Credenciais inválidas.")
            return render_template(
                "index.html",
                error="Credenciais inválidas",
                format_currency=format_currency,
            )

    # Se não estiver logado, mostra a tela de login
    if "user_role" not in session:
        return render_template(
            "index.html", error=None, format_currency=format_currency
        )

    # ==========================================================
    # === 2. INÍCIO DA CORREÇÃO (Para quem já está logado) ===
    # ==========================================================
    # Se for funcionário E estiver no telemóvel, redireciona
    if session.get("user_role") == "funcionario" and is_mobile_device():
        return redirect(url_for("mobile_dashboard"))
    # ==========================================================
    # === FIM DA CORREÇÃO ===
    # ==========================================================

    # =================================================================
    # Lógica GET Unificada (Isto agora só corre se NÃO for funcionário no telemóvel)
    # =================================================================
    try:
        empresa_id_logada = session["empresa_id"]
        user_id_logado = session["user_id"]
        current_role = user_role()  # Pega o papel atual
    except KeyError:
        flash("Erro de sessão. Faça login novamente.")
        return redirect(url_for("logout"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # --- Busca listas para o modal de Adicionar RD ---
    gestores_disponiveis = []
    usuarios_disponiveis = []
    if can_add():
        cursor.execute(
            "SELECT id, username FROM usuarios WHERE role = 'gestor' AND empresa_id = %s",
            (empresa_id_logada,),
        )
        gestores_disponiveis = cursor.fetchall()
        cursor.execute(
            "SELECT id, username FROM usuarios WHERE empresa_id = %s ORDER BY username",
            (empresa_id_logada,),
        )
        usuarios_disponiveis = cursor.fetchall()

    # --- Lógica de Filtro Unificada ---
    base_select = "SELECT * FROM rd WHERE empresa_id = %s"
    params = [empresa_id_logada]
    additional_filter = ""

    if current_role == "funcionario":
        additional_filter = " AND funcionario_id = %s"
        params.append(user_id_logado)

    elif current_role == "gestor":
        additional_filter = " AND gestor_aprovador_id = %s"
        params.append(user_id_logado)

    # --- Busca as RDs ---
    (
        pendentes,
        aprovados,
        liberados,
        fechamento_solicitado,
        fechamento_recusado,
        saldos_a_devolver,
        fechados,
    ) = ([], [], [], [], [], [], [])
    divergentes_count = 0

    if current_role == "supervisor":
        cursor.execute(f"{base_select} AND status='Liberado'", (empresa_id_logada,))
        liberados = cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) FROM rd WHERE anexo_divergente=TRUE AND empresa_id = %s",
            (empresa_id_logada,),
        )
        divergentes_count = cursor.fetchone()[0]
    else:
        cursor.execute(
            f"{base_select} AND status='Pendente' {additional_filter}", tuple(params)
        )
        pendentes = cursor.fetchall()
        cursor.execute(
            f"{base_select} AND status='Aprovado' {additional_filter}", tuple(params)
        )
        aprovados = cursor.fetchall()
        cursor.execute(
            f"{base_select} AND status='Liberado' {additional_filter}", tuple(params)
        )
        liberados = cursor.fetchall()
        cursor.execute(
            f"{base_select} AND status='Fechamento Solicitado' {additional_filter}",
            tuple(params),
        )
        fechamento_solicitado = cursor.fetchall()
        cursor.execute(
            f"{base_select} AND status='Fechamento Recusado' {additional_filter}",
            tuple(params),
        )
        fechamento_recusado = cursor.fetchall()
        cursor.execute(
            f"{base_select} AND status='Saldos a Devolver' {additional_filter}",
            tuple(params),
        )
        saldos_a_devolver = cursor.fetchall()
        cursor.execute(
            f"{base_select} AND status='Fechado' {additional_filter}", tuple(params)
        )
        fechados = cursor.fetchall()

        if current_role != "financeiro":
            cursor.execute(
                f"SELECT COUNT(*) FROM rd WHERE anexo_divergente=TRUE AND empresa_id = %s {additional_filter}",
                tuple(params),
            )
            count_row = cursor.fetchone()
            divergentes_count = count_row[0] if count_row else 0
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM rd WHERE anexo_divergente=TRUE AND empresa_id = %s",
                (empresa_id_logada,),
            )
            count_row = cursor.fetchone()
            divergentes_count = count_row[0] if count_row else 0

    # --- Finalização ---
    saldo_global = get_saldo_global(empresa_id_logada)
    adicional_id = request.args.get("adicional")
    fechamento_id = request.args.get("fechamento")
    active_tab = request.args.get("active_tab", "tab1")
    conn.close()

    return render_template(
        "index.html",
        error=None,
        format_currency=format_currency,
        user_role=current_role,
        saldo_global=saldo_global if is_financeiro() else None,
        pendentes=pendentes,
        aprovados=aprovados,
        liberados=liberados,
        fechamento_solicitado=fechamento_solicitado,
        fechamento_recusado=fechamento_recusado,
        saldos_a_devolver=saldos_a_devolver,
        fechados=fechados,
        divergentes_count=divergentes_count,
        can_add=can_add(),
        can_delete_func=can_delete,
        can_edit_func=can_edit,
        can_approve_func=can_approve,
        can_request_additional=can_request_additional,
        can_close=can_close,
        adicional_id=adicional_id,
        fechamento_id=fechamento_id,
        active_tab=active_tab,
        gestores_disponiveis=gestores_disponiveis,
        usuarios_disponiveis=usuarios_disponiveis,
    )


@app.route("/mobile/rd/<rd_id>")
@login_required
def mobile_gerenciar_anexos(rd_id):
    if session.get("user_role") != "funcionario":
        return redirect(url_for("index"))

    user_id_logado = session["user_id"]
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Busca a RD e confirma que pertence a este funcionário
    cursor.execute(
        "SELECT * FROM rd WHERE id = %s AND funcionario_id = %s",
        (rd_id, user_id_logado),
    )
    rd = cursor.fetchone()

    if not rd:
        flash("RD não encontrada ou não pertence a você.", "error")
        return redirect(url_for("mobile_dashboard"))

    # Carrega a lista de anexos (que está guardada como JSON)
    anexos_list = []
    if rd["arquivos"]:
        try:
            anexos_list = json.loads(rd["arquivos"])
        except json.JSONDecodeError:
            anexos_list = []  # Trata o caso de JSON mal formatado

    conn.close()

    # Lembre-se de criar este template!
    return render_template(
        "mobile_gerenciar_anexos.html", rd=rd, anexos_list=anexos_list
    )


@app.route("/mobile/upload_anexo/<rd_id>", methods=["POST"])
@login_required
def mobile_upload_anexo(rd_id):
    if session.get("user_role") != "funcionario":
        return redirect(url_for("index"))

    user_id_logado = session["user_id"]
    empresa_id_logada = session["empresa_id"] # <-- NECESSÁRIO PARA O NOME DO ARQUIVO
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    cursor.execute(
        "SELECT * FROM rd WHERE id = %s AND funcionario_id = %s",
        (rd_id, user_id_logado),
    )
    rd = cursor.fetchone()

    if not rd:
        flash("RD não encontrada.", "error")
        conn.close()
        return redirect(url_for("mobile_dashboard"))

    # Carrega a lista de anexos existentes
    anexos_list = []
    if rd["arquivos"]:
        try:
            anexos_list = json.loads(rd["arquivos"])
        except json.JSONDecodeError:
            anexos_list = []

    # Processa os novos ficheiros
    files = request.files.getlist("arquivo")
    if not files or files[0].filename == "":
        flash("Nenhum ficheiro selecionado.", "warning")
        conn.close()
        return redirect(url_for("mobile_gerenciar_anexos", rd_id=rd_id))

    for file in files:
        # CORREÇÃO 2: Chama a função allowed_file() que acabamos de criar
        if file and allowed_file(file.filename):
            # CORREÇÃO 3: Usa o padrão de nome de arquivo do R2 (igual ao 'add_rd')
            filename = f"emp{empresa_id_logada}_{rd_id}_{secure_filename(file.filename)}"
            
            # CORREÇÃO 4: Salva no R2 em vez de na pasta local
            try:
                upload_file_to_r2(file, filename)
                anexos_list.append(filename)  # Adiciona o novo ficheiro à lista
            except Exception as e:
                logging.error(f"Falha ao enviar arquivo para R2 (mobile): {e}")
                flash(f"Erro ao salvar arquivo: {file.filename}", "error")
        else:
            flash(f"Tipo de ficheiro não permitido: {file.filename}", "warning")

    # Atualiza o banco de dados com a nova lista de anexos
    cursor.execute(
        "UPDATE rd SET arquivos = %s WHERE id = %s AND empresa_id = %s", 
        (json.dumps(anexos_list), rd_id, empresa_id_logada) # <-- Segurança extra
    )
    conn.commit()
    conn.close()

    flash("Anexo(s) enviado(s) com sucesso!", "success")
    return redirect(url_for("mobile_gerenciar_anexos", rd_id=rd_id))

@app.route("/mobile/delete_anexo/<rd_id>", methods=["POST"])
@login_required
def mobile_delete_anexo(rd_id):
    if session.get("user_role") != "funcionario":
        return redirect(url_for("index"))

    filename_to_delete = request.form["filename"]
    user_id_logado = session["user_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    cursor.execute(
        "SELECT * FROM rd WHERE id = %s AND funcionario_id = %s",
        (rd_id, user_id_logado),
    )
    rd = cursor.fetchone()

    if not rd or not rd["arquivos"]:
        flash("RD não encontrada ou sem anexos.", "error")
        conn.close()
        return redirect(url_for("mobile_dashboard"))

    # Carrega, modifica e salva a lista de anexos
    try:
        anexos_list = json.loads(rd["arquivos"])
        if filename_to_delete in anexos_list:
            anexos_list.remove(filename_to_delete)

            # Atualiza o DB
            cursor.execute(
                "UPDATE rd SET arquivos = %s WHERE id = %s",
                (json.dumps(anexos_list), rd_id),
            )
            conn.commit()

            # Apaga o ficheiro físico
            try:
                os.remove(os.path.join(app.config["UPLOAD_FOLDER"], filename_to_delete))
            except OSError as e:
                print(f"Erro ao apagar ficheiro físico: {e}")

            flash(f"Anexo '{filename_to_delete}' removido.", "success")
        else:
            flash("Anexo não encontrado na lista.", "warning")

    except json.JSONDecodeError:
        flash("Erro ao processar a lista de anexos.", "error")

    conn.close()
    return redirect(url_for("mobile_gerenciar_anexos", rd_id=rd_id))


@app.route("/get_despesa_itens/<rd_id>", methods=["GET"])
@login_required
def get_despesa_itens(rd_id):
    """Busca todos os itens de despesa para uma RD."""
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute(
        "SELECT * FROM despesa_itens WHERE rd_id = %s AND empresa_id = %s ORDER BY categoria, data_lancamento",
        (rd_id, session["empresa_id"]),
    )
    itens = cursor.fetchall()
    conn.close()
    # Converte para JSON, tratando Decimais
    return jsonify([dict(row) for row in itens], default=decimal_default)


@app.route("/analisar_despesa", methods=["POST"])
@login_required
def analisar_despesa():
    # ... (validação de 'model' e 'arquivo' continua a mesma) ...
    if not model:
        return jsonify({"error": "API de IA não configurada."}), 500
    if "arquivo" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files["arquivo"]
    if file.filename == "":
        return jsonify({"error": "Nenhum arquivo selecionado"}), 400

    # PEGA OS DADOS DO FORMULÁRIO
    rd_id = request.form.get("rd_id")
    use_ia = (
        request.form.get("use_ia", "true") == "true"
    )  # Pega a decisão do utilizador
    empresa_id = session["empresa_id"]

    if not rd_id:
        return jsonify({"error": "RD ID não fornecido"}), 400

    categoria = "OUTROS"  # Padrão

    try:
        if use_ia:
            # --- RODA A IA ---
            file_bytes = file.read()
            mime_type = (
                mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
            )
            blob = {"mime_type": mime_type, "data": file_bytes}
            prompt_texto = f"""
            Analise este recibo. Classifique-o em UMA das categorias:
            {', '.join(LISTA_CATEGORIAS_DESPESA)}
            Responda APENAS com o nome da categoria. 
            Se não tiver certeza, responda "OUTROS".
            """
            response = model.generate_content([prompt_texto, blob])
            cat_ia = response.text.strip().upper()
            if cat_ia in LISTA_CATEGORIAS_DESPESA:
                categoria = cat_ia
            # --------------------

        # --- UPLOAD PARA R2 (SEMPRE ACONTECE) ---
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        anexo_filename = (
            f"emp{empresa_id}_rd{rd_id}_{timestamp}_{secure_filename(file.filename)}"
        )

        file.seek(0)
        upload_file_to_r2(file, anexo_filename)
        anexo_url = get_r2_public_url(anexo_filename)

        # --- INSERE NO BD (SEMPRE ACONTECE) ---
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute(
            """
            INSERT INTO despesa_itens (rd_id, empresa_id, categoria, valor, anexo_filename, anexo_url)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, categoria, valor, anexo_url, anexo_filename
            """,
            (rd_id, empresa_id, categoria, 0, anexo_filename, anexo_url),
        )
        novo_item = cursor.fetchone()
        conn.commit()
        conn.close()

        # Retorna o item completo para o front-end
        return jsonify(
            {
                "id": novo_item["id"],
                "categoria": novo_item["categoria"],  # Nome corrigido
                "valor": 0,
                "anexo_url": novo_item["anexo_url"],
                "anexo_filename": novo_item["anexo_filename"],
            },
            default=decimal_default,
        )  # Adiciona o default=decimal_default

    except Exception as e:
        logging.error(f"Erro na análise de despesa: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/delete_despesa_item/<int:item_id>", methods=["POST"])
@login_required
def delete_despesa_item(item_id):
    """Exclui um item de despesa e seu anexo."""
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # 1. Pega o nome do anexo ANTES de apagar
    cursor.execute(
        "SELECT anexo_filename FROM despesa_itens WHERE id = %s AND empresa_id = %s",
        (item_id, session["empresa_id"]),
    )
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item não encontrado"}), 404

    # 2. Apaga o item do R2
    if item["anexo_filename"]:
        try:
            delete_file_from_r2(item["anexo_filename"])
        except Exception as e:
            logging.warning(f"Falha ao deletar {item['anexo_filename']} do R2: {e}")

    # 3. Apaga o item do BD
    cursor.execute(
        "DELETE FROM despesa_itens WHERE id = %s AND empresa_id = %s",
        (item_id, session["empresa_id"]),
    )
    conn.commit()
    conn.close()

    return jsonify({"success": True})


@app.route("/mobile/dashboard")
@login_required
def mobile_dashboard():
    # Garante que só o funcionário acede aqui
    if session.get("user_role") != "funcionario":
        return redirect(url_for("index"))

    user_id_logado = session["user_id"]
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Busca RDs que precisam de ação do funcionário:
    # 1. 'Liberado' (precisa de anexar notas)
    # 2. 'Fechamento Recusado' (precisa de corrigir anexos)
    cursor.execute(
        "SELECT * FROM rd WHERE funcionario_id = %s AND empresa_id = %s AND status != 'Fechado' ORDER BY data DESC",
        (user_id_logado, empresa_id_logada),
    )
    rds_abertas = cursor.fetchall()
    conn.close()

    # Lembre-se de criar este template!
    return render_template("mobile_funcionario.html", rds_abertas=rds_abertas)


@app.route("/add", methods=["POST"])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado", 403

    try:
        empresa_id_logada = session["empresa_id"]
    except KeyError:
        flash("Erro de sessão. Faça login novamente.")
        return redirect(url_for("logout"))

    # ==================================================
    # CAMPOS ATUALIZADOS (HÍBRIDO: TEXTO + ID)
    # ==================================================
    solicitante = request.form["solicitante"].strip()  # <-- MUDANÇA: Lendo o TEXTO
    funcionario_id_selecionado = request.form.get(
        "funcionario_id"
    )  # <-- Pegando o ID do funcionário
    gestor_id = request.form.get("gestor_aprovador_id")
    # ==================================================

    data_str = request.form["data"].strip()
    centro_custo = request.form["centro_custo"].strip()
    observacao = request.form.get("observacao", "").strip()
    rd_tipo = request.form.get("tipo", "credito alelo").strip()
    unidade_negocio = request.form.get("unidade_negocio", "").strip()

    # Validação dos campos obrigatórios
    if not solicitante or not funcionario_id_selecionado or not gestor_id:
        flash("Erro: Solicitante, Funcionário e Gestor Aprovador são obrigatórios.")
        return redirect(url_for("index"))

    try:
        valor = Decimal(request.form["valor"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor inválido.")
        return redirect(url_for("index"))

    custom_id = generate_custom_id()
    data_atual = datetime.now().strftime("%Y-%m-%d")
    arquivos = []
    if "arquivo" in request.files:
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"emp{empresa_id_logada}_{custom_id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arquivos.append(fname)
    arquivos_str = ",".join(arquivos) if arquivos else None

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # ==================================================
    # BUSCAR O NOME (username) SOMENTE DO FUNCIONÁRIO
    # ==================================================
    try:
        # Busca nome do Funcionário
        cursor.execute(
            "SELECT username FROM usuarios WHERE id = %s AND empresa_id = %s",
            (funcionario_id_selecionado, empresa_id_logada),
        )
        funcionario_row = cursor.fetchone()
        if not funcionario_row:
            raise Exception("Funcionário não encontrado")
        funcionario_nome = funcionario_row["username"]

    except Exception as e:
        conn.rollback()
        logging.error(f"Erro ao buscar nome do funcionário: {e}")
        flash(f"Erro ao buscar dados do funcionário selecionado: {e}")
        conn.close()
        return redirect(url_for("index"))
    # ==================================================

    try:
        # Nota: A coluna solicitante_id não está sendo usada aqui, o que é OK.
        cursor.execute(
            """
        INSERT INTO rd (
          id, solicitante, funcionario, data, centro_custo,
          valor, status, arquivos, valor_liberado, observacao,
          tipo, unidade_negocio, data_credito_solicitado,
          empresa_id, gestor_aprovador_id, funcionario_id
        )
        VALUES (%s,%s,%s,%s,%s,
                %s,%s,%s,0,%s,
                %s,%s,%s,
                %s, %s, %s)
        """,
            (
                custom_id,
                solicitante,
                funcionario_nome,
                data_str,
                centro_custo,  # <-- Salva NOME do funcionário
                valor,
                "Pendente",
                arquivos_str,
                observacao,
                rd_tipo,
                unidade_negocio,
                data_atual,
                empresa_id_logada,
                gestor_id,
                funcionario_id_selecionado,
            ),
        )  # <-- Salva ID do funcionário

        detalhe_valor = f"Valor solicitado: R$ {format_currency(valor)}"
        registrar_historico(conn, custom_id, "RD Criada", detalhe_valor)

        conn.commit()
    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao inserir RD: {e}")
        flash(f"Erro ao salvar no banco de dados: {e}")
    except Exception as e:
        conn.rollback()
        logging.error(f"Erro GERAL ao inserir RD: {e}")
        flash(f"Erro geral ao salvar: {e}")
    finally:
        cursor.close()
        conn.close()

    flash("RD adicionada com sucesso.")

    active_tab = request.form.get("active_tab", "tab1")
    return redirect(url_for("index", active_tab=active_tab))


def is_mobile_device():
    """Verifica se o User-Agent do request indica um dispositivo móvel."""
    user_agent = request.headers.get("User-Agent", "").lower()
    mobile_keywords = ["android", "iphone", "ipad", "ipod", "windows phone", "mobi"]
    return any(keyword in user_agent for keyword in mobile_keywords)


# ==========================================================
# INÍCIO DA CORREÇÃO 1: Adicionar função 'allowed_file'
# ==========================================================
ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'xml'
}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ==========================================================
# 7. ROTA /historico ATUALIZADA
# ==========================================================
@app.route("/historico/<rd_id>")
def ver_historico(rd_id):
    if "user_role" not in session:
        return redirect(url_for("index"))

    try:
        empresa_id_logada = session["empresa_id"]
    except KeyError:
        flash("Erro de sessão. Faça login novamente.")
        return redirect(url_for("logout"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT * FROM rd WHERE id = %s AND empresa_id = %s", (rd_id, empresa_id_logada)
    )
    rd = cursor.fetchone()

    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT * FROM historico_acoes WHERE rd_id = %s AND empresa_id = %s ORDER BY data_acao DESC",
        (rd_id, empresa_id_logada),
    )
    historico = cursor.fetchall()

    conn.close()

    if not rd:
        flash("RD não encontrada ou não pertence à sua empresa.")
        return redirect(url_for("index"))

    return render_template(
        "historico_rd.html", rd=rd, historico=historico, format_currency=format_currency
    )


# ==========================================================
# 8. FUNÇÃO can_edit_status E ROTAS /edit... ATUALIZADAS
# ==========================================================
def can_edit_status(id):
    if "empresa_id" not in session:
        return False
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    # CORREÇÃO: Buscar 'status' E 'solicitante'
    cursor.execute(
        "SELECT status, solicitante FROM rd WHERE id=%s AND empresa_id = %s", (id, empresa_id_logada)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    
    # CORREÇÃO: Passar os dois argumentos para a função can_edit
    return can_edit(row["status"], row["solicitante"])


@app.route("/edit_form/<id>", methods=["GET"])
def edit_form(id):
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT * FROM rd WHERE id=%s AND empresa_id = %s", (id, empresa_id_logada)
    )
    rd = cursor.fetchone()
    conn.close()

    if not rd:
        flash("RD não encontrada ou não pertence à sua empresa.")
        return "RD não encontrada", 404

    # CORREÇÃO: Passar 'rd["solicitante"]' para a função
    if not can_edit(rd["status"], rd["solicitante"]):
        flash("Acesso negado. Você só pode editar suas próprias RDs pendentes.")
        return "Acesso negado", 403

    return render_template("edit_form.html", rd=rd, user_role=session.get("user_role"))


@app.route("/edit_submit/<id>", methods=["POST"])
def edit_submit(id):
    logging.debug(f"Iniciando edição da RD {id}")
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    if not can_edit_status(id):  # can_edit_status() já filtra por empresa
        logging.warning(f"Acesso negado para RD {id}")
        flash("Acesso negado ou RD não encontrada.")
        return "Acesso negado", 403

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT status, arquivos, valor_adicional, valor_liberado, valor_despesa, observacao FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        logging.error(f"RD {id} não encontrada para empresa {empresa_id_logada}")
        conn.close()
        return redirect(url_for("index"))

    (
        original_status,
        arquivos_str,
        valor_adicional_antigo,
        valor_liberado,
        valor_despesa_antigo,
        observacao_antiga,
    ) = row

    arqs_list = arquivos_str.split(",") if arquivos_str else []
    if "arquivo" in request.files:
        uploaded_files = request.files.getlist("arquivo")
        for f in uploaded_files:
            if f and f.filename:
                fname = f"emp{empresa_id_logada}_{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_list.append(fname)
                logging.debug(f"Anexo adicionado: {fname}")
    new_arqs = ",".join(arqs_list) if arqs_list else None

    if user_role() == "supervisor":
        observacao = request.form.get("observacao", "").strip()
        try:
            cursor.execute(
                """
            UPDATE rd
            SET arquivos=%s, observacao=%s
            WHERE id=%s AND empresa_id = %s
            """,
                (new_arqs, observacao, id, empresa_id_logada),
            )

            registrar_historico(
                conn,
                id,
                "RD Editada pelo Supervisor",
                "Anexos e/ou observação foram atualizados.",
            )
            conn.commit()
        except psycopg2.Error as e:
            logging.error(f"Erro no banco de dados (supervisor): {e}")
            conn.rollback()
            flash("Erro ao salvar no banco de dados.")
    else:
        solicitante = request.form.get("solicitante", "").strip()
        funcionario = request.form.get("funcionario", "").strip()
        data_str = request.form.get("data", "").strip()
        centro_custo = request.form.get("centro_custo", "").strip()
        observacao = request.form.get("observacao", "").strip()
        unidade_negocio = request.form.get("unidade_negocio", "").strip()

        if not all([solicitante, funcionario, data_str, centro_custo]):
            flash("Preencha todos os campos obrigatórios.")
            conn.close()
            return redirect(url_for("index"))

        valor_raw = request.form.get("valor", "").strip()
        valor_adicional_raw = request.form.get("valor_adicional", "").strip()
        valor_despesa_raw = request.form.get("valor_despesa", "").strip()

        try:
            valor_novo = Decimal(valor_raw.replace(",", "."))
            valor_adicional_novo = (
                Decimal(valor_adicional_raw.replace(",", "."))
                if valor_adicional_raw
                else Decimal('0.00')
            )
            valor_despesa_novo = (
                Decimal(valor_despesa_raw.replace(",", "."))
                if valor_despesa_raw
                else valor_despesa_antigo
            )
        except ValueError as e:
            logging.error(f"Erro ao converter valores: {e}")
            flash("Valor, Valor Adicional ou Valor Despesa inválido.")
            conn.close()
            return redirect(url_for("index"))

        total_cred = valor_novo + valor_adicional_novo
        saldo_devolver_novo = (
            total_cred - valor_despesa_novo if valor_despesa_novo else None
        )

        try:
            cursor.execute(
                """
            UPDATE rd
            SET solicitante=%s, funcionario=%s, data=%s, centro_custo=%s, valor=%s, valor_adicional=%s,
                valor_despesa=%s, saldo_devolver=%s, arquivos=%s, observacao=%s, unidade_negocio=%s
            WHERE id=%s AND empresa_id = %s
            """,
                (
                    solicitante,
                    funcionario,
                    data_str,
                    centro_custo,
                    valor_novo,
                    valor_adicional_novo,
                    valor_despesa_novo,
                    saldo_devolver_novo,
                    new_arqs,
                    observacao,
                    unidade_negocio,
                    id,
                    empresa_id_logada,
                ),
            )

            registrar_historico(conn, id, "RD Editada")

            if is_solicitante() and original_status == "Fechamento Recusado":
                cursor.execute(
                    "UPDATE rd SET status='Fechamento Solicitado', motivo_recusa=NULL WHERE id=%s AND empresa_id = %s",
                    (id, empresa_id_logada),
                )
                registrar_historico(
                    conn, id, "Reenviada para Fechamento", "RD corrigida após recusa."
                )

            conn.commit()
        except psycopg2.Error as e:
            logging.error(f"Erro no banco de dados (edição): {e}")
            conn.rollback()
            flash("Erro ao salvar no banco de dados.")

    conn.close()
    flash("RD atualizada com sucesso.")

    active_tab = request.form.get("active_tab", "tab1")
    return redirect(url_for("index", active_tab=active_tab))


# Não se esqueça de adicionar estes imports no topo do seu app.py (junto com os outros)
# import re
# import io
# from pdf2image import convert_from_bytes
# from decimal import Decimal
# import json
# import mimetypes
# import requests




@app.route("/analise_gastos_ia/<rd_id>")
@login_required
def analise_gastos_ia(rd_id):
    if "user_id" not in session: 
        return jsonify({"success": False, "message": "Sessão expirada."}), 401
    
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection() 
    cursor = conn.cursor(cursor_factory=DictCursor)

    try:
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s AND empresa_id=%s", (rd_id, empresa_id_logada))
        rd_row = cursor.fetchone()

        if not rd_row or not rd_row['arquivos']:
            return jsonify({"success": False, "message": "Nenhum arquivo anexado a esta RD."})

        arquivos = [f for f in rd_row['arquivos'].split(',') if f]
        resultados_analise = [] # Esta lista agora só terá arquivos válidos

        try:
            model = genai.GenerativeModel('gemini-2.0-flash')
        except Exception as e:
            logging.error(f"Falha ao carregar o modelo 'gemini-2.0-flash'. Verifique a API Key e a versão da biblioteca. Erro: {e}")
            return jsonify({"success": False, "message": f"Erro ao carregar modelo de IA: {e}"}), 500

        # ==========================================================
        # INÍCIO: PROMPT ATUALIZADO (PARA 'tipo_refeicao')
        # ==========================================================
        categorias_str = ", ".join(LISTA_CATEGORIAS_DESPESA_IA) 
        base_prompt = (
            f"Sua tarefa é analisar imagens de despesas. Você deve extrair gastos APENAS de recibos reais (cupons fiscais, notas fiscais, recibos de UBER, etc.).\n"
            
            f"--- REGRA CRÍTICA DE EXCLUSÃO ---\n"
            f"Documentos com os títulos 'PRESTAÇÃO DE CONTAS' ou 'RELATÓRIO DE DESPESAS' NÃO SÃO RECIBOS. Eles são RESUMOS.\n"
            f"Se a imagem for um desses RESUMOS, IGNORE TODOS OS VALORES nela. NÃO EXTRAIA NENHUM GASTO. Apenas retorne um objeto JSON único indicando que é um resumo:\n"
            f"{{\"tipo_documento\": \"resumo\", \"categoria\": \"Não aplicável\", \"valor\": \"0.00\", \"alerta_gasto\": \"nao\", \"tipo_refeicao\": \"N/A\"}}\n"
            f"--- FIM DA REGRA CRÍTICA ---\n\n"
            
            f"Se a imagem for um RECIBO REAL (ex: cupom de restaurante, posto de gasolina):\n"
            f"Extraia os itens e retorne um objeto JSON (ou uma LISTA de objetos) com as chaves: 'tipo_documento', 'categoria', 'valor', 'alerta_gasto', e 'tipo_refeicao'.\n"
            
            f"--- REGRA DE VALOR (MUITO IMPORTANTE) ---\n"
            f"Sempre extraia o valor final do cupom, da linha 'TOTAL' ou 'VALOR'.\n"
            f"NÃO some os preços unitários. Por exemplo, no cupom, o valor correto é '110,00' (da linha TOTAL) e não '61,00' (que é a soma errada de 12,00 + 49,00).\n"
            f"--- FIM DA REGRA DE VALOR ---\n\n"

            f"   - 'tipo_documento': 'recibo'\n"
            f"   - 'categoria': (Uma das seguintes: {categorias_str}) -> MANTENHA 'REFEIÇÕES' COMO A CATEGORIA PRINCIPAL.\n"
            f"   - 'valor': (O valor total do item, conforme a regra de valor acima)\n"
            f"   - 'alerta_gasto': ('sim' ou 'nao')\n"
            f"   - 'tipo_refeicao': (Use 'N/A' se não for refeição. Se for, use a Regra de Horário para definir 'Almoço', 'Janta' ou 'Café'.)\n"

            f"REGRAS DE ALERTA E TIPO DE REFEIÇÃO (APENAS para RECIBOS de 'REFEIÇÕES'):\n"
            f"**Esta é a regra mais importante, NÃO A ESQUEÇA:**\n"
            f"1. **REGRA DE HORÁRIO:** Extraia o HORÁRIO do recibo (ex: 13:27). Use o horário para definir o TIPO de refeição:\n"
            f"   - 'Café': Antes das 11:00\n"
            f"   - 'Almoço': Das 11:00 às 16:00\n"
            f"   - 'Janta': Após as 16:00\n"
            f"   **INSTRUÇÃO:** Preencha a chave 'tipo_refeicao' com este valor ('Almoço', 'Janta', 'Café'). MANTENHA a 'categoria' principal como 'REFEIÇÕES'.\n"
            
            f"2. **REGRA DE QUANTIDADE:** Verifique se o cupom indica múltiplas pessoas (ex: '2 pessoas' ou uma quantidade '2,000xUN' para um item de refeição). Se sim, divida o valor do item (ex: 98,00) pelo número de pessoas (ex: 2).\n"
            f"3. **COMPARAÇÃO:** Compare o valor por pessoa (ex: 49,00) com o limite correto (ex: Almoço Interior: R$ 37,00).\n"
            f"4. **RESULTADO:** Se exceder o limite para aquele TIPO e LOCAL, retorne 'alerta_gasto': 'sim'. Caso contrário, 'alerta_gasto': 'nao'.\n"
            f"5. Para todas as outras categorias (COMBUSTÍVEL, etc.), retorne 'alerta_gasto': 'nao'.\n"

            f"Exemplo de resposta (recibo único): {{\"tipo_documento\": \"recibo\", \"categoria\": \"REFEIÇÕES\", \"valor\": \"60.00\", \"alerta_gasto\": \"sim\", \"tipo_refeicao\": \"Janta\"}}\n"
            f"Exemplo de resposta (combustível): {{\"tipo_documento\": \"recibo\", \"categoria\": \"COMBUSTÍVEL\", \"valor\": \"150.00\", \"alerta_gasto\": \"nao\", \"tipo_refeicao\": \"N/A\"}}\n"
            f"Exemplo de resposta (resumo): {{\"tipo_documento\": \"resumo\", \"categoria\": \"Não aplicável\", \"valor\": \"0.00\", \"alerta_gasto\": \"nao\", \"tipo_refeicao\": \"N/A\"}}\n"
            
            f"Responda APENAS em formato JSON."
        )
        # ==========================================================
        # FIM: PROMPT ATUALIZADO
        # ==========================================================

        # Loop por cada arquivo
        for filename in arquivos:
            
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type or not mime_type.startswith('image/'):
                logging.info(f"Arquivo {filename} ignorado (tipo: {mime_type}). Não é uma imagem.")
                continue 

            categoria_final_arquivo = "Não identificado"
            valor_total_arquivo = Decimal('0.00')
            alerta_final_arquivo = "nao"
            tipo_refeicao_final_arquivo = "N/A" # NOVO: Variável para armazenar o tipo

            file_url = get_r2_public_url(filename) 

            try:
                response_file = requests.get(file_url)
                response_file.raise_for_status()
                file_content = response_file.content
                
                image_part = {
                    "mime_type": mime_type,
                    "data": file_content
                }
                prompt_parts = [base_prompt, image_part]
                
                raw_text = "" 
                try:
                    response = model.generate_content(prompt_parts)
                    response.resolve() 
                    
                    raw_text = response.text
                    cleaned_text = re.sub(r'```json\s*|\s*```', '', raw_text).strip()
                    json_compatible_text = cleaned_text.replace("'", '"')
                    parsed_data = json.loads(json_compatible_text)

                    items_to_process = []
                    if isinstance(parsed_data, list):
                        items_to_process = parsed_data 
                    elif isinstance(parsed_data, dict):
                        items_to_process.append(parsed_data) 

                    for item in items_to_process:
                        if item.get('tipo_documento') == 'resumo':
                            continue 

                        categoria_pagina = item.get('categoria', 'Não identificado')
                        valor_str = str(item.get('valor', '0.00')).replace(',', '.')
                        valor_pagina = Decimal(valor_str)
                        alerta_pagina = item.get('alerta_gasto', 'nao')
                        
                        # ==========================================================
                        # INÍCIO: Captura o tipo de refeição
                        # ==========================================================
                        tipo_refeicao_pagina = item.get('tipo_refeicao', 'N/A')
                        if tipo_refeicao_pagina != 'N/A':
                            tipo_refeicao_final_arquivo = tipo_refeicao_pagina
                        # ==========================================================
                        # FIM: Captura o tipo de refeição
                        # ==========================================================
                        
                        if alerta_pagina == 'sim':
                            alerta_final_arquivo = 'sim'

                        valor_total_arquivo += valor_pagina
                        
                        if categoria_final_arquivo == "Não identificado" and categoria_pagina != "Não identificado":
                            categoria_final_arquivo = categoria_pagina # Salva "REFEIÇÕES"

                except json.JSONDecodeError as json_err:
                    logging.error(f"Erro ao processar JSON da IA para {filename}: {json_err} - Resposta: {raw_text}")
                    categoria_final_arquivo = "IA não conseguiu ler a imagem"
                except Exception as e:
                    logging.error(f"Erro na geração de conteúdo da IA para {filename}: {e} - Resposta: {raw_text}")
                    categoria_final_arquivo = "Erro de análise da IA"
            
            except requests.exceptions.RequestException as e:
                logging.error(f"Erro ao baixar arquivo {filename} do R2: {e}")
                categoria_final_arquivo = "Erro ao acessar arquivo"
            except Exception as e:
                logging.error(f"Erro inesperado no processamento do arquivo {filename}: {e}")
                categoria_final_arquivo = "Erro inesperado"

            if valor_total_arquivo == Decimal('0.00') and categoria_final_arquivo == "Não identificado":
                categoria_final_arquivo = "Resumo (Ignorado)"

            # Adiciona o novo campo 'tipo_refeicao' ao resultado do arquivo
            resultados_analise.append({
                "filename": filename,
                "url": file_url,
                "categoria": categoria_final_arquivo,
                "valor": float(valor_total_arquivo),
                "alerta_gasto": alerta_final_arquivo,
                "tipo_refeicao": tipo_refeicao_final_arquivo # NOVO
            })
            
        # A lógica de agrupar gastos não muda, ela continua agrupando por "REFEIÇÕES"
        gastos_agrupados = {}
        for item in resultados_analise:
            if item['categoria'] == 'Resumo (Ignorado)':
                continue
                
            cat = item['categoria'] # <-- Isto ainda será "REFEIÇÕES"
            val = Decimal(str(item['valor']))
            alerta = item['alerta_gasto']
            
            if cat not in gastos_agrupados:
                gastos_agrupados[cat] = {"total": Decimal('0.00'), "alerta": "nao"}
                
            gastos_agrupados[cat]["total"] += val
            if alerta == 'sim':
                gastos_agrupados[cat]["alerta"] = "sim"

        gastos_totais_por_categoria = [
            {"categoria": cat, "total": float(data["total"]), "alerta_gasto": data["alerta"]}
            for cat, data in gastos_agrupados.items()
        ]

        # Retorna o JSON final
        return jsonify({
            "success": True,
            "analise_por_arquivo": resultados_analise,
            "gastos_totais_por_categoria": gastos_totais_por_categoria
        })

    except Exception as e:
        logging.error(f"Erro geral na rota /analise_gastos_ia: {e}")
        return jsonify({"success": False, "message": f"Erro interno do servidor: {e}"}), 500
    finally:
        cursor.close()
        conn.close()
        

@app.route("/update_despesa_item/<int:item_id>", methods=["POST"])
@login_required
def update_despesa_item(item_id):
    """Atualiza a categoria ou valor de um item."""
    data = request.json
    campo = data.get("campo")  # "categoria" ou "valor"
    valor = data.get("valor")

    if campo not in ["categoria", "valor"]:
        return jsonify({"error": "Campo inválido"}), 400

    conn = get_pg_connection()
    cursor = conn.cursor()

    if campo == "valor":
        try:
            # Converte o valor para Decimal para o PostgreSQL
            valor = Decimal(str(valor).replace(",", "."))
        except:
            return jsonify({"error": "Valor inválido"}), 400

    # Query de atualização dinâmica e segura
    query = f"UPDATE despesa_itens SET {campo} = %s WHERE id = %s AND empresa_id = %s"

    try:
        cursor.execute(query, (valor, item_id, session["empresa_id"]))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Erro ao atualizar item {item_id}: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

    return jsonify({"success": True})


# ==========================================================
# 9. ROTA /approve ATUALIZADA
# ==========================================================
@app.route("/approve/<id>", methods=["POST"])
def approve(id):
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT status, valor, valor_adicional, tipo, valor_liberado FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada ou não pertence à sua empresa.")
        return redirect(url_for("index"))
    st_atual, val, val_adic, rd_tipo, valor_liberado_anterior = row

    if not can_approve(st_atual):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    now = datetime.now().strftime("%Y-%m-%d")

    try:
        if st_atual == "Pendente" and is_gestor():
            new_st = "Aprovado"
            cursor.execute(
                """
            UPDATE rd SET status=%s, aprovado_data=%s
            WHERE id=%s AND empresa_id = %s
            """,
                (new_st, now, id, empresa_id_logada),
            )
            registrar_historico(conn, id, "Aprovada pelo Gestor")

        elif st_atual == "Aprovado" and is_financeiro():
            if rd_tipo.lower() == "reembolso":
                new_st = "Fechado"
                cursor.execute(
                    """
                UPDATE rd SET status=%s, data_fechamento=%s, valor_despesa=valor, saldo_devolver=0
                WHERE id=%s AND empresa_id = %s
                """,
                    (new_st, now, id, empresa_id_logada),
                )
                registrar_historico(conn, id, "Reembolso Aprovado e Fechado")
            else:
                new_st = "Liberado"
                total_credit = val + (val_adic or 0)
                novo_credito = total_credit - (valor_liberado_anterior or 0)

                # Chamadas de saldo ATUALIZADAS
                saldo_atual = get_saldo_global(empresa_id_logada)
                novo_saldo = saldo_atual - Decimal(novo_credito)
                set_saldo_global(novo_saldo, empresa_id_logada)

                cursor.execute(
                    """
                UPDATE rd SET status=%s, liberado_data=%s, valor_liberado=%s, data_credito_liberado=%s
                WHERE id=%s AND empresa_id = %s
                """,
                    (new_st, now, total_credit, now, id, empresa_id_logada),
                )
                detalhe_liberado = f"Valor liberado: R$ {format_currency(total_credit)}"
                registrar_historico(
                    conn, id, "Crédito Liberado pelo Financeiro", detalhe_liberado
                )

        elif st_atual == "Fechamento Solicitado" and is_gestor():
            new_st = "Saldos a Devolver"
            cursor.execute(
                """
            UPDATE rd SET status=%s, data_fechamento=%s
            WHERE id=%s AND empresa_id = %s
            """,
                (new_st, now, id, empresa_id_logada),
            )
            registrar_historico(conn, id, "Fechamento Aprovado pelo Gestor")
        else:
            flash("Não é possível aprovar/liberar esta RD.")

        conn.commit()
        flash("Operação realizada com sucesso.")

    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao aprovar RD {id}: {e}")
        flash(f"Erro no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab1")
    return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# 10. ROTA /delete ATUALIZADA
# ==========================================================
@app.route("/delete/<id>", methods=["POST"])
def delete_rd(id):
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT solicitante, status, valor_liberado, valor, arquivos FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada ou não pertence à sua empresa.")
        return redirect(url_for("index"))
    rd_solic, rd_status, rd_liber, rd_valor, arq_str = row

    if not can_delete(rd_status, rd_solic):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    try:
        # Registrar no histórico DEVE vir antes da exclusão
        registrar_historico(conn, id, "RD Excluída")

        usuario_excluiu = session.get("user_role", "desconhecido")
        data_exclusao = datetime.now().strftime("%Y-%m-%d")

        # Adiciona empresa_id ao histórico de exclusão
        cursor.execute(
            """
        INSERT INTO historico_exclusao (rd_id, solicitante, valor, data_exclusao, usuario_excluiu, empresa_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
            (id, rd_solic, rd_valor, data_exclusao, usuario_excluiu, empresa_id_logada),
        )

        # Devolver saldo se aplicável
        if rd_status == "Liberado" and rd_liber and rd_liber > 0:
            # Chamadas de saldo ATUALIZADAS
            saldo = get_saldo_global(empresa_id_logada)
            set_saldo_global(saldo + rd_liber, empresa_id_logada)

        # Excluir arquivos do R2
        if arq_str:
            for a in arq_str.split(","):
                delete_file_from_r2(a)

        # Excluir RD
        cursor.execute(
            "DELETE FROM rd WHERE id=%s AND empresa_id = %s", (id, empresa_id_logada)
        )

        conn.commit()
        flash("RD excluída com sucesso.")

    except psycopg2.Error as e:
        conn.rollback()
        flash("Erro ao acessar banco de dados ao registrar histórico.")
        logging.error(f"Erro ao excluir RD: {e}")
    except Exception as e:
        conn.rollback()
        logging.error(f"Erro inesperado ao excluir RD: {e}")
        flash("Erro inesperado.")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab1")
    return redirect(url_for("index", active_tab=active_tab))


@app.route("/adicional_submit/<id>", methods=["POST"])
def adicional_submit(id):
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT arquivos, status, valor_adicional, adicionais_individuais, valor, valor_despesa FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada ou não pertence à sua empresa.")
        return redirect(url_for("index"))

    arquivos_str, st_atual, val_adic_atual, add_ind, val_sol, val_desp = row

    if not can_request_additional(st_atual):
        conn.close()
        flash("Não é possível solicitar adicional agora.")
        return redirect(url_for("index"))

    # Lógica de Arquivos
    arqs_atual = arquivos_str.split(",") if arquivos_str else []
    if "arquivo" in request.files:
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"emp{empresa_id_logada}_{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_atual.append(fname)
    new_arqs_str = ",".join(arqs_atual) if arqs_atual else None

    try:
        # CORREÇÃO 1: O valor do formulário deve ser 'Decimal', não 'float'.
        val_adi = Decimal(request.form["valor_adicional"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor adicional inválido.")
        conn.close()
        return redirect(url_for("index"))

    # CORREÇÃO 1: O valor de 'fallback' (ou 0) também deve ser 'Decimal'.
    novo_total = (val_adic_atual or Decimal("0.00")) + val_adi

    # CORREÇÃO 2: Pega a data atual para registrar no histórico individual
    data_add = datetime.now().strftime("%Y-%m-%d")

    if add_ind:
        partes = [x.strip() for x in add_ind.split(",")]
        idx = len(partes) + 1
        # CORREÇÃO 2: Salva a data junto com o valor (Valor:Data)
        add_ind = add_ind + f", Adicional {idx}:{val_adi:.2f}:{data_add}"
    else:
        # CORREÇÃO 2: Salva a data junto com o valor (Valor:Data)
        add_ind = f"Adicional 1:{val_adi:.2f}:{data_add}"

    total_cred = val_sol + novo_total

    # CORREÇÃO 1: Garante que 'val_desp' também seja Decimal para o cálculo
    saldo_dev = total_cred - (val_desp or Decimal("0.00"))

    # A linha 'data_add' original é usada para 'adicional_data',
    # mas também a usamos acima para o 'adicionais_individuais'.

    try:
        cursor.execute(
            """
        UPDATE rd
        SET valor_adicional=%s, adicional_data=%s, status='Pendente', 
            adicionais_individuais=%s, saldo_devolver=%s, arquivos=%s
        WHERE id=%s AND empresa_id = %s
        """,
            (
                novo_total,
                data_add,
                add_ind,
                saldo_dev,
                new_arqs_str,
                id,
                empresa_id_logada,
            ),
        )

        detalhe_adicional = f"Valor adicional solicitado: R$ {format_currency(val_adi)}"
        registrar_historico(
            conn, id, "Solicitação de Crédito Adicional", detalhe_adicional
        )

        conn.commit()
        flash("Crédito adicional solicitado. A RD voltou para 'Pendente'.")

    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao salvar adicional: {e}")
        flash(f"Erro no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab3")
    return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# 12. ROTA /fechamento_submit ATUALIZADA
# ==========================================================
@app.route("/fechamento_submit/<id>", methods=["POST"])
def fechamento_submit(id):
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # 1. Pega o status da RD e arquivos existentes
    cursor.execute(
        "SELECT valor, valor_adicional, status, arquivos FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))

    val_sol, val_adic, st_atual, arquivos_str = row

    if not can_close(st_atual):
        conn.close()
        flash("Não é possível fechar esta RD agora.")
        return redirect(url_for("index"))

    # 2. PEGA O VALOR DO FORMULÁRIO (COMO NO index.html)
    try:
        val_desp = Decimal(request.form["valor_despesa"].replace(",", "."))
    except (ValueError, TypeError):
        conn.close()
        flash("Valor da despesa inválido.")
        return redirect(url_for("index", active_tab="tab3"))

    if val_desp <= 0:
        conn.close()
        flash("Valor da despesa deve ser maior que zero.")
        return redirect(url_for("index", active_tab="tab3"))

    # 3. Lógica de cálculo (baseada no valor manual)
    total_cred = val_sol + (val_adic or Decimal("0.00"))
    if total_cred < val_desp:
        conn.close()
        flash(
            "Valor da despesa (R$ %.2f) maior que o total de créditos (R$ %.2f)."
            % (val_desp, total_cred)
        )
        return redirect(url_for("index", active_tab="tab3"))

    saldo_dev = total_cred - val_desp
    data_fech = datetime.now().strftime("%Y-%m-%d")

    # 4. Lógica de Arquivos (mantida do seu código original)
    arqs_atual = arquivos_str.split(",") if arquivos_str else []
    if "arquivo" in request.files:
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"emp{empresa_id_logada}_{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_atual.append(fname)
    new_arqs_str = ",".join(arqs_atual) if arqs_atual else None

    try:
        # 5. ATUALIZA A RD com o valor manual e novos arquivos
        cursor.execute(
            """
        UPDATE rd
        SET valor_despesa=%s, saldo_devolver=%s, data_fechamento=%s,
            status='Fechamento Solicitado', data_debito_despesa=%s,
            arquivos=%s
        WHERE id=%s AND empresa_id = %s
        """,
            (
                val_desp,
                saldo_dev,
                data_fech,
                data_fech,
                new_arqs_str,
                id,
                empresa_id_logada,
            ),
        )

        detalhe_gasto = (
            f"Valor gasto (informado manualmente): R$ {format_currency(val_desp)}"
        )
        registrar_historico(conn, id, "Solicitação de Fechamento", detalhe_gasto)

        conn.commit()
        flash("Fechamento solicitado. Aguarde aprovação do gestor.")

    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao salvar fechamento: {e}")
        flash(f"Erro no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab3")
    return redirect(url_for("index", active_tab=active_tab))


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def get_date_range(req_args):
    data_fim_dt = datetime.now()
    data_inicio_dt = data_fim_dt - timedelta(days=30)
    data_inicio = req_args.get("data_inicio")
    data_fim = req_args.get("data_fim")
    try:
        if data_inicio:
            data_inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
        if data_fim:
            data_fim_dt = datetime.strptime(data_fim, "%Y-%m-%d")
    except ValueError:
        pass
    return (data_inicio_dt.strftime("%Y-%m-%d"), data_fim_dt.strftime("%Y-%m-%d"))


# ==========================================================
# 13. ROTA /dashboard ATUALIZADA
# ==========================================================
@app.route("/dashboard")
def dashboard():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    try:
        empresa_id_logada = session["empresa_id"]
    except KeyError:
        flash("Erro de sessão. Faça login novamente.")
        return redirect(url_for("logout"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    data_inicio, data_fim = get_date_range(request.args)

    # Parâmetros para queries com filtro de data
    params_data = (data_inicio, data_fim, empresa_id_logada)
    # Parâmetros para queries sem filtro de data
    params_empresa = (empresa_id_logada,)

    # KPI 1: Total Gasto no Período
    cursor.execute(
        """
        SELECT SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE data_fechamento BETWEEN %s AND %s
        AND empresa_id = %s
    """,
        params_data,
    )
    kpi_gasto_total = cursor.fetchone()["total_gasto"] or 0

    # KPI 2: Valor Pendente de Aprovação (Gestor)
    cursor.execute(
        """
        SELECT SUM(valor) as valor_pendente
        FROM rd
        WHERE status = 'Pendente'
        AND empresa_id = %s
    """,
        params_empresa,
    )
    kpi_valor_pendente = cursor.fetchone()["valor_pendente"] or 0

    # KPI 3: RDs Aguardando Devolução (Financeiro)
    cursor.execute(
        """
        SELECT COUNT(id) as count_saldos
        FROM rd
        WHERE status = 'Saldos a Devolver'
        AND empresa_id = %s
    """,
        params_empresa,
    )
    kpi_saldos_devolver = cursor.fetchone()["count_saldos"] or 0

    # KPI 4: Tempo Médio de Aprovação
    cursor.execute(
        """
        SELECT 
            AVG(liberado_data - data_credito_solicitado) as tempo_medio
        FROM rd
        WHERE data_credito_solicitado IS NOT NULL
          AND liberado_data IS NOT NULL
          AND liberado_data BETWEEN %s AND %s
          AND empresa_id = %s
    """,
        params_data,
    )
    kpi_tempo_medio_result = cursor.fetchone()["tempo_medio"]
    kpi_tempo_medio = round(float(kpi_tempo_medio_result or 0), 1)

    # Gráfico 1: Evolução de Gastos Mensais
    cursor.execute(
        """
        SELECT 
            to_char(date_trunc('month', data_fechamento), 'YYYY-MM') as mes_ano,
            SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE data_fechamento IS NOT NULL
        AND empresa_id = %s
        GROUP BY 1
        ORDER BY 1 ASC
    """,
        params_empresa,
    )
    evolucao_mensal = cursor.fetchall()

    # Gráfico 2: Gasto por Centro de Custo (no período)
    cursor.execute(
        """
        SELECT centro_custo, SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE status IN ('Fechado', 'Saldos a Devolver') 
          AND valor_despesa IS NOT NULL
          AND data_fechamento BETWEEN %s AND %s
          AND empresa_id = %s
        GROUP BY centro_custo
        HAVING SUM(valor_despesa) > 0
        ORDER BY total_gasto DESC
    """,
        params_data,
    )
    gasto_por_cc = cursor.fetchall()

    # Gráfico 3: Distribuição de RDs por Status (Geral)
    cursor.execute(
        """
        SELECT status, COUNT(id) as total_rds
        FROM rd
        WHERE empresa_id = %s
        GROUP BY status
        ORDER BY total_rds DESC
    """,
        params_empresa,
    )
    status_dist = cursor.fetchall()

    # Gráfico 4: Top 5 Solicitantes por Valor Gasto (no período)
    cursor.execute(
        """
        SELECT solicitante, SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE status IN ('Fechado', 'Saldos a Devolver') 
          AND valor_despesa IS NOT NULL
          AND data_fechamento BETWEEN %s AND %s
          AND empresa_id = %s
        GROUP BY solicitante
        HAVING SUM(valor_despesa) > 0
        ORDER BY total_gasto DESC
        LIMIT 5
    """,
        params_data,
    )
    top_solicitantes = cursor.fetchall()

    # Top 5 RDs Pendentes mais Antigas
    cursor.execute(
        """
        SELECT id, solicitante, data, valor
        FROM rd
        WHERE status = 'Pendente'
        AND empresa_id = %s
        ORDER BY data ASC
        LIMIT 5
    """,
        params_empresa,
    )
    pendentes_antigas = cursor.fetchall()

    conn.close()

    chart_data = {
        "kpis": {
            "gasto_total": kpi_gasto_total,
            "valor_pendente": kpi_valor_pendente,
            "saldos_devolver": kpi_saldos_devolver,
            "tempo_medio": kpi_tempo_medio,
        },
        "evolucao_mensal": {
            "labels": [row["mes_ano"] for row in evolucao_mensal],
            "data": [row["total_gasto"] for row in evolucao_mensal],
        },
        "gasto_por_cc": {
            "labels": [row["centro_custo"] for row in gasto_por_cc],
            "data": [row["total_gasto"] for row in gasto_por_cc],
        },
        "status_dist": {
            "labels": [row["status"] for row in status_dist],
            "data": [row["total_rds"] for row in status_dist],
        },
        "top_solicitantes": {
            "labels": [row["solicitante"] for row in top_solicitantes],
            "data": [row["total_gasto"] for row in top_solicitantes],
        },
    }
    chart_data_json = json.dumps(chart_data, default=decimal_default)

    return render_template(
        "dashboard.html",
        user_role=session.get("user_role"),
        # Chamada de saldo ATUALIZADA
        saldo_global=get_saldo_global(empresa_id_logada) if is_financeiro() else None,
        chart_data_json=chart_data_json,
        pendentes_antigas=pendentes_antigas,
        filtro_data_inicio=data_inicio,
        filtro_data_fim=data_fim,
    )


# ==========================================================
# 14. ROTA /reject_fechamento ATUALIZADA
# ==========================================================
@app.route("/reject_fechamento/<id>", methods=["POST"])
def reject_fechamento(id):
    if not is_gestor():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT status FROM rd WHERE id=%s AND empresa_id = %s", (id, empresa_id_logada)
    )
    row = cursor.fetchone()
    if not row or row[0] != "Fechamento Solicitado":
        conn.close()
        flash("Ação não permitida ou RD não encontrada.")
        return redirect(url_for("index"))

    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        flash("Informe um motivo para a recusa.")
        conn.close()
        return redirect(url_for("index"))

    try:
        cursor.execute(
            """
        UPDATE rd
        SET status='Fechamento Recusado', motivo_recusa=%s
        WHERE id=%s AND empresa_id = %s
        """,
            (motivo, id, empresa_id_logada),
        )

        detalhe_motivo = f"Motivo: {motivo}"
        registrar_historico(conn, id, "Fechamento Recusado pelo Gestor", detalhe_motivo)

        conn.commit()
        flash("Fechamento recusado com sucesso.")
    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao recusar fechamento: {e}")
        flash(f"Erro no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab4")
    return redirect(url_for("index", active_tab=active_tab))


@app.route("/reenviar_fechamento/<id>", methods=["POST"])
def reenviar_fechamento(id):
    flash("Utilize o botão 'Corrigir e reenviar' para editar a RD.")
    return redirect(url_for("index"))


# ==========================================================
# 15. ROTA /edit_saldo ATUALIZADA
# ==========================================================
@app.route("/edit_saldo", methods=["POST"])
def edit_saldo():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    try:
        novo_saldo = Decimal(request.form["saldo_global"].replace(",", "."))
    except:
        flash("Saldo inválido.")
        return redirect(url_for("index"))

    # Chamada de saldo ATUALIZADA
    set_saldo_global(novo_saldo, empresa_id_logada)
    flash("Saldo Global atualizado com sucesso.")

    active_tab = request.form.get("active_tab", "tab1")
    return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# 16. ROTA /delete_file ATUALIZADA
# ==========================================================
@app.route("/delete_file/<id>", methods=["POST"])
def delete_file(id):
    filename = request.form.get("filename")
    if not filename:
        flash("Nenhum arquivo para excluir.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT arquivos, status, solicitante FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada ou não pertence à sua empresa.")
        return redirect(url_for("index"))

    arquivos_str, rd_status, rd_solic = row
    if not arquivos_str:
        conn.close()
        flash("Nenhum arquivo na RD.")
        return redirect(url_for("index"))

    if not (can_edit(rd_status) or can_delete(rd_status, rd_solic)):
        conn.close()
        flash("Você não pode excluir arquivos desta RD.")
        return redirect(url_for("index"))

    arq_list = arquivos_str.split(",")
    if filename not in arq_list:
        conn.close()
        flash("Arquivo não pertence a esta RD.")
        return redirect(url_for("index"))

    try:
        delete_file_from_r2(filename)
        arq_list.remove(filename)
        new_str = ",".join(arq_list) if arq_list else None

        # Filtra por ID e EMPRESA
        cursor.execute(
            "UPDATE rd SET arquivos=%s WHERE id=%s AND empresa_id = %s",
            (new_str, id, empresa_id_logada),
        )
        conn.commit()
        flash("Arquivo excluído com sucesso.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Erro ao excluir arquivo {filename}: {e}")
        flash(f"Erro ao excluir arquivo: {e}")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab1")
    return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# 17. ROTA /registrar_saldo_devolvido ATUALIZADA
# ==========================================================
@app.route("/registrar_saldo_devolvido/<id>", methods=["POST"])
def registrar_saldo_devolvido(id):
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT valor, valor_adicional, valor_despesa, data_saldo_devolvido, status FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada ou não pertence à sua empresa.")
        return redirect(url_for("index"))

    val_sol, val_adic, val_desp, data_sal_dev, status = row
    if data_sal_dev:
        conn.close()
        flash("Saldo já registrado antes.")
        return redirect(url_for("index"))
    if status != "Saldos a Devolver":
        conn.close()
        flash("Ação permitida apenas para RDs em 'Saldos a Devolver'.")
        return redirect(url_for("index"))

    total_cred = val_sol + (val_adic or 0)
    if total_cred < (val_desp or 0):
        conn.close()
        flash("Despesa maior que o total de créditos.")
        return redirect(url_for("index"))

    saldo_dev = total_cred - (val_desp or 0)

    try:
        # Chamadas de saldo ATUALIZADAS
        saldo = get_saldo_global(empresa_id_logada)
        set_saldo_global(saldo + saldo_dev, empresa_id_logada)

        now = datetime.now().strftime("%Y-%m-%d")
        # Filtra por ID e EMPRESA
        cursor.execute(
            """
        UPDATE rd SET data_saldo_devolvido=%s, status='Fechado'
        WHERE id=%s AND empresa_id = %s
        """,
            (now, id, empresa_id_logada),
        )

        detalhe_devolvido = (
            f"Valor devolvido ao saldo global: R$ {format_currency(saldo_dev)}"
        )
        registrar_historico(
            conn, id, "Devolução de Saldo Registrada", detalhe_devolvido
        )

        conn.commit()
        flash(f"Saldo devolvido com sucesso. Valor= R${format_currency(saldo_dev)}")
    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao registrar saldo devolvido: {e}")
        flash(f"Erro no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab7")
    return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# 18. ROTA /export_excel ATUALIZADA
# ==========================================================
@app.route("/export_excel", methods=["GET"])
def export_excel():
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por EMPRESA
    cursor.execute(
        "SELECT * FROM rd WHERE empresa_id = %s ORDER BY id ASC", (empresa_id_logada,)
    )
    rd_list = cursor.fetchall()

    # Chamada de saldo ATUALIZADA
    saldo_global = get_saldo_global(empresa_id_logada)
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = wb.add_worksheet("Relatorio")

    header = [
        "Número RD",
        "Data Solicitação",
        "Solicitante",
        "Funcionário",
        "Valor Solicitado",
        "Valor Adicional",
        "Data do Adicional",
        "Centro de Custo",
        "Unidade de Negócio",
        "Valor Gasto",
        "Saldo a Devolver",
        "Data de Fechamento",
        "Status",
        "Data Crédito Solicitado",
        "Data Crédito Liberado",
        "Data Débito Despesa",
        "Pronto Para Fechamento",
        "Saldo Global",
    ]
    for col, h in enumerate(header):
        ws.write(0, col, h)

    rowi = 1
    for rd_row in rd_list:
        ws.write(rowi, 0, rd_row["id"])
        ws.write(rowi, 1, str(rd_row["data"]) if rd_row["data"] else "")
        ws.write(rowi, 2, rd_row["solicitante"])
        ws.write(rowi, 3, rd_row["funcionario"])
        ws.write(rowi, 4, float(rd_row["valor"] or 0))
        ws.write(rowi, 5, float(rd_row["valor_adicional"] or 0))
        ws.write(
            rowi, 6, str(rd_row["adicional_data"]) if rd_row["adicional_data"] else ""
        )
        ws.write(rowi, 7, rd_row["centro_custo"])
        ws.write(
            rowi, 8, rd_row["unidade_negocio"] if rd_row["unidade_negocio"] else ""
        )
        ws.write(rowi, 9, float(rd_row["valor_despesa"] or 0))
        ws.write(rowi, 10, float(rd_row["saldo_devolver"] or 0))
        ws.write(
            rowi,
            11,
            str(rd_row["data_fechamento"]) if rd_row["data_fechamento"] else "",
        )
        ws.write(rowi, 12, rd_row["status"])
        ws.write(
            rowi,
            13,
            (
                str(rd_row["data_credito_solicitado"])
                if rd_row["data_credito_solicitado"]
                else ""
            ),
        )
        ws.write(
            rowi,
            14,
            (
                str(rd_row["data_credito_liberado"])
                if rd_row["data_credito_liberado"]
                else ""
            ),
        )
        ws.write(
            rowi,
            15,
            str(rd_row["data_debito_despesa"]) if rd_row["data_debito_despesa"] else "",
        )
        ws.write(rowi, 16, "Sim" if rd_row["pronto_fechamento"] else "Não")
        ws.write(rowi, 17, float(saldo_global))
        rowi += 1

    wb.close()
    output.seek(0)
    conn.close()

    return send_file(
        output,
        as_attachment=True,
        download_name=f"Relatorio_RD_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ==========================================================
# 19. ROTA /export_historico ATUALIZADA
# ==========================================================
@app.route("/export_historico", methods=["GET"])
def export_historico():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    try:
        # Filtra por EMPRESA
        cursor.execute(
            "SELECT rd_id, solicitante, valor, data_exclusao, usuario_excluiu FROM historico_exclusao WHERE empresa_id = %s ORDER BY data_exclusao DESC",
            (empresa_id_logada,),
        )
        historico = cursor.fetchall()
    except psycopg2.Error as e:
        conn.close()
        flash("Erro ao acessar banco de dados.")
        logging.error(f"Erro ao consultar histórico: {e}")
        return redirect(url_for("index"))

    if not historico:
        conn.close()
        flash("Nenhum registro de exclusão encontrado para esta empresa.")
        return redirect(url_for("index"))

    output = io.StringIO()
    output.write("Histórico de Exclusões de RDs\n")
    output.write("=" * 50 + "\n")
    for reg in historico:
        rd_id, solic, valor, data_exc, usuario = reg
        linha = f"Data: {data_exc} | RD: {rd_id} | Solicitante: {solic} | Valor: R${format_currency(valor)} | Excluído por: {usuario}\n"
        output.write(linha)
    output.write("=" * 50 + "\n")
    output.write(f"Total de exclusões: {len(historico)}\n")

    buffer = io.BytesIO(output.getvalue().encode("utf-8"))
    buffer.seek(0)
    conn.close()

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Historico_Exclusoes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mimetype="text/plain",
    )


# ==========================================================
# 20. ROTA /historico_geral ATUALIZADA
# ==========================================================
@app.route("/historico_geral")
def historico_geral():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Filtra por EMPRESA
    query = """
    WITH ultima_acao_por_rd AS (
        SELECT DISTINCT ON (rd_id)
            rd_id,
            acao as ultima_acao,
            data_acao as data_ultima_acao,
            usuario as usuario_ultima_acao,
            detalhes as detalhes_ultima_acao
        FROM historico_acoes
        WHERE rd_id IS NOT NULL AND empresa_id = %s
        ORDER BY rd_id, data_acao DESC
    ),
    contagem_por_rd AS (
        SELECT rd_id, COUNT(*) as total_movimentacoes
        FROM historico_acoes
        WHERE rd_id IS NOT NULL AND empresa_id = %s
        GROUP BY rd_id
    )
    SELECT 
        u.rd_id,
        u.ultima_acao,
        u.data_ultima_acao,
        u.usuario_ultima_acao,
        u.detalhes_ultima_acao,
        COALESCE(c.total_movimentacoes, 0) as total_movimentacoes
    FROM ultima_acao_por_rd u
    LEFT JOIN contagem_por_rd c ON u.rd_id = c.rd_id
    ORDER BY 
        CAST(split_part(u.rd_id, '.', 1) AS BIGINT) DESC,
        CAST(split_part(u.rd_id, '.', 2) AS BIGINT) DESC
    """

    try:
        cursor.execute(query, (empresa_id_logada, empresa_id_logada))
        resumo_rds = cursor.fetchall()
    except psycopg2.Error as e:
        logging.error(f"Erro ao consultar histórico resumido: {e}")
        conn.rollback()
        resumo_rds = []

    total_rds = len(resumo_rds)

    total_acoes = 0
    try:
        # Filtra por EMPRESA
        cursor.execute(
            "SELECT COUNT(*) as total FROM historico_acoes WHERE rd_id IS NOT NULL AND empresa_id = %s",
            (empresa_id_logada,),
        )
        total_acoes_row = cursor.fetchone()
        if total_acoes_row:
            total_acoes = total_acoes_row["total"]
    except psycopg2.Error as e:
        logging.error(f"Erro ao contar ações: {e}")
        total_acoes = 0

    ultima_acao = "N/A"
    try:
        # Filtra por EMPRESA
        cursor.execute(
            "SELECT MAX(data_acao) as data_acao FROM historico_acoes WHERE rd_id IS NOT NULL AND empresa_id = %s",
            (empresa_id_logada,),
        )
        ultima_acao_row = cursor.fetchone()
        if ultima_acao_row and ultima_acao_row["data_acao"]:
            ultima_acao = ultima_acao_row["data_acao"].strftime("%d/%m/%Y %H:%M")
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar última ação: {e}")
        ultima_acao = "N/A"

    conn.close()

    return render_template(
        "historico_geral.html",
        resumo_rds=resumo_rds,
        total_rds=total_rds,
        total_acoes=total_acoes,
        ultima_acao=ultima_acao,
    )


# ==========================================================
# 21. ROTA /historico_geral_completo ATUALIZADA
# ==========================================================
@app.route("/historico_geral_completo")
def historico_geral_completo():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    filtro_rd_id = request.args.get("rd_id", "").strip()
    filtro_usuario = request.args.get("usuario", "").strip()
    filtro_acao = request.args.get("acao", "").strip()
    filtro_data_inicio = request.args.get("data_inicio", "").strip()
    filtro_data_fim = request.args.get("data_fim", "").strip()
    filtro_periodo = request.args.get("periodo", "").strip()

    # Filtra por EMPRESA como base
    query = "SELECT * FROM historico_acoes WHERE empresa_id = %s"
    params = [empresa_id_logada]

    if filtro_rd_id:
        query += " AND rd_id = %s"
        params.append(filtro_rd_id)
    if filtro_usuario:
        query += " AND usuario = %s"
        params.append(filtro_usuario)
    if filtro_acao:
        query += " AND acao = %s"
        params.append(filtro_acao)

    if filtro_periodo:
        hoje = datetime.now().date()
        if filtro_periodo == "hoje":
            data_inicio = hoje
            data_fim = hoje
        elif filtro_periodo == "7dias":
            data_inicio = hoje - timedelta(days=7)
            data_fim = hoje
        elif filtro_periodo == "30dias":
            data_inicio = hoje - timedelta(days=30)
            data_fim = hoje
        elif filtro_periodo == "90dias":
            data_inicio = hoje - timedelta(days=90)
            data_fim = hoje
        else:
            data_inicio = None
            data_fim = None

        if data_inicio and data_fim:
            query += " AND DATE(data_acao) >= %s AND DATE(data_acao) <= %s"
            params.extend([data_inicio, data_fim])
    else:
        if filtro_data_inicio:
            query += " AND DATE(data_acao) >= %s"
            params.append(filtro_data_inicio)
        if filtro_data_fim:
            query += " AND DATE(data_acao) <= %s"
            params.append(filtro_data_fim)

    query += " ORDER BY data_acao DESC"

    try:
        cursor.execute(query, params)
        historico_completo = cursor.fetchall()
    except psycopg2.Error as e:
        logging.error(f"Erro ao consultar histórico completo: {e}")
        conn.rollback()
        historico_completo = []

    total_acoes = len(historico_completo)
    usuarios_unicos_count = len(
        set(evt["usuario"] for evt in historico_completo if evt["usuario"])
    )
    rds_afetadas_count = len(
        set(evt["rd_id"] for evt in historico_completo if evt["rd_id"])
    )

    if historico_completo:
        periodo = f"{historico_completo[-1]['data_acao'].strftime('%d/%m/%Y')} a {historico_completo[0]['data_acao'].strftime('%d/%m/%Y')}"
    else:
        periodo = "Sem dados"

    try:
        # Filtra por EMPRESA
        cursor.execute(
            "SELECT DISTINCT usuario FROM historico_acoes WHERE usuario IS NOT NULL AND empresa_id = %s ORDER BY usuario",
            (empresa_id_logada,),
        )
        usuarios_disponiveis = [row["usuario"] for row in cursor.fetchall()]
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar usuários: {e}")
        conn.rollback()
        usuarios_disponiveis = []

    try:
        # Filtra por EMPRESA
        cursor.execute(
            "SELECT DISTINCT acao FROM historico_acoes WHERE acao IS NOT NULL AND empresa_id = %s ORDER BY acao",
            (empresa_id_logada,),
        )
        acoes_disponiveis = [row["acao"] for row in cursor.fetchall()]
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar ações: {e}")
        conn.rollback()
        acoes_disponiveis = []

    conn.close()

    return render_template(
        "historico_geral_completo.html",
        historico=historico_completo,
        total_acoes=total_acoes,
        usuarios_unicos=usuarios_unicos_count,
        rds_afetadas=rds_afetadas_count,
        periodo=periodo,
        filtro_rd_id=filtro_rd_id,
        filtro_usuario=filtro_usuario,
        filtro_acao=filtro_acao,
        filtro_data_inicio=filtro_data_inicio,
        filtro_data_fim=filtro_data_fim,
        filtro_periodo=filtro_periodo,
        usuarios_disponiveis=usuarios_disponiveis,
        acoes_disponiveis=acoes_disponiveis,
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("Logout realizado com sucesso.")
    return redirect(url_for("index"))


@app.route("/cadastro_funcionario", methods=["GET"])
def cadastro_funcionario():
    # Apenas renderiza o template, não precisa de filtro de empresa aqui
    return render_template("cadastro_funcionario.html")


# ==========================================================
# 22. ROTA /cadastrar_funcionario ATUALIZADA
# ==========================================================
@app.route("/cadastrar_funcionario", methods=["POST"])
def cadastrar_funcionario():
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    nome = request.form["nome"].strip()
    centro_custo = request.form["centroCusto"].strip()
    unidade_negocio = request.form["unidadeNegocio"].strip()

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    try:
        # Adiciona empresa_id ao INSERT
        cursor.execute(
            """
        INSERT INTO funcionarios (nome, centro_custo, unidade_negocio, empresa_id)
        VALUES (%s, %s, %s, %s)
        """,
            (nome, centro_custo, unidade_negocio, empresa_id_logada),
        )
        conn.commit()
        flash("Funcionário cadastrado com sucesso.")
    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao cadastrar funcionário: {e}")
        flash(f"Erro no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("cadastro_funcionario"))


# ==========================================================
# 23. ROTA /consulta_funcionario ATUALIZADA
# ==========================================================
@app.route("/consulta_funcionario", methods=["GET"])
def consulta_funcionario():
    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por EMPRESA
    cursor.execute(
        "SELECT * FROM funcionarios WHERE empresa_id = %s ORDER BY nome ASC",
        (empresa_id_logada,),
    )
    funcionarios = cursor.fetchall()
    conn.close()
    return render_template("consulta_funcionario.html", funcionarios=funcionarios)


# ==========================================================
# 24. ROTA /marcar_divergente ATUALIZADA
# ==========================================================
@app.route("/marcar_divergente/<id>", methods=["GET", "POST"])
def marcar_divergente(id):
    if "user_role" not in session or session["user_role"] not in [
        "gestor",
        "solicitante",
    ]:
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT status FROM rd WHERE id = %s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    rd = cursor.fetchone()
    if not rd:
        flash("RD não encontrada ou não pertence à sua empresa.")
        cursor.close()
        conn.close()
        return redirect(url_for("index"))

    if rd["status"] == "Fechado":
        flash("Não é possível marcar uma RD já fechada como divergente.")
        cursor.close()
        conn.close()
        return redirect(url_for("index"))

    if request.method == "GET":
        cursor.close()
        conn.close()
        return render_template("motivo_divergente.html", rd_id=id)
    else:  # POST
        motivo_div = request.form.get("motivo_divergente", "").strip()
        try:
            # Filtra por ID e EMPRESA
            cursor.execute(
                """
            UPDATE rd
            SET anexo_divergente = TRUE,
                motivo_divergente = %s
            WHERE id = %s AND empresa_id = %s
            """,
                (motivo_div, id, empresa_id_logada),
            )

            detalhe_motivo = (
                f"Motivo: {motivo_div}" if motivo_div else "Nenhum motivo informado."
            )
            registrar_historico(conn, id, "Marcada como Divergente", detalhe_motivo)

            conn.commit()
            flash("RD marcada como divergente.")
        except psycopg2.Error as e:
            conn.rollback()
            logging.error(f"Erro ao marcar divergente: {e}")
            flash(f"Erro no banco de dados: {e}")
        finally:
            cursor.close()
            conn.close()

        active_tab = request.form.get("active_tab", "tab3")
        return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# 25. ROTA /anexos_divergentes ATUALIZADA
# ==========================================================
@app.route("/anexos_divergentes", methods=["GET"])
def anexos_divergentes():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por EMPRESA
    cursor.execute(
        "SELECT * FROM rd WHERE anexo_divergente = TRUE AND empresa_id = %s ORDER BY id",
        (empresa_id_logada,),
    )
    divergentes = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        "divergentes.html", divergentes=divergentes, user_role=session.get("user_role")
    )


# ==========================================================
# 26. ROTA /corrigir_divergente ATUALIZADA
# ==========================================================
@app.route("/corrigir_divergente/<id>", methods=["GET", "POST"])
def corrigir_divergente(id):
    if "user_role" not in session or session["user_role"] != "supervisor":
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    if request.method == "GET":
        # Filtra por ID e EMPRESA
        cursor.execute(
            "SELECT * FROM rd WHERE id = %s AND empresa_id = %s",
            (id, empresa_id_logada),
        )
        rd = cursor.fetchone()
        cursor.close()
        conn.close()
        if not rd:
            flash("RD não encontrada ou não pertence à sua empresa.")
            return redirect(url_for("anexos_divergentes"))
        return render_template("corrigir_divergente.html", rd=rd)
    else:  # POST
        try:
            # Filtra por ID e EMPRESA
            cursor.execute(
                "SELECT arquivos FROM rd WHERE id = %s AND empresa_id = %s",
                (id, empresa_id_logada),
            )
            row = cursor.fetchone()
            a_list = row[0].split(",") if (row and row[0]) else []

            if "arquivo" in request.files:
                for f in request.files.getlist("arquivo"):
                    if f.filename:
                        fname = f"emp{empresa_id_logada}_{id}_{f.filename}"
                        upload_file_to_r2(f, fname)
                        a_list.append(fname)
            new_arq_str = ",".join(a_list) if a_list else None

            # Filtra por ID e EMPRESA
            cursor.execute(
                "UPDATE rd SET arquivos = %s WHERE id = %s AND empresa_id = %s",
                (new_arq_str, id, empresa_id_logada),
            )

            # Filtra por ID e EMPRESA
            cursor.execute(
                """
            UPDATE rd
            SET anexo_divergente = FALSE,
                motivo_divergente = NULL
            WHERE id = %s AND empresa_id = %s
            """,
                (id, empresa_id_logada),
            )

            registrar_historico(conn, id, "Divergência Corrigida")

            conn.commit()
            flash("Correção da divergência realizada com sucesso.")
        except psycopg2.Error as e:
            conn.rollback()
            logging.error(f"Erro ao corrigir divergente: {e}")
            flash(f"Erro no banco de dados: {e}")
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for("anexos_divergentes"))


# ==========================================================
# 27. ROTA /marcar_pronto_fechamento ATUALIZADA
# ==========================================================
@app.route("/marcar_pronto_fechamento/<id>", methods=["POST"])
def marcar_pronto_fechamento(id):
    if user_role() != "supervisor":
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if "empresa_id" not in session:
        return redirect(url_for("logout"))
    empresa_id_logada = session["empresa_id"]

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Filtra por ID e EMPRESA
    cursor.execute(
        "SELECT pronto_fechamento FROM rd WHERE id=%s AND empresa_id = %s",
        (id, empresa_id_logada),
    )
    row = cursor.fetchone()
    if not row:
        flash("RD não encontrada ou não pertence à sua empresa.")
        conn.close()
        return redirect(url_for("index"))

    novo_valor = not row["pronto_fechamento"]

    try:
        # Filtra por ID e EMPRESA
        cursor.execute(
            "UPDATE rd SET pronto_fechamento=%s WHERE id=%s AND empresa_id = %s",
            (novo_valor, id, empresa_id_logada),
        )
        conn.commit()
        if novo_valor:
            flash("RD marcada como pronta para fechamento.")
        else:
            flash("RD desmarcada como pronta para fechamento.")
    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Erro ao marcar pronto fechamento: {e}")
        flash(f"Erro no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

    active_tab = request.form.get("active_tab", "tab3")
    return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# 28. NOVAS ROTAS DE ADMINISTRAÇÃO DE USUÁRIOS
# ==========================================================
@app.route("/admin_usuarios", methods=["GET", "POST"])
def admin_usuarios():
    # Proteção: Apenas 'financeiro' pode gerenciar usuários
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    if request.method == "POST":
        # Criar novo usuário
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        role = request.form["role"].strip()
        # Pega a empresa selecionada no dropdown
        empresa_id_selecionada = request.form["empresa_id"]

        if not username or not password or not role or not empresa_id_selecionada:
            flash("Todos os campos, incluindo a empresa, são obrigatórios.")
        else:
            try:
                hash_pwd = generate_password_hash(password)
                # Insere o usuário na empresa selecionada
                cursor.execute(
                    "INSERT INTO usuarios (username, password_hash, role, empresa_id) VALUES (%s, %s, %s, %s)",
                    (username, hash_pwd, role, empresa_id_selecionada),
                )
                conn.commit()
                flash("Usuário criado com sucesso!")
            except psycopg2.IntegrityError:  # Caso o username já exista
                conn.rollback()
                flash("Erro: Nome de usuário já existe.")
            except Exception as e:
                conn.rollback()
                logging.error(f"Erro ao criar usuário: {e}")
                flash(f"Erro ao criar usuário: {e}")

    # O Financeiro vê usuários de TODAS as empresas
    cursor.execute(
        """
        SELECT u.id, u.username, u.role, e.nome as empresa_nome
        FROM usuarios u
        JOIN empresas e ON u.empresa_id = e.id
        ORDER BY e.nome, u.username
    """
    )
    todos_usuarios = cursor.fetchall()

    # O Financeiro precisa da lista de empresas para o dropdown
    cursor.execute("SELECT id, nome FROM empresas ORDER BY nome")
    empresas = cursor.fetchall()

    conn.close()

    return render_template(
        "admin_usuarios.html",
        usuarios=todos_usuarios,
        empresas=empresas,  # Passa a lista de empresas para o dropdown
        user_role=session.get("user_role"),
    )


@app.route("/admin_excluir_usuario/<int:user_id>", methods=["POST"])
def admin_excluir_usuario(user_id):
    # Proteção: Apenas 'financeiro' pode excluir
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    # Um usuário não pode excluir a si mesmo
    if user_id == session.get("user_id"):
        flash("Você não pode excluir a si mesmo.")
        return redirect(url_for("admin_usuarios"))

    conn = get_pg_connection()
    cursor = conn.cursor()
    try:
        # O Financeiro pode excluir qualquer usuário (exceto ele mesmo)
        # Removemos o filtro de 'empresa_id'
        cursor.execute("DELETE FROM usuarios WHERE id = %s", (user_id,))
        conn.commit()
        if cursor.rowcount > 0:
            flash("Usuário excluído com sucesso.")
        else:
            flash("Usuário não encontrado.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Erro ao excluir usuário: {e}")
        flash(f"Erro ao excluir usuário: {e}")

    conn.close()
    return redirect(url_for("admin_usuarios"))


if __name__ == "__main__":
    #init_db()
    app.run(debug=True)
