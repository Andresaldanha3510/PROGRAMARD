from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# ============ Config. Cloudflare R2 ============
import boto3
from botocore.client import Config

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
            password=PG_PASSWORD
        )
        return conn
    except psycopg2.Error as e:
        logging.error(f"Erro ao conectar ao PostgreSQL: {e}")
        import sys
        sys.exit(1)

def init_db():
    conn = get_pg_connection()
    cursor = conn.cursor()

    # Tabela RD
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
        data_credito_solicitado DATE,  -- Nova coluna
        data_credito_liberado DATE,    -- Nova coluna
        data_debito_despesa DATE       -- Nova coluna
    );
    """
    cursor.execute(create_rd_table)

    # Verificar e adicionar colunas novas, se necessário
    for col in ['data_credito_solicitado', 'data_credito_liberado', 'data_debito_despesa']:
        cursor.execute(f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='rd' AND column_name='{col}'
        """)
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE rd ADD COLUMN {col} DATE")

    # anexo_divergente
    cursor.execute("""
    SELECT column_name 
    FROM information_schema.columns
    WHERE table_name='rd' AND column_name='anexo_divergente'
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN anexo_divergente BOOLEAN DEFAULT FALSE")

    # motivo_divergente
    cursor.execute("""
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name='rd' AND column_name='motivo_divergente'
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN motivo_divergente TEXT")

    # Tabela saldo_global
    create_saldo_global_table = """
    CREATE TABLE IF NOT EXISTS saldo_global (
        id SERIAL PRIMARY KEY,
        saldo NUMERIC(15,2) DEFAULT 30000
    );
    """
    cursor.execute(create_saldo_global_table)
    cursor.execute("SELECT COUNT(*) FROM saldo_global")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO saldo_global (saldo) VALUES (30000)")

    # Tabela funcionarios
    create_funcionarios_table = """
    CREATE TABLE IF NOT EXISTS funcionarios (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        centro_custo TEXT NOT NULL,
        unidade_negocio TEXT NOT NULL
    );
    """
    cursor.execute(create_funcionarios_table)

    conn.commit()
    cursor.close()
    conn.close()

# ====== Funções de lógica ======
def generate_custom_id():
    current_year = datetime.now().year % 100
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM rd
        WHERE split_part(id, '.', 2)::INTEGER=%s
        ORDER BY (split_part(id, '.',1))::INTEGER DESC LIMIT 1
    """, (current_year,))
    last_id = cursor.fetchone()
    conn.close()
    if not last_id:
        return f"400.{current_year}"
    last_str = last_id[0]
    last_num_str, _ = last_str.split('.')
    last_num = int(last_num_str)
    return f"{last_num+1}.{current_year}"

def user_role():
    return session.get('user_role')

def is_solicitante():
    return user_role() == "solicitante"

def is_gestor():
    return user_role() == "gestor"

def is_financeiro():
    return user_role() == "financeiro"

def can_add():
    return user_role() in ["solicitante","gestor","financeiro"]

# Supervisor pode editar apenas anexos; se o usuário for supervisor, o UPDATE só altera a coluna de arquivos.
def can_edit(status):
    if status == "Fechado":
        return False
    if is_solicitante():
        return status in ["Pendente","Fechamento Recusado"]
    if is_gestor() or is_financeiro() or user_role()=="supervisor":
        return True
    return False

def can_delete(status, solicitante):
    if status == "Fechado":
        return False
    if status=="Pendente" and is_solicitante():
        return True
    if (is_gestor() or is_financeiro()) and status in ["Pendente","Aprovado","Liberado"]:
        return True
    return False

def can_approve(status):
    if status=="Pendente" and is_gestor():
        return True
    if status=="Fechamento Solicitado" and is_gestor():
        return True
    if status=="Aprovado" and is_financeiro():
        return True
    return False

def can_request_additional(status):
    return (is_solicitante() and status=="Liberado")

def can_close(status):
    return (is_solicitante() and status=="Liberado")

def get_saldo_global():
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT saldo FROM saldo_global LIMIT 1")
    saldo = cursor.fetchone()[0]
    conn.close()
    return saldo

def set_saldo_global(novo_saldo):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE saldo_global SET saldo=%s WHERE id=1",(novo_saldo,))
    conn.commit()
    conn.close()

def format_currency(value):
    if value is None:
        return "0,00"
    s = f"{value:,.2f}"
    parts = s.split(".")
    left = parts[0].replace(",",".")
    right= parts[1]
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

@app.route("/", methods=["GET","POST"])
def index():
    if request.method=="POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()

        if username=="gestor" and password=="115289":
            session["user_role"]="gestor"
            flash("Login como gestor bem-sucedido.")
        elif username=="financeiro" and password=="351073":
            session["user_role"]="financeiro"
            flash("Login como financeiro bem-sucedido.")
        elif username=="solicitante" and password=="102030":
            session["user_role"]="solicitante"
            flash("Login como solicitante bem-sucedido.")
        elif username=="supervisor" and password=="223344":
            session["user_role"]="supervisor"
            flash("Login como supervisor bem-sucedido.")
        else:
            flash("Credenciais inválidas.")
            return render_template("index.html", error="Credenciais inválidas", format_currency=format_currency)

        return redirect(url_for("index"))

    if "user_role" not in session:
        return render_template("index.html", error=None, format_currency=format_currency)

    conn = get_pg_connection()
    cursor = conn.cursor()

    if user_role()=="supervisor":
        cursor.execute("SELECT * FROM rd WHERE status='Liberado'")
        liberados = cursor.fetchall()
        pendentes = []
        aprovados = []
        fechamento_solicitado = []
        fechamento_recusado = []
        fechados = []
        # Para supervisor, contar os RDs com anexos divergentes
        cursor.execute("SELECT COUNT(*) FROM rd WHERE anexo_divergente=TRUE")
        divergentes_count = cursor.fetchone()[0]
    else:
        cursor.execute("SELECT * FROM rd WHERE status='Pendente'")
        pendentes = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Aprovado'")
        aprovados = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Liberado'")
        liberados = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechamento Solicitado'")
        fechamento_solicitado = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechamento Recusado'")
        fechamento_recusado = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechado'")
        fechados = cursor.fetchall()
        divergentes_count = 0

    saldo_global = get_saldo_global()
    adicional_id = request.args.get("adicional")
    fechamento_id = request.args.get("fechamento")
    conn.close()

    return render_template(
        "index.html",
        error=None,
        format_currency=format_currency,
        user_role=user_role(),
        saldo_global=saldo_global if is_financeiro() else None,
        pendentes=pendentes,
        aprovados=aprovados,
        liberados=liberados,
        fechamento_solicitado=fechamento_solicitado,
        fechamento_recusado=fechamento_recusado,
        fechados=fechados,
        divergentes_count=divergentes_count,
        can_add=can_add(),
        can_delete_func=can_delete,
        can_edit_func=can_edit,
        can_approve_func=can_approve,
        can_request_additional=can_request_additional,
        can_close=can_close,
        adicional_id=adicional_id,
        fechamento_id=fechamento_id
    )

@app.route("/add", methods=["POST"])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado",403

    solicitante     = request.form["solicitante"].strip()
    funcionario     = request.form["funcionario"].strip()
    data_str        = request.form["data"].strip()
    centro_custo    = request.form["centro_custo"].strip()
    observacao      = request.form.get("observacao","").strip()
    rd_tipo         = request.form.get("tipo","credito alelo").strip()
    unidade_negocio = request.form.get("unidade_negocio","").strip()

    try:
        valor = float(request.form["valor"].replace(",",".")) 
    except:
        flash("Valor inválido.")
        return redirect(url_for("index"))

    custom_id = generate_custom_id()
    data_atual = datetime.now().strftime("%Y-%m-%d")  # Data atual para crédito solicitado
    arquivos = []
    if "arquivo" in request.files:
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{custom_id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arquivos.append(fname)
    arquivos_str = ",".join(arquivos) if arquivos else None

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO rd (
      id, solicitante, funcionario, data, centro_custo,
      valor, status, arquivos, valor_liberado, observacao,
      tipo, unidade_negocio, data_credito_solicitado
    )
    VALUES (%s,%s,%s,%s,%s,
            %s,%s,%s,0,%s,
            %s,%s,%s)
    """, (custom_id, solicitante, funcionario, data_str, centro_custo,
          valor, "Pendente", arquivos_str, observacao, rd_tipo, unidade_negocio, data_atual))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD adicionada com sucesso.")
    return redirect(url_for("index"))

