from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
from psycopg2.extras import DictCursor
import os
from dotenv import load_dotenv
# NOVAS IMPORTAÇÕES PARA LOGIN
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

# ============ Config. Cloudflare R2 ============
import boto3
from botocore.client import Config
import json
from decimal import Decimal
from datetime import datetime, timedelta

# Ajuste se necessário
R2_ACCESS_KEY = "97060093e2382cb9b485900551b6e470"
R2_SECRET_KEY = "f82c29e70532b18b1705ffc94aea2f62fe4c2a85a8c99ad30b6894f068582970"
R2_ENDPOINT   = "https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com"
R2_BUCKET_NAME = "meu-bucket-r2"
R2_PUBLIC_URL  = "https://pub-1e6f8559bc2b413c889fbf4860462599.r2.dev"

def get_r2_public_url(object_name):
    return f"{R2_PUBLIC_URL}/{object_name}"

def upload_file_to_r2(file_obj, object_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4")
    )
    file_obj.seek(0)
    s3.upload_fileobj(file_obj, R2_BUCKET_NAME, object_name)

def delete_file_from_r2(object_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4")
    )
    s3.delete_object(Bucket=R2_BUCKET_NAME, Key=object_name)

# ============ Config. Excel ============
import io
import xlsxwriter
import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY", "secret123")
app.secret_key = secret_key
logging.debug("SECRET_KEY carregado corretamente.")

# ============ Config. BD ============
PG_HOST = os.getenv("PG_HOST", "dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB   = os.getenv("PG_DB", "programard_db")
PG_USER = os.getenv("PG_USER", "programard_db_user")
PG_PASSWORD = os.getenv("PG_PASSWORD", "hU9wJmIfgiyCg02KFQ3a4AropKSMopXr")

# Adição do filtro personalizado para validar formato de data
import re

@app.template_filter('is_date_format')
def is_date_format(value):
    if value is None:
        return False
    if isinstance(value, str):
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        return bool(re.match(pattern, value))
    return False

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
        logging.error(f"Erro ao conectar ao PostgreSQL: {e}")
        import sys
        sys.exit(1)


# ==========================================================
# FUNÇÃO init_db ATUALIZADA
# Agora ela cria as novas tabelas e adiciona as colunas 'setor'
# se elas não existirem.
# ==========================================================
def init_db():
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Tabela RD (com 'setor')
    create_rd_table = """
    CREATE TABLE IF NOT EXISTS rd (
        id TEXT PRIMARY KEY,
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
        setor TEXT NOT NULL DEFAULT 'servicos', /* NOVO */
        pronto_fechamento BOOLEAN DEFAULT FALSE,
        anexo_divergente BOOLEAN DEFAULT FALSE,
        motivo_divergente TEXT
    );
    """
    cursor.execute(create_rd_table)

    # Tabela historico_acoes (com 'setor')
    create_historico_acoes_table = """
    CREATE TABLE IF NOT EXISTS historico_acoes (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        data_acao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        usuario TEXT NOT NULL,
        acao TEXT NOT NULL,
        detalhes TEXT,
        setor TEXT NOT NULL DEFAULT 'servicos' /* NOVO */
    );
    """
    cursor.execute(create_historico_acoes_table)

    # Tabela funcionarios (com 'setor')
    create_funcionarios_table = """
    CREATE TABLE IF NOT EXISTS funcionarios (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        centro_custo TEXT NOT NULL,
        unidade_negocio TEXT NOT NULL,
        setor TEXT NOT NULL DEFAULT 'servicos' /* NOVO */
    );
    """
    cursor.execute(create_funcionarios_table)
    
    # Tabela historico_exclusao (com 'setor')
    create_historico_table = """
    CREATE TABLE IF NOT EXISTS historico_exclusao (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        solicitante TEXT NOT NULL,
        valor NUMERIC(15,2) NOT NULL,
        data_exclusao DATE NOT NULL,
        usuario_excluiu TEXT NOT NULL,
        setor TEXT NOT NULL DEFAULT 'servicos' /* NOVO */
    );
    """
    cursor.execute(create_historico_table)

    # NOVA Tabela saldo_global_por_setor
    create_saldo_global_setor_table = """
    CREATE TABLE IF NOT EXISTS saldo_global_por_setor (
        id SERIAL PRIMARY KEY,
        setor TEXT NOT NULL UNIQUE,
        saldo NUMERIC(15,2) DEFAULT 0
    );
    """
    cursor.execute(create_saldo_global_setor_table)

    # NOVA Tabela usuarios
    create_usuarios_table = """
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        setor TEXT NOT NULL
    );
    """
    cursor.execute(create_usuarios_table)


    # Adiciona colunas se não existirem (migração segura)
    colunas_para_adicionar = {
        'rd': 'setor TEXT NOT NULL DEFAULT \'servicos\'',
        'funcionarios': 'setor TEXT NOT NULL DEFAULT \'servicos\'',
        'historico_acoes': 'setor TEXT NOT NULL DEFAULT \'servicos\'',
        'historico_exclusao': 'setor TEXT NOT NULL DEFAULT \'servicos\''
    }
    
    for tabela, definicao_coluna in colunas_para_adicionar.items():
        coluna_nome = definicao_coluna.split(' ')[0]
        cursor.execute(f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='{tabela}' AND column_name='{coluna_nome}'
        """)
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {definicao_coluna}")
            logging.info(f"Coluna '{coluna_nome}' adicionada à tabela '{tabela}'.")

    # Remove tabela antiga se existir (opcional, mas limpa)
    # cursor.execute("DROP TABLE IF EXISTS saldo_global;")

    conn.commit()
    cursor.close()
    conn.close()

# ====== Funções de lógica (Leitura da Sessão) ======
def user_role():
    return session.get('user_role')

def user_setor():
    return session.get('user_setor')

def user_username():
    return session.get('username')

def is_solicitante():
    return user_role() == "solicitante"

def is_gestor():
    return user_role() == "gestor"

def is_financeiro():
    return user_role() == "financeiro"

def is_supervisor():
    return user_role() == "supervisor"

def can_add():
    return user_role() in ["solicitante", "gestor", "financeiro"]

def can_edit(status):
    if status == "Fechado":
        return False
    if is_solicitante():
        return status in ["Pendente", "Fechamento Recusado"]
    if is_gestor() or is_financeiro() or is_supervisor():
        return True
    return False

def can_delete(status, solicitante):
    if status == "Fechado":
        return False
    if status == "Pendente" and is_solicitante():
        return True
    if (is_gestor() or is_financeiro()) and status in ["Pendente", "Aprovado", "Liberado"]:
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
    return (is_solicitante() and status == "Liberado")

def can_close(status):
    return (is_solicitante() and status == "Liberado")


# ==========================================================
# FUNÇÕES DE SALDO ATUALIZADAS
# Agora elas operam na nova tabela 'saldo_global_por_setor'
# ==========================================================
def get_saldo_global(setor):
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT saldo FROM saldo_global_por_setor WHERE setor = %s", (setor,))
    resultado = cursor.fetchone()
    conn.close()
    if resultado:
        return resultado['saldo']
    return 0 # Retorna 0 se o setor não tiver saldo

def set_saldo_global(setor, novo_saldo):
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Garante que o setor exista antes de atualizar
    cursor.execute(
        """
        INSERT INTO saldo_global_por_setor (setor, saldo)
        VALUES (%s, %s)
        ON CONFLICT (setor) DO UPDATE SET saldo = %s
        """,
        (setor, novo_saldo, novo_saldo)
    )
    conn.commit()
    conn.close()

# ==========================================================
# FUNÇÃO DE HISTÓRICO ATUALIZADA
# Agora salva o 'username' e o 'setor'
# ==========================================================
def registrar_historico(conn, rd_id, acao, detalhes=""):
    """Registra uma nova ação no histórico de uma RD."""
    try:
        usuario = session.get('username', 'Sistema') # Salva o username
        setor = session.get('user_setor', 'desconhecido') # Salva o setor
        
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO historico_acoes (rd_id, usuario, acao, detalhes, setor)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (rd_id, usuario, acao, detalhes, setor)
        )
    except psycopg2.Error as e:
        logging.error(f"Falha ao registrar histórico para RD {rd_id}: {e}")

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
    format_currency=format_currency
)

# ============ ROTAS ============

# ==========================================================
# ROTA INDEX ATUALIZADA
# Usa a tabela 'usuarios' para login e filtra tudo por 'setor'
# ==========================================================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM usuarios WHERE username = %s", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            # Login bem-sucedido, armazena dados na sessão
            session["user_id"] = user['id']
            session["username"] = user['username']
            session["user_role"] = user['role']
            session["user_setor"] = user['setor']
            flash(f"Login como {user['username']} ({user['role']}) bem-sucedido.")
            return redirect(url_for("index"))
        else:
            # Login falhou
            flash("Credenciais inválidas.")
            return render_template("index.html", error="Credenciais inválidas", format_currency=format_currency)

    if "user_role" not in session:
        return render_template("index.html", error=None, format_currency=format_currency)

    # Usuário está logado, buscar dados do SEU SETOR
    setor_do_usuario = user_setor()
    role_do_usuario = user_role()
    
    active_tab = request.args.get('active_tab', 'tab1')

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Lógica especial do SUPERVISOR (só vê liberados e divergentes do seu setor)
    if role_do_usuario == "supervisor":
        cursor.execute("SELECT * FROM rd WHERE status='Liberado' AND setor = %s", (setor_do_usuario,))
        liberados = cursor.fetchall()
        pendentes = []
        aprovados = []
        fechamento_solicitado = []
        fechamento_recusado = []
        saldos_a_devolver = []
        fechados = []
        cursor.execute("SELECT COUNT(*) FROM rd WHERE anexo_divergente=TRUE AND setor = %s", (setor_do_usuario,))
        divergentes_count = cursor.fetchone()[0]
    else:
        # Lógica para outros usuários (gestor, financeiro, solicitante)
        cursor.execute("SELECT * FROM rd WHERE status='Pendente' AND setor = %s", (setor_do_usuario,))
        pendentes = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Aprovado' AND setor = %s", (setor_do_usuario,))
        aprovados = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Liberado' AND setor = %s", (setor_do_usuario,))
        liberados = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechamento Solicitado' AND setor = %s", (setor_do_usuario,))
        fechamento_solicitado = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechamento Recusado' AND setor = %s", (setor_do_usuario,))
        fechamento_recusado = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Saldos a Devolver' AND setor = %s", (setor_do_usuario,))
        saldos_a_devolver = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechado' AND setor = %s", (setor_do_usuario,))
        fechados = cursor.fetchall()
        divergentes_count = 0 # Outros perfis não veem o contador de divergentes

    # Busca o saldo global APENAS do setor do usuário
    saldo_global_setor = get_saldo_global(setor_do_usuario)
    
    adicional_id = request.args.get("adicional")
    fechamento_id = request.args.get("fechamento")
    conn.close()

    return render_template(
        "index.html",
        error=None,
        format_currency=format_currency,
        user_role=role_do_usuario, # Passa a role para o template
        saldo_global=saldo_global_setor if is_financeiro() else None, # Passa o saldo do SETOR
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
        active_tab=active_tab
    )

def can_mark_pronto_fechamento(status):
    return user_role() == "supervisor" and status == "Liberado"


# ==========================================================
# ROTA add_rd ATUALIZADA
# Adiciona 'setor' automaticamente no INSERT
# ==========================================================
@app.route("/add", methods=["POST"])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado", 403

    # Pega o setor do usuário logado
    setor_do_usuario = user_setor()

    solicitante     = request.form["solicitante"].strip()
    funcionario     = request.form["funcionario"].strip()
    data_str        = request.form["data"].strip()
    centro_custo    = request.form["centro_custo"].strip()
    observacao      = request.form.get("observacao", "").strip()
    rd_tipo         = request.form.get("tipo", "credito alelo").strip()
    unidade_negocio = request.form.get("unidade_negocio", "").strip()

    try:
        valor = float(request.form["valor"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor inválido.")
        return redirect(url_for("index"))

    custom_id = generate_custom_id()
    data_atual = datetime.now().strftime("%Y-%m-%d")
    arquivos = []
    if "arquivo" in request.files:
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{custom_id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arquivos.append(fname)
    arquivos_str = ",".join(arquivos) if arquivos else None

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("""
    INSERT INTO rd (
      id, solicitante, funcionario, data, centro_custo,
      valor, status, arquivos, valor_liberado, observacao,
      tipo, unidade_negocio, data_credito_solicitado, setor
    )
    VALUES (%s,%s,%s,%s,%s,
            %s,%s,%s,0,%s,
            %s,%s,%s,%s)
    """, (custom_id, solicitante, funcionario, data_str, centro_custo,
          valor, "Pendente", arquivos_str, observacao, rd_tipo, 
          unidade_negocio, data_atual, setor_do_usuario)) # Adiciona o setor
    
    detalhe_valor = f"Valor solicitado: R$ {format_currency(valor)}"
    registrar_historico(conn, custom_id, "RD Criada", detalhe_valor)

    conn.commit()
    cursor.close()
    conn.close()
    flash("RD adicionada com sucesso.")
    
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA historico ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/historico/<rd_id>")
def ver_historico(rd_id):
    if "user_role" not in session:
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Garante que o usuário só veja RDs do seu setor
    cursor.execute("SELECT * FROM rd WHERE id = %s AND setor = %s", (rd_id, setor_do_usuario))
    rd = cursor.fetchone()

    if not rd:
        flash("RD não encontrada ou pertence a outro setor.")
        conn.close()
        return redirect(url_for("index"))

    cursor.execute(
        "SELECT * FROM historico_acoes WHERE rd_id = %s AND setor = %s ORDER BY data_acao DESC",
        (rd_id, setor_do_usuario)
    )
    historico = cursor.fetchall()
    
    conn.close()

    return render_template("historico_rd.html", rd=rd, historico=historico, format_currency=format_currency)

def can_edit_status(id):
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # Verifica status E setor
    cursor.execute("SELECT status FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return can_edit(row[0])

@app.route("/edit_form/<id>", methods=["GET"])
def edit_form(id):
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    rd = cursor.fetchone()
    conn.close()

    if not rd:
        flash("RD não encontrada.")
        return "RD não encontrada", 404

    if not can_edit(rd['status']):
        flash("Acesso negado.")
        return "Acesso negado", 403

    return render_template("edit_form.html", rd=rd, user_role=session.get("user_role"))

# ==========================================================
# ROTA edit_submit ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/edit_submit/<id>", methods=["POST"])
def edit_submit(id):
    logging.debug(f"Iniciando edição da RD {id}")
    setor_do_usuario = user_setor()

    if not can_edit_status(id): # can_edit_status já checa o setor
        logging.warning(f"Acesso negado para RD {id}")
        flash("Acesso negado.")
        return "Acesso negado", 403

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    cursor.execute("SELECT status, arquivos, valor_adicional, valor_liberado, valor_despesa, observacao FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        logging.error(f"RD {id} não encontrada no setor {setor_do_usuario}")
        conn.close()
        return redirect(url_for("index"))
    
    original_status, arquivos_str, valor_adicional_antigo, valor_liberado, valor_despesa_antigo, observacao_antiga = row
    
    arqs_list = arquivos_str.split(",") if arquivos_str else []
    if "arquivo" in request.files:
        uploaded_files = request.files.getlist("arquivo")
        for f in uploaded_files:
            if f and f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_list.append(fname)
    new_arqs = ",".join(arqs_list) if arqs_list else None

    if user_role() == "supervisor":
        observacao = request.form.get("observacao", "").strip()
        try:
            cursor.execute("""
            UPDATE rd
            SET arquivos=%s, observacao=%s
            WHERE id=%s AND setor = %s
            """, (new_arqs, observacao, id, setor_do_usuario))
            
            registrar_historico(conn, id, "RD Editada pelo Supervisor", "Anexos e/ou observação foram atualizados.")
            conn.commit()
        except psycopg2.Error as e:
            logging.error(f"Erro no banco de dados: {e}")
            conn.rollback()
            flash("Erro ao salvar no banco de dados.")
    else:
        # Lógica para outros perfis
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
            valor_novo = float(valor_raw.replace(",", "."))
            valor_adicional_novo = float(valor_adicional_raw.replace(",", ".")) if valor_adicional_raw else 0.0
            valor_despesa_novo = float(valor_despesa_raw.replace(",", ".")) if valor_despesa_raw else valor_despesa_antigo
        except ValueError as e:
            flash("Valor, Valor Adicional ou Valor Despesa inválido.")
            conn.close()
            return redirect(url_for("index"))

        total_cred = valor_novo + valor_adicional_novo
        saldo_devolver_novo = total_cred - valor_despesa_novo if valor_despesa_novo else None

        try:
            cursor.execute("""
            UPDATE rd
            SET solicitante=%s, funcionario=%s, data=%s, centro_custo=%s, valor=%s, valor_adicional=%s,
                valor_despesa=%s, saldo_devolver=%s, arquivos=%s, observacao=%s, unidade_negocio=%s
            WHERE id=%s AND setor = %s
            """, (solicitante, funcionario, data_str, centro_custo, valor_novo, valor_adicional_novo,
                  valor_despesa_novo, saldo_devolver_novo, new_arqs, observacao, unidade_negocio, id, setor_do_usuario))
            
            registrar_historico(conn, id, "RD Editada")

            if is_solicitante() and original_status == "Fechamento Recusado":
                cursor.execute("UPDATE rd SET status='Fechamento Solicitado', motivo_recusa=NULL WHERE id=%s AND setor = %s", (id, setor_do_usuario))
                registrar_historico(conn, id, "Reenviada para Fechamento", "RD corrigida após recusa.")

            conn.commit()
        except psycopg2.Error as e:
            logging.error(f"Erro no banco de dados: {e}")
            conn.rollback()
            flash("Erro ao salvar no banco de dados.")

    conn.close()
    flash("RD atualizada com sucesso.")
    
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA approve ATUALIZADA
# Filtra por 'setor' e usa saldo do 'setor'
# ==========================================================
@app.route("/approve/<id>", methods=["POST"])
def approve(id):
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status, valor, valor_adicional, tipo, valor_liberado FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    st_atual, val, val_adic, rd_tipo, valor_liberado_anterior = row

    if not can_approve(st_atual):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    now = datetime.now().strftime("%Y-%m-%d")

    if st_atual == "Pendente" and is_gestor():
        new_st = "Aprovado"
        cursor.execute("""
        UPDATE rd SET status=%s, aprovado_data=%s
        WHERE id=%s AND setor = %s
        """, (new_st, now, id, setor_do_usuario))
        registrar_historico(conn, id, "Aprovada pelo Gestor")

    elif st_atual == "Aprovado" and is_financeiro():
        if rd_tipo.lower() == "reembolso":
            new_st = "Fechado"
            cursor.execute("""
            UPDATE rd SET status=%s, data_fechamento=%s, valor_despesa=valor, saldo_devolver=0
            WHERE id=%s AND setor = %s
            """, (new_st, now, id, setor_do_usuario))
            registrar_historico(conn, id, "Reembolso Aprovado e Fechado")
        else:
            new_st = "Liberado"
            total_credit = val + (val_adic or 0)
            novo_credito = total_credit - (valor_liberado_anterior or 0)
            
            # ATUALIZA SALDO DO SETOR
            saldo_atual_setor = get_saldo_global(setor_do_usuario)
            novo_saldo_setor = saldo_atual_setor - novo_credito
            set_saldo_global(setor_do_usuario, novo_saldo_setor)
            
            cursor.execute("""
            UPDATE rd SET status=%s, liberado_data=%s, valor_liberado=%s, data_credito_liberado=%s
            WHERE id=%s AND setor = %s
            """, (new_st, now, total_credit, now, id, setor_do_usuario))
            detalhe_liberado = f"Valor liberado: R$ {format_currency(total_credit)}"
            registrar_historico(conn, id, "Crédito Liberado pelo Financeiro", detalhe_liberado)

    elif st_atual == "Fechamento Solicitado" and is_gestor():
        new_st = "Saldos a Devolver"
        cursor.execute("""
        UPDATE rd SET status=%s, data_fechamento=%s
        WHERE id=%s AND setor = %s
        """, (new_st, now, id, setor_do_usuario))
        registrar_historico(conn, id, "Fechamento Aprovado pelo Gestor")
    else:
        conn.close()
        flash("Não é possível aprovar/liberar esta RD.")
        return redirect(url_for("index"))

    conn.commit()
    cursor.close()
    conn.close()
    flash("Operação realizada com sucesso.")
    
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA delete_rd ATUALIZADA
# Filtra por 'setor' e usa saldo do 'setor'
# ==========================================================
@app.route("/delete/<id>", methods=["POST"])
def delete_rd(id):
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT solicitante, status, valor_liberado, valor FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    rd_solic, rd_status, rd_liber, rd_valor = row

    if not can_delete(rd_status, rd_solic):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    registrar_historico(conn, id, "RD Excluída")

    usuario_excluiu = user_username() # Salva o username
    data_exclusao = datetime.now().strftime("%Y-%m-%d")
    try:
        # Adiciona setor ao histórico de exclusão
        cursor.execute("""
        INSERT INTO historico_exclusao (rd_id, solicitante, valor, data_exclusao, usuario_excluiu, setor)
        VALUES (%s, %s, %s, %s, %s, %s)
        """, (id, rd_solic, rd_valor, data_exclusao, usuario_excluiu, setor_do_usuario))
    except psycopg2.Error as e:
        conn.close()
        flash("Erro ao acessar banco de dados ao registrar histórico.")
        logging.error(f"Erro ao registrar histórico: {e}")
        return redirect(url_for("index"))

    if rd_status == "Liberado" and rd_liber and rd_liber > 0:
        # Devolve saldo para o SETOR
        saldo_setor = get_saldo_global(setor_do_usuario)
        set_saldo_global(setor_do_usuario, saldo_setor + rd_liber)

    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,)) # Setor já checado
    arq_str = cursor.fetchone()[0]
    if arq_str:
        for a in arq_str.split(","):
            delete_file_from_r2(a)

    cursor.execute("DELETE FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD excluída com sucesso.")
    
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA adicional_submit ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/adicional_submit/<id>", methods=["POST"])
def adicional_submit(id):
    setor_do_usuario = user_setor()
    
    if "arquivo" in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
        row = cursor.fetchone()
        arqs_atual = row[0].split(",") if row and row[0] else []
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_atual.append(fname)
        new_arqs_str = ",".join(arqs_atual) if arqs_atual else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s AND setor = %s", (new_arqs_str, id, setor_do_usuario))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        val_adi = float(request.form["valor_adicional"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor adicional inválido.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status, valor_adicional, adicionais_individuais, valor, valor_despesa FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    st_atual, val_adic_atual, add_ind, val_sol, val_desp = row

    if not can_request_additional(st_atual):
        conn.close()
        flash("Não é possível solicitar adicional agora.")
        return redirect(url_for("index"))

    novo_total = (val_adic_atual or 0) + val_adi
    if add_ind:
        partes = [x.strip() for x in add_ind.split(",")]
        idx = len(partes) + 1
        add_ind = add_ind + f", Adicional {idx}:{val_adi:.2f}"
    else:
        add_ind = f"Adicional 1:{val_adi:.2f}"

    total_cred = val_sol + novo_total
    saldo_dev = total_cred - (val_desp or 0)

    data_add = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
    UPDATE rd
    SET valor_adicional=%s, adicional_data=%s, status='Pendente', adicionais_individuais=%s, saldo_devolver=%s
    WHERE id=%s AND setor = %s
    """, (novo_total, data_add, add_ind, saldo_dev, id, setor_do_usuario))
    
    detalhe_adicional = f"Valor adicional solicitado: R$ {format_currency(val_adi)}"
    registrar_historico(conn, id, "Solicitação de Crédito Adicional", detalhe_adicional)

    conn.commit()
    cursor.close()
    conn.close()
    flash("Crédito adicional solicitado. A RD voltou para 'Pendente'.")
    
    active_tab = request.form.get('active_tab', 'tab3')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA fechamento_submit ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/fechamento_submit/<id>", methods=["POST"])
def fechamento_submit(id):
    setor_do_usuario = user_setor()
    
    if "arquivo" in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
        row = cursor.fetchone()
        a_list = row[0].split(",") if row and row[0] else []
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                a_list.append(fname)
        new_str = ",".join(a_list) if a_list else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s AND setor = %s", (new_str, id, setor_do_usuario))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        val_desp = float(request.form["valor_despesa"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor da despesa inválido.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT valor, valor_adicional, status FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    val_sol, val_adic, st_atual = row

    if not can_close(st_atual):
        conn.close()
        flash("Não é possível fechar esta RD agora.")
        return redirect(url_for("index"))

    total_cred = val_sol + (val_adic or 0)
    if total_cred < val_desp:
        conn.close()
        flash("Valor da despesa maior que o total de créditos solicitados.")
        return redirect(url_for("index"))

    saldo_dev = total_cred - val_desp
    data_fech = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
    UPDATE rd
    SET valor_despesa=%s, saldo_devolver=%s, data_fechamento=%s,
        status='Fechamento Solicitado', data_debito_despesa=%s
    WHERE id=%s AND setor = %s
    """, (val_desp, saldo_dev, data_fech, data_fech, id, setor_do_usuario))

    detalhe_gasto = f"Valor gasto informado: R$ {format_currency(val_desp)}"
    registrar_historico(conn, id, "Solicitação de Fechamento", detalhe_gasto)
    
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento solicitado. Aguarde aprovação do gestor.")
    
    active_tab = request.form.get('active_tab', 'tab3')
    return redirect(url_for("index", active_tab=active_tab))

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def get_date_range(req_args):
    """Obtém o intervalo de datas do request ou usa os últimos 30 dias como padrão."""
    data_fim_dt = datetime.now()
    data_inicio_dt = data_fim_dt - timedelta(days=30)
    data_inicio = req_args.get('data_inicio')
    data_fim = req_args.get('data_fim')
    try:
        if data_inicio:
            data_inicio_dt = datetime.strptime(data_inicio, '%Y-%m-%d')
        if data_fim:
            data_fim_dt = datetime.strptime(data_fim, '%Y-%m-%d')
    except ValueError:
        pass
    return (
        data_inicio_dt.strftime('%Y-%m-%d'), 
        data_fim_dt.strftime('%Y-%m-%d')
    )

# ==========================================================
# ROTA dashboard ATUALIZADA
# Todas as queries são filtradas por 'setor'
# ==========================================================
@app.route("/dashboard")
def dashboard():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    data_inicio, data_fim = get_date_range(request.args)
    
    # Parâmetros para queries de período
    params_periodo = (data_inicio, data_fim, setor_do_usuario)
    # Parâmetros para queries de setor apenas
    params_setor = (setor_do_usuario,)
    
    # === KPIs ===
    
    # KPI 1: Total Gasto no Período
    cursor.execute("""
        SELECT SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE data_fechamento BETWEEN %s AND %s AND setor = %s
    """, params_periodo)
    kpi_gasto_total = cursor.fetchone()['total_gasto'] or 0
    
    # KPI 2: Valor Pendente de Aprovação (Gestor)
    cursor.execute("""
        SELECT SUM(valor) as valor_pendente
        FROM rd
        WHERE status = 'Pendente' AND setor = %s
    """, params_setor)
    kpi_valor_pendente = cursor.fetchone()['valor_pendente'] or 0
    
    # KPI 3: RDs Aguardando Devolução (Financeiro)
    cursor.execute("""
        SELECT COUNT(id) as count_saldos
        FROM rd
        WHERE status = 'Saldos a Devolver' AND setor = %s
    """, params_setor)
    kpi_saldos_devolver = cursor.fetchone()['count_saldos'] or 0
    
    # KPI 4: Tempo Médio de Aprovação (no período)
    cursor.execute("""
        SELECT AVG(liberado_data - data_credito_solicitado) as tempo_medio
        FROM rd
        WHERE data_credito_solicitado IS NOT NULL
          AND liberado_data IS NOT NULL
          AND liberado_data BETWEEN %s AND %s
          AND setor = %s
    """, params_periodo)
    kpi_tempo_medio_result = cursor.fetchone()['tempo_medio']
    kpi_tempo_medio = round(float(kpi_tempo_medio_result or 0), 1)
    
    # === Gráficos ===
    
    # Gráfico 1: Evolução de Gastos Mensais
    cursor.execute("""
        SELECT 
            to_char(date_trunc('month', data_fechamento), 'YYYY-MM') as mes_ano,
            SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE data_fechamento IS NOT NULL AND setor = %s
        GROUP BY 1
        ORDER BY 1 ASC
    """, params_setor)
    evolucao_mensal = cursor.fetchall()
    
    # Gráfico 2: Gasto por Centro de Custo (no período)
    cursor.execute("""
        SELECT centro_custo, SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE status IN ('Fechado', 'Saldos a Devolver') 
          AND valor_despesa IS NOT NULL
          AND data_fechamento BETWEEN %s AND %s
          AND setor = %s
        GROUP BY centro_custo
        HAVING SUM(valor_despesa) > 0
        ORDER BY total_gasto DESC
    """, params_periodo)
    gasto_por_cc = cursor.fetchall()
    
    # Gráfico 3: Distribuição de RDs por Status (Geral)
    cursor.execute("""
        SELECT status, COUNT(id) as total_rds
        FROM rd
        WHERE setor = %s
        GROUP BY status
        ORDER BY total_rds DESC
    """, params_setor)
    status_dist = cursor.fetchall()

    # Gráfico 4: Top 5 Solicitantes por Valor Gasto (no período)
    cursor.execute("""
        SELECT solicitante, SUM(valor_despesa) as total_gasto
        FROM rd
        WHERE status IN ('Fechado', 'Saldos a Devolver') 
          AND valor_despesa IS NOT NULL
          AND data_fechamento BETWEEN %s AND %s
          AND setor = %s
        GROUP BY solicitante
        HAVING SUM(valor_despesa) > 0
        ORDER BY total_gasto DESC
        LIMIT 5
    """, params_periodo)
    top_solicitantes = cursor.fetchall()
    
    # === Tabela de Ações ===
    
    # Top 5 RDs Pendentes mais Antigas (Estado Atual)
    cursor.execute("""
        SELECT id, solicitante, data, valor
        FROM rd
        WHERE status = 'Pendente' AND setor = %s
        ORDER BY data ASC
        LIMIT 5
    """, params_setor)
    pendentes_antigas = cursor.fetchall()
    
    conn.close()
    
    # Formatar dados para Chart.js
    chart_data = {
        "kpis": {
            "gasto_total": kpi_gasto_total,
            "valor_pendente": kpi_valor_pendente,
            "saldos_devolver": kpi_saldos_devolver,
            "tempo_medio": kpi_tempo_medio
        },
        "evolucao_mensal": { "labels": [row['mes_ano'] for row in evolucao_mensal], "data": [row['total_gasto'] for row in evolucao_mensal] },
        "gasto_por_cc": { "labels": [row['centro_custo'] for row in gasto_por_cc], "data": [row['total_gasto'] for row in gasto_por_cc] },
        "status_dist": { "labels": [row['status'] for row in status_dist], "data": [row['total_rds'] for row in status_dist] },
        "top_solicitantes": { "labels": [row['solicitante'] for row in top_solicitantes], "data": [row['total_gasto'] for row in top_solicitantes] }
    }

    chart_data_json = json.dumps(chart_data, default=decimal_default)

    # Busca saldo do setor para o header
    saldo_global_setor = get_saldo_global(setor_do_usuario)

    return render_template(
        "dashboard.html",
        user_role=session.get("user_role"),
        saldo_global=saldo_global_setor if is_financeiro() else None, 
        chart_data_json=chart_data_json,
        pendentes_antigas=pendentes_antigas, 
        filtro_data_inicio=data_inicio, 
        filtro_data_fim=data_fim
    )

# ==========================================================
# ROTA reject_fechamento ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/reject_fechamento/<id>", methods=["POST"])
def reject_fechamento(id):
    if not is_gestor():
        flash("Acesso negado.")
        return redirect(url_for("index"))
    
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row or row[0] != "Fechamento Solicitado":
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))
    
    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        flash("Informe um motivo para a recusa.")
        return redirect(url_for("index"))
    
    cursor.execute("""
    UPDATE rd
    SET status='Fechamento Recusado', motivo_recusa=%s
    WHERE id=%s AND setor = %s
    """, (motivo, id, setor_do_usuario))

    detalhe_motivo = f"Motivo: {motivo}"
    registrar_historico(conn, id, "Fechamento Recusado pelo Gestor", detalhe_motivo)

    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento recusado com sucesso.")
    
    active_tab = request.form.get('active_tab', 'tab4')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/reenviar_fechamento/<id>", methods=["POST"])
def reenviar_fechamento(id):
    flash("Utilize o botão 'Corrigir e reenviar' para editar a RD.")
    return redirect(url_for("index"))

# ==========================================================
# ROTA edit_saldo ATUALIZADA
# Edita o saldo do 'setor' do financeiro
# ==========================================================
@app.route("/edit_saldo", methods=["POST"])
def edit_saldo():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor() # Pega o setor do financeiro
    try:
        novo_saldo = float(request.form["saldo_global"].replace(",", "."))
    except:
        flash("Saldo inválido.")
        return redirect(url_for("index"))

    set_saldo_global(setor_do_usuario, novo_saldo) # Atualiza o saldo do seu setor
    flash(f"Saldo Global do setor '{setor_do_usuario}' atualizado com sucesso.")
    
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA delete_file ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/delete_file/<id>", methods=["POST"])
def delete_file(id):
    filename = request.form.get("filename")
    if not filename:
        flash("Nenhum arquivo para excluir.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT arquivos, status, solicitante FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
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

    delete_file_from_r2(filename)
    arq_list.remove(filename)
    new_str = ",".join(arq_list) if arq_list else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s AND setor = %s", (new_str, id, setor_do_usuario))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Arquivo excluído com sucesso.")
    
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA registrar_saldo_devolvido ATUALIZADA
# Filtra por 'setor' e devolve saldo para o 'setor'
# ==========================================================
@app.route("/registrar_saldo_devolvido/<id>", methods=["POST"])
def registrar_saldo_devolvido(id):
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT valor, valor_adicional, valor_despesa, data_saldo_devolvido, status FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
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
    saldo_dev = total_cred - (val_desp or 0)
    
    # Devolve saldo para o SETOR
    saldo_setor = get_saldo_global(setor_do_usuario)
    set_saldo_global(setor_do_usuario, saldo_setor + saldo_dev)
    
    now = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
    UPDATE rd SET data_saldo_devolvido=%s, status='Fechado'
    WHERE id=%s AND setor = %s
    """, (now, id, setor_do_usuario))

    detalhe_devolvido = f"Valor devolvido ao saldo global: R$ {format_currency(saldo_dev)}"
    registrar_historico(conn, id, "Devolução de Saldo Registrada", detalhe_devolvido)

    conn.commit()
    cursor.close()
    conn.close()
    flash(f"Saldo devolvido com sucesso. Valor= R${format_currency(saldo_dev)}")
    
    active_tab = request.form.get('active_tab', 'tab7')
    return redirect(url_for("index", active_tab=active_tab))