def can_edit_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return can_edit(row[0])

@app.route("/edit_form/<id>", methods=["GET"])
def edit_form(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()

    if not rd:
        flash("RD não encontrada.")
        return "RD não encontrada",404

    if not can_edit(rd[6]):
        flash("Acesso negado.")
        return "Acesso negado",403

    # Passamos user_role para o template para que o supervisor possa ter interface diferenciada
    return render_template("edit_form.html", rd=rd, user_role=session.get("user_role"))

@app.route("/edit_submit/<id>", methods=["POST"])
def edit_submit(id):
    if not can_edit_status(id):
        flash("Acesso negado.")
        return "Acesso negado",403

    conn = get_pg_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT status, arquivos FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    original_status = row[0]
    arquivos_str = row[1]

    # Atualização dos anexos sempre
    arqs_list = arquivos_str.split(",") if arquivos_str else []
    if "arquivo" in request.files:
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_list.append(fname)
    new_arqs = ",".join(arqs_list) if arqs_list else None

    # Se o usuário for supervisor, atualiza somente os anexos
    if user_role() == "supervisor":
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (new_arqs, id))
    else:
        solicitante     = request.form["solicitante"].strip()
        funcionario     = request.form["funcionario"].strip()
        data_str        = request.form["data"].strip()
        centro_custo    = request.form["centro_custo"].strip()
        observacao      = request.form.get("observacao","").strip()
        unidade_negocio = request.form.get("unidade_negocio","").strip()

        try:
            valor_novo = float(request.form["valor"].replace(",",".")) 
        except:
            flash("Valor inválido.")
            conn.close()
            return redirect(url_for("index"))

        cursor.execute("""
        UPDATE rd
        SET solicitante=%s,
            funcionario=%s,
            data=%s,
            centro_custo=%s,
            valor=%s,
            arquivos=%s,
            observacao=%s,
            unidade_negocio=%s
        WHERE id=%s
        """, (solicitante, funcionario, data_str, centro_custo, valor_novo,
              new_arqs, observacao, unidade_negocio, id))

        if is_solicitante() and original_status == "Fechamento Recusado":
            cursor.execute("UPDATE rd SET status='Fechamento Solicitado', motivo_recusa=NULL WHERE id=%s", (id,))

    conn.commit()
    cursor.close()
    conn.close()

    flash("RD atualizada com sucesso.")
    return redirect(url_for("index"))