# ==========================================================
# ROTA export_excel ATUALIZADA
# Exporta dados apenas do 'setor'
# ==========================================================
@app.route("/export_excel", methods=["GET"])
def export_excel():
    if "user_role" not in session:
        return redirect(url_for("index"))
        
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM rd WHERE setor = %s ORDER BY id ASC", (setor_do_usuario,))
    rd_list = cursor.fetchall()
    saldo_global_setor = get_saldo_global(setor_do_usuario)
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = wb.add_worksheet(f"Relatorio {setor_do_usuario}")

    header = [
        "Número RD", "Data Solicitação", "Solicitante", "Funcionário", "Valor Solicitado",
        "Valor Adicional", "Data do Adicional", "Centro de Custo", "Unidade de Negócio",
        "Valor Gasto", "Saldo a Devolver", "Data de Fechamento", "Status", "Data Crédito Solicitado",
        "Data Crédito Liberado", "Data Débito Despesa", "Pronto Para Fechamento", f"Saldo Global ({setor_do_usuario})"
    ]
    for col, h in enumerate(header):
        ws.write(0, col, h)

    rowi = 1
    for rd_row in rd_list:
        # Mapeamento por nome de coluna para segurança
        ws.write(rowi, 0, rd_row.get('id'))
        ws.write(rowi, 1, str(rd_row.get('data', '')))
        ws.write(rowi, 2, rd_row.get('solicitante'))
        ws.write(rowi, 3, rd_row.get('funcionario'))
        ws.write(rowi, 4, float(rd_row.get('valor', 0) or 0))
        ws.write(rowi, 5, float(rd_row.get('valor_adicional', 0) or 0))
        ws.write(rowi, 6, str(rd_row.get('adicional_data', '')))
        ws.write(rowi, 7, rd_row.get('centro_custo'))
        ws.write(rowi, 8, rd_row.get('unidade_negocio', ''))
        ws.write(rowi, 9, float(rd_row.get('valor_despesa', 0) or 0))
        ws.write(rowi, 10, float(rd_row.get('saldo_devolver', 0) or 0))
        ws.write(rowi, 11, str(rd_row.get('data_fechamento', '')))
        ws.write(rowi, 12, rd_row.get('status'))
        ws.write(rowi, 13, str(rd_row.get('data_credito_solicitado', '')))
        ws.write(rowi, 14, str(rd_row.get('data_credito_liberado', '')))
        ws.write(rowi, 15, str(rd_row.get('data_debito_despesa', '')))
        ws.write(rowi, 16, "Sim" if rd_row.get('pronto_fechamento') else "Não")
        ws.write(rowi, 17, float(saldo_global_setor))
        rowi += 1

    wb.close()
    output.seek(0)
    conn.close()

    return send_file(
        output,
        as_attachment=True,
        download_name=f"Relatorio_RD_{setor_do_usuario}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ==========================================================
# ROTA export_historico ATUALIZADA
# Exporta dados apenas do 'setor'
# ==========================================================
@app.route("/export_historico", methods=["GET"])
def export_historico():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    try:
        cursor.execute("SELECT rd_id, solicitante, valor, data_exclusao, usuario_excluiu FROM historico_exclusao WHERE setor = %s ORDER BY data_exclusao DESC", (setor_do_usuario,))
        historico = cursor.fetchall()
    except psycopg2.Error as e:
        conn.close()
        flash("Erro ao acessar banco de dados.")
        logging.error(f"Erro ao consultar histórico: {e}")
        return redirect(url_for("index"))

    if not historico:
        conn.close()
        flash(f"Nenhum registro de exclusão encontrado para o setor '{setor_do_usuario}'.")
        return redirect(url_for("index"))

    output = io.StringIO()
    output.write(f"Histórico de Exclusões de RDs - Setor: {setor_do_usuario}\n")
    output.write("=" * 50 + "\n")
    for reg in historico:
        rd_id, solic, valor, data_exc, usuario = reg
        linha = f"Data: {data_exc} | RD: {rd_id} | Solicitante: {solic} | Valor: R${format_currency(valor)} | Excluído por: {usuario}\n"
        output.write(linha)
    output.write("=" * 50 + "\n")
    output.write(f"Total de exclusões: {len(historico)}\n")

    buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    buffer.seek(0)
    conn.close()

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Historico_Exclusoes_{setor_do_usuario}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mimetype="text/plain"
    )