@app.route("/approve/<id>", methods=["POST"])
def approve(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional, tipo FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    st_atual, val, val_adic, rd_tipo = row

    if not can_approve(st_atual):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    now = datetime.now().strftime("%Y-%m-%d")

    if st_atual == "Pendente" and is_gestor():
        new_st = "Aprovado"
        cursor.execute("""
        UPDATE rd SET status=%s, aprovado_data=%s
        WHERE id=%s
        """, (new_st, now, id))
    elif st_atual == "Aprovado" and is_financeiro():
        if rd_tipo.lower() == "reembolso":
            new_st = "Fechado"
            cursor.execute("""
            UPDATE rd SET status=%s, data_fechamento=%s
            WHERE id=%s
            """, (new_st, now, id))
        else:
            new_st = "Liberado"
            total_credit = val + (val_adic or 0)
            saldo_atual = get_saldo_global()
            novo_saldo = saldo_atual - total_credit
            set_saldo_global(novo_saldo)
            cursor.execute("""
            UPDATE rd SET status=%s, liberado_data=%s, valor_liberado=%s, data_credito_liberado=%s
            WHERE id=%s
            """, (new_st, now, total_credit, now, id))
    elif st_atual == "Fechamento Solicitado" and is_gestor():
        new_st = "Fechado"
        cursor.execute("UPDATE rd SET status=%s WHERE id=%s", (new_st, id))
    else:
        conn.close()
        flash("Não é possível aprovar/liberar esta RD.")
        return redirect(url_for("index"))

    conn.commit()
    cursor.close()
    conn.close()
    flash("Operação realizada com sucesso.")
    return redirect(url_for("index"))

@app.route("/delete/<id>", methods=["POST"])
def delete_rd(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT solicitante, status, valor_liberado FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    rd_solic, rd_status, rd_liber = row

    if not can_delete(rd_status, rd_solic):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    if rd_status == "Liberado" and rd_liber and rd_liber > 0:
        saldo = get_saldo_global()
        set_saldo_global(saldo + rd_liber)

    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    arq_str = cursor.fetchone()[0]
    if arq_str:
        for a in arq_str.split(","):
            delete_file_from_r2(a)

    cursor.execute("DELETE FROM rd WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD excluída com sucesso.")
    return redirect(url_for("index"))

@app.route("/adicional_submit/<id>", methods=["POST"])
def adicional_submit(id):
    if "arquivo" in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        row = cursor.fetchone()
        arqs_atual = row[0].split(",") if row and row[0] else []
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_atual.append(fname)
        new_arqs_str = ",".join(arqs_atual) if arqs_atual else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (new_arqs_str, id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        val_adi = float(request.form["valor_adicional"].replace(",", "."))
    except:
        flash("Valor adicional inválido.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor_adicional, adicionais_individuais, valor FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    st_atual, val_adic_atual, add_ind, val_sol = row

    if not can_request_additional(st_atual):
        conn.close()
        flash("Não é possível solicitar adicional agora.")
        return redirect(url_for("index"))

    novo_total = (val_adic_atual or 0) + val_adi
    if add_ind:
        partes = [x.strip() for x in add_ind.split(",")]
        idx = len(partes) + 1
        add_ind = add_ind + f", Adicional {idx}:{val_adi}"
    else:
        add_ind = f"Adicional 1:{val_adi}"

    data_add = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
    UPDATE rd
    SET valor_adicional=%s, adicional_data=%s, status='Pendente', adicionais_individuais=%s
    WHERE id=%s
    """, (novo_total, data_add, add_ind, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Crédito adicional solicitado. A RD voltou para 'Pendente'.")
    return redirect(url_for("index"))

@app.route("/fechamento_submit/<id>", methods=["POST"])
def fechamento_submit(id):
    if "arquivo" in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        row = cursor.fetchone()
        a_list = row[0].split(",") if row and row[0] else []
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                a_list.append(fname)
        new_str = ",".join(a_list) if a_list else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (new_str, id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        val_desp = float(request.form["valor_despesa"].replace(",", "."))
    except:
        flash("Valor da despesa inválido.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT valor, valor_adicional, status FROM rd WHERE id=%s", (id,))
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
    WHERE id=%s
    """, (val_desp, saldo_dev, data_fech, data_fech, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento solicitado. Aguarde aprovação do gestor.")
    return redirect(url_for("index"))

@app.route("/reject_fechamento/<id>", methods=["POST"])
def reject_fechamento(id):
    if not is_gestor():
        flash("Acesso negado.")
        return redirect(url_for("index"))
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
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
    WHERE id=%s
    """, (motivo, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento recusado com sucesso.")
    return redirect(url_for("index"))

@app.route("/reenviar_fechamento/<id>", methods=["POST"])
def reenviar_fechamento(id):
    flash("Utilize o botão 'Corrigir e reenviar' para editar a RD.")
    return redirect(url_for("index"))

@app.route("/edit_saldo", methods=["POST"])
def edit_saldo():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    try:
        novo_saldo = float(request.form["saldo_global"].replace(",", "."))
    except:
        flash("Saldo inválido.")
        return redirect(url_for("index"))

    set_saldo_global(novo_saldo)
    flash("Saldo Global atualizado com sucesso.")
    return redirect(url_for("index"))

@app.route("/delete_file/<id>", methods=["POST"])
def delete_file(id):
    filename = request.form.get("filename")
    if not filename:
        flash("Nenhum arquivo para excluir.")
        return redirect(request.referrer or url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos, status, solicitante FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(request.referrer or url_for("index"))

    arquivos_str, rd_status, rd_solic = row
    if not arquivos_str:
        conn.close()
        flash("Nenhum arquivo na RD.")
        return redirect(request.referrer or url_for("index"))

    if not (can_edit(rd_status) or can_delete(rd_status, rd_solic)):
        conn.close()
        flash("Você não pode excluir arquivos desta RD.")
        return redirect(request.referrer or url_for("index"))

    arq_list = arquivos_str.split(",")
    if filename not in arq_list:
        conn.close()
        flash("Arquivo não pertence a esta RD.")
        return redirect(request.referrer or url_for("index"))

    delete_file_from_r2(filename)
    arq_list.remove(filename)
    new_str = ",".join(arq_list) if arq_list else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (new_str, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Arquivo excluído com sucesso.")
    return redirect(request.referrer or url_for("index"))

@app.route("/registrar_saldo_devolvido/<id>", methods=["POST"])
def registrar_saldo_devolvido(id):
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT valor, valor_adicional, valor_despesa, data_saldo_devolvido FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    val_sol, val_adic, val_desp, data_sal_dev = row
    if data_sal_dev:
        conn.close()
        flash("Saldo já registrado antes.")
        return redirect(url_for("index"))
    total_cred = val_sol + (val_adic or 0)
    if total_cred < (val_desp or 0):
        conn.close()
        flash("Despesa maior que o total de créditos.")
        return redirect(url_for("index"))
    saldo_dev = total_cred - (val_desp or 0)
    saldo = get_saldo_global()
    set_saldo_global(saldo + saldo_dev)
    now = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("UPDATE rd SET data_saldo_devolvido=%s WHERE id=%s", (now, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash(f"Saldo devolvido com sucesso. Valor= R${saldo_dev:,.2f}")
    return redirect(url_for("index"))

@app.route("/export_excel", methods=["GET"])
def export_excel():
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd ORDER BY id ASC")
    rd_list = cursor.fetchall()
    saldo_global = get_saldo_global()
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = wb.add_worksheet("Relatorio")

    # Cabeçalhos atualizados com "Unidade de Negócio" e as novas datas
    header = [
        "Número RD", "Data Solicitação", "Solicitante", "Funcionário", "Valor Solicitado",
        "Valor Adicional", "Data do Adicional", "Centro de Custo", "Unidade de Negócio",
        "Valor Gasto", "Saldo a Devolver", "Data de Fechamento", "Status", "Data Crédito Solicitado",
        "Data Crédito Liberado", "Data Débito Despesa", "Saldo Global"
    ]
    for col, h in enumerate(header):
        ws.write(0, col, h)

    # Preenchendo os dados
    rowi = 1
    for rd_row in rd_list:
        rd_id = rd_row[0]                    # id
        rd_data = rd_row[3]                  # data
        rd_solic = rd_row[1]                 # solicitante
        rd_func = rd_row[2]                  # funcionario
        rd_valor = rd_row[5]                 # valor
        rd_val_adic = rd_row[7]              # valor_adicional
        rd_adic_data = rd_row[8]             # adicional_data
        rd_ccusto = rd_row[4]                # centro_custo
        rd_unidade_negocio = rd_row[18]      # unidade_negocio
        rd_desp = rd_row[9]                  # valor_despesa
        rd_saldo_dev = rd_row[10]            # saldo_devolver
        rd_data_fech = rd_row[11]            # data_fechamento
        rd_status = rd_row[6]                # status
        rd_data_cred_solic = rd_row[22]      # data_credito_solicitado
        rd_data_cred_liber = rd_row[23]      # data_credito_liberado
        rd_data_deb_desp = rd_row[24]        # data_debito_despesa

        ws.write(rowi, 0, rd_id)
        ws.write(rowi, 1, str(rd_data) if rd_data else "")
        ws.write(rowi, 2, rd_solic)
        ws.write(rowi, 3, rd_func)
        ws.write(rowi, 4, float(rd_valor or 0))
        ws.write(rowi, 5, float(rd_val_adic or 0))
        ws.write(rowi, 6, str(rd_adic_data) if rd_adic_data else "")
        ws.write(rowi, 7, rd_ccusto)
        ws.write(rowi, 8, rd_unidade_negocio if rd_unidade_negocio else "")
        ws.write(rowi, 9, float(rd_desp or 0))
        ws.write(rowi, 10, float(rd_saldo_dev or 0))
        ws.write(rowi, 11, str(rd_data_fech) if rd_data_fech else "")
        ws.write(rowi, 12, rd_status)
        ws.write(rowi, 13, str(rd_data_cred_solic) if rd_data_cred_solic else "")
        ws.write(rowi, 14, str(rd_data_cred_liber) if rd_data_cred_liber else "")
        ws.write(rowi, 15, str(rd_data_deb_desp) if rd_data_deb_desp else "")
        ws.write(rowi, 16, float(saldo_global))
        rowi += 1

    wb.close()
    output.seek(0)
    conn.close()

    return send_file(
        output,
        as_attachment=True,
        download_name=f"Relatorio_RD_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/logout")
def logout():
    session.clear()
    flash("Logout realizado com sucesso.")
    return redirect(url_for("index"))

# =========== Rotas p/ Funcionários ===========
@app.route("/cadastro_funcionario", methods=["GET"])
def cadastro_funcionario():
    return render_template("cadastro_funcionario.html")

@app.route("/cadastrar_funcionario", methods=["POST"])
def cadastrar_funcionario():
    nome = request.form["nome"].strip()
    centro_custo = request.form["centroCusto"].strip()
    unidade_negocio = request.form["unidadeNegocio"].strip()

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO funcionarios (nome, centro_custo, unidade_negocio)
    VALUES (%s, %s, %s)
    """, (nome, centro_custo, unidade_negocio))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Funcionário cadastrado com sucesso.")
    return redirect(url_for("cadastro_funcionario"))

@app.route("/consulta_funcionario", methods=["GET"])
def consulta_funcionario():
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM funcionarios ORDER BY nome ASC")
    funcionarios = cursor.fetchall()
    conn.close()
    return render_template("consulta_funcionario.html", funcionarios=funcionarios)

# =========== Divergentes ===========
@app.route("/marcar_divergente/<id>", methods=["GET","POST"])
def marcar_divergente(id):
    """
    Ao clicar no botão "Anexo Divergente" na aba Liberados,
    gestor/solicitante insere o motivo antes de marcar divergente.
    """
    if "user_role" not in session or session["user_role"] not in ["gestor", "solicitante"]:
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    if request.method == "GET":
        # Exibe um formulário para digitar o motivo
        return render_template("motivo_divergente.html", rd_id=id)
    else:
        # POST: grava o motivo no BD e marca o RD como divergente
        motivo_div = request.form.get("motivo_divergente", "").strip()
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE rd
        SET anexo_divergente = TRUE,
            motivo_divergente = %s
        WHERE id = %s
        """, (motivo_div, id))
        conn.commit()
        cursor.close()
        conn.close()
        flash("RD marcado como divergente.")
        return redirect(url_for("index"))

@app.route("/anexos_divergentes", methods=["GET"])
def anexos_divergentes():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE anexo_divergente = TRUE ORDER BY id")
    divergentes = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("divergentes.html", divergentes=divergentes, user_role=session.get("user_role"))

@app.route("/corrigir_divergente/<id>", methods=["GET", "POST"])
def corrigir_divergente(id):
    if "user_role" not in session or session["user_role"] != "supervisor":
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if request.method == "GET":
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rd WHERE id = %s", (id,))
        rd = cursor.fetchone()
        cursor.close()
        conn.close()
        if not rd:
            flash("RD não encontrada.")
            return redirect(url_for("anexos_divergentes"))
        return render_template("corrigir_divergente.html", rd=rd)
    else:
        # POST: atualiza os anexos e remove a marca de divergente, voltando o RD para Liberados
        conn = get_pg_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT arquivos FROM rd WHERE id = %s", (id,))
        row = cursor.fetchone()
        a_list = row[0].split(",") if (row and row[0]) else []

        if "arquivo" in request.files:
            for f in request.files.getlist("arquivo"):
                if f.filename:
                    fname = f"{id}_{f.filename}"
                    upload_file_to_r2(f, fname)
                    a_list.append(fname)
        new_arq_str = ",".join(a_list) if a_list else None

        # Atualiza os anexos
        cursor.execute("UPDATE rd SET arquivos = %s WHERE id = %s", (new_arq_str, id))
        conn.commit()

        # Remove a marca de divergente e altera o status para Liberado
        cursor.execute("""
        UPDATE rd
        SET anexo_divergente = FALSE,
            status = 'Liberado'
        WHERE id = %s
        """, (id,))
        conn.commit()

        cursor.close()
        conn.close()
        flash("Correção realizada e RD retornou para Liberados.")
        return redirect(url_for("anexos_divergentes"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