# ==========================================================
# ROTA historico_geral ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/historico_geral")
def historico_geral():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    query = """
    WITH ultima_acao_por_rd AS (
        SELECT DISTINCT ON (rd_id)
            rd_id,
            acao as ultima_acao,
            data_acao as data_ultima_acao,
            usuario as usuario_ultima_acao,
            detalhes as detalhes_ultima_acao
        FROM historico_acoes
        WHERE rd_id IS NOT NULL AND setor = %s
        ORDER BY rd_id, data_acao DESC
    ),
    contagem_por_rd AS (
        SELECT rd_id, COUNT(*) as total_movimentacoes
        FROM historico_acoes
        WHERE rd_id IS NOT NULL AND setor = %s
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
        cursor.execute(query, (setor_do_usuario, setor_do_usuario))
        resumo_rds = cursor.fetchall()
    except psycopg2.Error as e:
        logging.error(f"Erro ao consultar histórico resumido: {e}")
        conn.rollback()
        resumo_rds = []

    total_rds = len(resumo_rds)
    
    total_acoes = 0
    try:
        cursor.execute("SELECT COUNT(*) as total FROM historico_acoes WHERE rd_id IS NOT NULL AND setor = %s", (setor_do_usuario,))
        total_acoes_row = cursor.fetchone()
        if total_acoes_row:
            total_acoes = total_acoes_row['total']
    except psycopg2.Error as e:
        logging.error(f"Erro ao contar ações: {e}")
    
    ultima_acao = "N/A"
    try:
        cursor.execute(
            "SELECT MAX(data_acao) as data_acao FROM historico_acoes WHERE rd_id IS NOT NULL AND setor = %s", (setor_do_usuario,)
        )
        ultima_acao_row = cursor.fetchone()
        if ultima_acao_row and ultima_acao_row['data_acao']:
            ultima_acao = ultima_acao_row['data_acao'].strftime('%d/%m/%Y %H:%M')
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar última ação: {e}")

    conn.close()

    return render_template(
        "historico_geral.html",
        resumo_rds=resumo_rds,
        total_rds=total_rds,
        total_acoes=total_acoes,
        ultima_acao=ultima_acao
    )

# ==========================================================
# ROTA historico_geral_completo ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/historico_geral_completo")
def historico_geral_completo():
    if "user_role" not in session:
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    filtro_rd_id = request.args.get('rd_id', '').strip()
    filtro_usuario = request.args.get('usuario', '').strip()
    filtro_acao = request.args.get('acao', '').strip()
    filtro_data_inicio = request.args.get('data_inicio', '').strip()
    filtro_data_fim = request.args.get('data_fim', '').strip()
    filtro_periodo = request.args.get('periodo', '').strip()

    # Filtro base por SETOR
    query = "SELECT * FROM historico_acoes WHERE setor = %s"
    params = [setor_do_usuario]

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
        data_inicio, data_fim = None, None
        if filtro_periodo == 'hoje': data_inicio, data_fim = hoje, hoje
        elif filtro_periodo == '7dias': data_inicio, data_fim = hoje - timedelta(days=7), hoje
        elif filtro_periodo == '30dias': data_inicio, data_fim = hoje - timedelta(days=30), hoje
        elif filtro_periodo == '90dias': data_inicio, data_fim = hoje - timedelta(days=90), hoje
        
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

    # Estatísticas baseadas nos resultados filtrados
    total_acoes = len(historico_completo)
    usuarios_unicos = set(evt['usuario'] for evt in historico_completo if evt['usuario'])
    rds_afetadas = set(evt['rd_id'] for evt in historico_completo if evt['rd_id'])
    periodo = "Sem dados"
    if historico_completo:
        periodo = f"{historico_completo[-1]['data_acao'].strftime('%d/%m/%Y')} a {historico_completo[0]['data_acao'].strftime('%d/%m/%Y')}"

    # Listas para dropdowns (baseadas apenas no setor do usuário)
    try:
        cursor.execute("SELECT DISTINCT usuario FROM historico_acoes WHERE usuario IS NOT NULL AND setor = %s ORDER BY usuario", (setor_do_usuario,))
        usuarios_disponiveis = [row['usuario'] for row in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT acao FROM historico_acoes WHERE acao IS NOT NULL AND setor = %s ORDER BY acao", (setor_do_usuario,))
        acoes_disponiveis = [row['acao'] for row in cursor.fetchall()]
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar filtros: {e}")
        conn.rollback()
        usuarios_disponiveis, acoes_disponiveis = [], []

    conn.close()

    return render_template(
        "historico_geral_completo.html",
        historico=historico_completo,
        total_acoes=total_acoes,
        usuarios_unicos=len(usuarios_unicos),
        rds_afetadas=len(rds_afetadas),
        periodo=periodo,
        filtro_rd_id=filtro_rd_id, filtro_usuario=filtro_usuario, filtro_acao=filtro_acao,
        filtro_data_inicio=filtro_data_inicio, filtro_data_fim=filtro_data_fim, filtro_periodo=filtro_periodo,
        usuarios_disponiveis=usuarios_disponiveis, acoes_disponiveis=acoes_disponiveis
    )

@app.route("/logout")
def logout():
    session.clear()
    flash("Logout realizado com sucesso.")
    return redirect(url_for("index"))

@app.route("/cadastro_funcionario", methods=["GET"])
def cadastro_funcionario():
    # Apenas redireciona, o formulário agora está no admin
    if is_financeiro():
        return redirect(url_for('admin_usuarios'))
    return render_template("cadastro_funcionario.html") # Mantém para não quebrar links

# ==========================================================
# ROTA cadastrar_funcionario ATUALIZADA
# Adiciona 'setor'
# ==========================================================
@app.route("/cadastrar_funcionario", methods=["POST"])
def cadastrar_funcionario():
    # Esta rota agora só deve ser usada pelo financeiro no admin
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))
        
    setor_do_usuario = user_setor() # O funcionário é cadastrado no setor do financeiro
    nome = request.form["nome"].strip()
    centro_custo = request.form["centroCusto"].strip()
    unidade_negocio = request.form["unidadeNegocio"].strip()

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("""
    INSERT INTO funcionarios (nome, centro_custo, unidade_negocio, setor)
    VALUES (%s, %s, %s, %s)
    """, (nome, centro_custo, unidade_negocio, setor_do_usuario))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Funcionário cadastrado com sucesso.")
    
    # Redireciona de volta para a consulta de funcionários
    return redirect(url_for("consulta_funcionario"))

# ==========================================================
# ROTA consulta_funcionario ATUALIZADA
# Filtra por 'setor'
# ==========================================================
@app.route("/consulta_funcionario", methods=["GET"])
def consulta_funcionario():
    if "user_role" not in session:
        return redirect(url_for("index"))
        
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM funcionarios WHERE setor = %s ORDER BY nome ASC", (setor_do_usuario,))
    funcionarios = cursor.fetchall()
    conn.close()
    return render_template("consulta_funcionario.html", funcionarios=funcionarios)

# ==========================================================
# ROTAS DE DIVERGENTES ATUALIZADAS
# Filtram por 'setor'
# ==========================================================
@app.route("/marcar_divergente/<id>", methods=["GET", "POST"])
def marcar_divergente(id):
    if "user_role" not in session or session["user_role"] not in ["gestor", "solicitante"]:
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status FROM rd WHERE id = %s AND setor = %s", (id, setor_do_usuario))
    rd = cursor.fetchone()
    if not rd:
        flash("RD não encontrada.")
        cursor.close()
        conn.close()
        return redirect(url_for("index"))
    
    # ... (resto da lógica é igual)
    if rd['status'] == 'Fechado':
        flash("Não é possível marcar uma RD já fechada como divergente.")
        cursor.close()
        conn.close()
        return redirect(url_for("index"))
    
    if request.method == "GET":
        cursor.close()
        conn.close()
        return render_template("motivo_divergente.html", rd_id=id)
    else: # POST
        motivo_div = request.form.get("motivo_divergente", "").strip()
        cursor.execute("""
        UPDATE rd
        SET anexo_divergente = TRUE,
            motivo_divergente = %s
        WHERE id = %s AND setor = %s
        """, (motivo_div, id, setor_do_usuario))
        
        detalhe_motivo = f"Motivo: {motivo_div}" if motivo_div else "Nenhum motivo informado."
        registrar_historico(conn, id, "Marcada como Divergente", detalhe_motivo)
        
        conn.commit()
        cursor.close()
        conn.close()
        flash("RD marcada como divergente.")
        
        active_tab = request.form.get('active_tab', 'tab3')
        return redirect(url_for("index", active_tab=active_tab))
    
@app.route("/anexos_divergentes", methods=["GET"])
def anexos_divergentes():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM rd WHERE anexo_divergente = TRUE AND setor = %s ORDER BY id", (setor_do_usuario,))
    divergentes = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("divergentes.html", divergentes=divergentes, user_role=session.get("user_role"))

@app.route("/corrigir_divergente/<id>", methods=["GET", "POST"])
def corrigir_divergente(id):
    if not is_supervisor():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    
    if request.method == "GET":
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM rd WHERE id = %s AND setor = %s", (id, setor_do_usuario))
        rd = cursor.fetchone()
        cursor.close()
        conn.close()
        if not rd:
            flash("RD não encontrada.")
            return redirect(url_for("anexos_divergentes"))
        return render_template("corrigir_divergente.html", rd=rd)
    else: # POST
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)

        cursor.execute("SELECT arquivos FROM rd WHERE id = %s AND setor = %s", (id, setor_do_usuario))
        row = cursor.fetchone()
        if not row:
            flash("RD não encontrada.")
            conn.close()
            return redirect(url_for("anexos_divergentes"))
            
        a_list = row[0].split(",") if (row and row[0]) else []

        if "arquivo" in request.files:
            for f in request.files.getlist("arquivo"):
                if f.filename:
                    fname = f"{id}_{f.filename}"
                    upload_file_to_r2(f, fname)
                    a_list.append(fname)
        new_arq_str = ",".join(a_list) if a_list else None

        cursor.execute("UPDATE rd SET arquivos = %s WHERE id = %s AND setor = %s", (new_arq_str, id, setor_do_usuario))
        conn.commit()

        cursor.execute("""
        UPDATE rd
        SET anexo_divergente = FALSE,
            motivo_divergente = NULL
        WHERE id = %s AND setor = %s
        """, (id, setor_do_usuario))
        
        registrar_historico(conn, id, "Divergência Corrigida")
        
        conn.commit()

        cursor.close()
        conn.close()
        flash("Correção da divergência realizada com sucesso.")
        return redirect(url_for("anexos_divergentes"))
    
@app.route("/marcar_pronto_fechamento/<id>", methods=["POST"])
def marcar_pronto_fechamento(id):
    if not is_supervisor():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT pronto_fechamento FROM rd WHERE id=%s AND setor = %s", (id, setor_do_usuario))
    row = cursor.fetchone()
    if not row:
        flash("RD não encontrada.")
        conn.close()
        return redirect(url_for("index"))

    novo_valor = not row["pronto_fechamento"]
    cursor.execute("UPDATE rd SET pronto_fechamento=%s WHERE id=%s AND setor = %s", (novo_valor, id, setor_do_usuario))
    
    if novo_valor:
        registrar_historico(conn, id, "Marcada como Pronta para Fechamento")
        flash("RD marcada como pronta para fechamento.")
    else:
        registrar_historico(conn, id, "Desmarcada como Pronta para Fechamento")
        flash("RD desmarcada como pronta para fechamento.")
        
    conn.commit()
    cursor.close()
    conn.close()
    
    active_tab = request.form.get('active_tab', 'tab3')
    return redirect(url_for("index", active_tab=active_tab))


# ==========================================================
# NOVAS ROTAS DE ADMINISTRAÇÃO (FINANCEIRO)
# ==========================================================

@app.route("/admin")
def admin_hub():
    """Página principal de administração para o financeiro."""
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))
        
    setor_do_usuario = user_setor()
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    # Lista setores (da tabela de saldos)
    cursor.execute("SELECT * FROM saldo_global_por_setor ORDER BY setor")
    setores = cursor.fetchall()
    
    # Lista usuários (do setor do financeiro)
    cursor.execute("SELECT * FROM usuarios WHERE setor = %s ORDER BY username", (setor_do_usuario,))
    usuarios_setor = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        "admin.html", 
        user_role=user_role(),
        setores=setores,
        usuarios_setor=usuarios_setor,
        setor_atual=setor_do_usuario
    )

@app.route("/admin/add_setor", methods=["POST"])
def add_setor():
    """Adiciona um novo setor (ex: Comercial) com saldo zerado."""
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))
        
    nome_setor = request.form.get("nome_setor", "").strip().lower()
    
    if not nome_setor:
        flash("Nome do setor não pode ser vazio.")
        return redirect(url_for("admin_hub"))
        
    conn = get_pg_connection()
    cursor = conn.cursor()
    try:
        # Adiciona o novo setor com saldo 0
        cursor.execute(
            "INSERT INTO saldo_global_por_setor (setor, saldo) VALUES (%s, 0)",
            (nome_setor,)
        )
        conn.commit()
        flash(f"Setor '{nome_setor}' criado com sucesso (base zerada).")
    except psycopg2.IntegrityError:
        conn.rollback()
        flash(f"Erro: Setor '{nome_setor}' já existe.")
    except Exception as e:
        conn.rollback()
        flash(f"Erro ao criar setor: {e}")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for("admin_hub"))

@app.route("/admin/add_usuario", methods=["POST"])
def add_usuario():
    """Adiciona um novo usuário (gestor, solicitante) a um setor."""
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "").strip()
    setor = request.form.get("setor", "").strip() # Setor vem do dropdown

    if not all([username, password, role, setor]):
        flash("Todos os campos são obrigatórios para criar um usuário.")
        return redirect(url_for("admin_hub"))
        
    # Gera o hash da senha
    password_hash = generate_password_hash(password)
    
    conn = get_pg_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO usuarios (username, password_hash, role, setor) VALUES (%s, %s, %s, %s)",
            (username, password_hash, role, setor)
        )
        conn.commit()
        flash(f"Usuário '{username}' ({role} / {setor}) criado com sucesso.")
    except psycopg2.IntegrityError:
        conn.rollback()
        flash(f"Erro: Usuário '{username}' já existe.")
    except Exception as e:
        conn.rollback()
        flash(f"Erro ao criar usuário: {e}")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for("admin_hub"))


# ==========================================================
if __name__ == "__main__":
    init_db()
    app.run(debug=True)