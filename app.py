from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Configurações para Cloudflare R2
import boto3
from botocore.client import Config

R2_ACCESS_KEY = os.getenv('R2_ACCESS_KEY', 'your_r2_access_key')
R2_SECRET_KEY = os.getenv('R2_SECRET_KEY', 'your_r2_secret_key')
R2_ENDPOINT   = 'https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com'
R2_BUCKET_NAME = 'meu-bucket-r2'
R2_PUBLIC_URL = "https://pub-1e6f8559bc2b413c889fbf4860462599.r2.dev"

def get_r2_public_url(object_name):
    return f"{R2_PUBLIC_URL}/{object_name}"

def upload_file_to_r2(file_obj, object_name):
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4')
    )
    file_obj.seek(0)
    s3.upload_fileobj(file_obj, R2_BUCKET_NAME, object_name)

def delete_file_from_r2(object_name):
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4')
    )
    s3.delete_object(Bucket=R2_BUCKET_NAME, Key=object_name)

import io
import xlsxwriter
import logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'secret123')

# Configuração do banco de dados
PG_HOST = os.getenv('PG_HOST', 'dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com')
PG_PORT = os.getenv('PG_PORT', '5432')
PG_DB   = os.getenv('PG_DB', 'programard_db')
PG_USER = os.getenv('PG_USER', 'programard_db_user')
PG_PASSWORD = os.getenv('PG_PASSWORD', 'hU9wJmIfgiyCg02KFQ3a4AropKSMopXr')

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
    # Cria a tabela rd com todas as colunas necessárias
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
        observacao TEXT,
        tipo TEXT DEFAULT 'credito alelo',
        data_saldo_devolvido DATE,
        unidade_negocio TEXT,
        motivo_recusa TEXT,
        adicionais_individuais TEXT,
        motivo_anexo_divergente TEXT,
        data_divergencia TIMESTAMP,
        data_ultima_operacao TIMESTAMP
    );
    """
    cursor.execute(create_rd_table)

    # Adiciona as colunas caso não existam (usando ALTER TABLE ... IF NOT EXISTS)
    alter_commands = [
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS valor_liberado NUMERIC(15,2) DEFAULT 0;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS observacao TEXT;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS tipo TEXT DEFAULT 'credito alelo';",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS data_saldo_devolvido DATE;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS unidade_negocio TEXT;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS motivo_recusa TEXT;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS adicionais_individuais TEXT;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS motivo_anexo_divergente TEXT;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS data_divergencia TIMESTAMP;",
        "ALTER TABLE rd ADD COLUMN IF NOT EXISTS data_ultima_operacao TIMESTAMP;"
    ]
    for cmd in alter_commands:
        cursor.execute(cmd)

    # Tabela de saldo global
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

    # Tabela de Funcionários
    create_funcionarios_table = """
    CREATE TABLE IF NOT EXISTS funcionarios (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        centro_custo TEXT NOT NULL,
        unidade_negocio TEXT NOT NULL
    );
    """
    cursor.execute(create_funcionarios_table)

    # Tabela de Créditos Adicionais (registro individual)
    create_creditos_adicionais = """
    CREATE TABLE IF NOT EXISTS creditos_adicionais (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        valor NUMERIC(15,2) NOT NULL,
        data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (rd_id) REFERENCES rd(id) ON DELETE CASCADE
    );
    """
    cursor.execute(create_creditos_adicionais)

    conn.commit()
    cursor.close()
    conn.close()

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
    return user_role() == 'solicitante'

def is_gestor():
    return user_role() == 'gestor'

def is_financeiro():
    return user_role() == 'financeiro'

def is_supervisor():
    return session.get('user_role') == 'supervisor'

def can_add():
    return user_role() in ['solicitante', 'gestor', 'financeiro']

def can_edit(status):
    if status == 'Fechado':
        return False
    if is_solicitante():
        return status in ['Pendente', 'Fechamento Recusado']
    if is_gestor() or is_financeiro():
        return True
    return False

def can_delete(status, solicitante):
    if status == 'Fechado':
        return False
    if status == 'Pendente' and is_solicitante():
        return True
    if (is_gestor() or is_financeiro()) and status in ['Pendente', 'Aprovado', 'Liberado']:
        return True
    return False

def can_approve(status):
    if status == 'Pendente' and is_gestor():
        return True
    if status == 'Fechamento Solicitado' and is_gestor():
        return True
    if status == 'Aprovado' and is_financeiro():
        return True
    return False

def can_request_additional(status):
    return (is_solicitante() and status == 'Liberado')

def can_close(status):
    return (is_solicitante() and status == 'Liberado')

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
    cursor.execute("UPDATE saldo_global SET saldo=%s WHERE id=1", (novo_saldo,))
    conn.commit()
    conn.close()

def format_currency(value):
    if value is None:
        return "0,00"
    s = f"{value:,.2f}"
    parts = s.split('.')
    left = parts[0].replace(',', '.')
    right = parts[1]
    return f"{left},{right}"

app.jinja_env.globals.update(
    get_r2_public_url=get_r2_public_url,
    is_gestor=lambda: session.get('user_role') == 'gestor',
    is_solicitante=lambda: session.get('user_role') == 'solicitante',
    is_financeiro=lambda: session.get('user_role') == 'financeiro',
    is_supervisor=is_supervisor
)

# Rota principal e login
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == 'gestor' and password == '115289':
            session['user_role'] = 'gestor'
            flash("Login como gestor bem-sucedido.")
        elif username == 'financeiro' and password == '351073':
            session['user_role'] = 'financeiro'
            flash("Login como financeiro bem-sucedido.")
        elif username == 'solicitante' and password == '102030':
            session['user_role'] = 'solicitante'
            flash("Login como solicitante bem-sucedido.")
        elif username == 'supervisor' and password == '335289':
            session['user_role'] = 'supervisor'
            flash("Login como supervisor bem-sucedido.")
        else:
            flash("Credenciais inválidas.")
            return render_template('index.html', error="Credenciais inválidas", format_currency=format_currency)
        return redirect(url_for('index'))
    
    if 'user_role' not in session:
        return render_template('index.html', error=None, format_currency=format_currency)
    
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE status='Pendente'")
    pendentes = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Aprovado'")
    aprovados = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Liberado'")
    liberados = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Fechamento Recusado'")
    fechamento_recusado = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Fechado'")
    fechados = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Saldo Devolver'")
    saldo_devolver = cursor.fetchall()
    saldo_global = get_saldo_global()
    adicional_id = request.args.get('adicional')
    fechamento_id = request.args.get('fechamento')
    
    # Consulta para divergências (popup para supervisor)
    divergent_rds = []
    if session.get('user_role') == 'supervisor':
        cursor.execute("SELECT id, motivo_anexo_divergente, data_divergencia FROM rd WHERE motivo_anexo_divergente IS NOT NULL")
        divergent_rds = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        'index.html',
        error=None,
        format_currency=format_currency,
        user_role=user_role(),
        saldo_global=saldo_global if is_financeiro() else None,
        pendentes=pendentes,
        aprovados=aprovados,
        liberados=liberados,
        fechamento_recusado=fechamento_recusado,
        fechados=fechados,
        saldo_devolver=saldo_devolver,
        can_add=can_add(),
        can_delete_func=can_delete,
        can_edit_func=can_edit,
        can_approve_func=can_approve,
        can_request_additional=can_request_additional,
        can_close=can_close,
        adicional_id=adicional_id,
        fechamento_id=fechamento_id,
        divergent_rds=divergent_rds
    )

# Rota para editar o saldo global
@app.route('/edit_saldo', methods=['POST'])
def edit_saldo():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for('index'))
    try:
        novo_saldo = float(request.form['saldo_global'].replace(',', '.'))
    except (ValueError, KeyError):
        flash("Saldo inválido.")
        return redirect(url_for('index'))
    set_saldo_global(novo_saldo)
    flash("Saldo Global atualizado com sucesso.")
    return redirect(url_for('index'))

# Rota para adicionar nova RD
@app.route('/add', methods=['POST'])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado", 403

    solicitante     = request.form['solicitante'].strip()
    funcionario     = request.form['funcionario'].strip()
    data            = request.form['data'].strip()
    centro_custo    = request.form['centro_custo'].strip()
    observacao      = request.form.get('observacao', '').strip()
    rd_tipo         = request.form.get('tipo', 'credito alelo').strip()
    unidade_negocio = request.form.get('unidade_negocio', '').strip()

    try:
        valor = float(request.form['valor'].replace(',', '.'))
    except ValueError:
        flash("Valor inválido.")
        return redirect(url_for('index'))

    custom_id = generate_custom_id()
    arquivos = []
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{custom_id}_{file.filename}"
                upload_file_to_r2(file, filename)
                arquivos.append(filename)
    arquivos_str = ','.join(arquivos) if arquivos else None

    conn = get_pg_connection()
    cursor = conn.cursor()
    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        INSERT INTO rd (
            id, solicitante, funcionario, data, centro_custo,
            valor, status, arquivos, valor_liberado, observacao,
            tipo, unidade_negocio, data_ultima_operacao
        )
        VALUES (%s, %s, %s, %s, %s,
                %s, %s, %s, 0, %s,
                %s, %s, %s)
    """, (custom_id, solicitante, funcionario, data, centro_custo,
          valor, 'Pendente', arquivos_str, observacao,
          rd_tipo, unidade_negocio, current_date))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD adicionada com sucesso.")
    return redirect(url_for('index'))

# Rota para exibir o formulário de edição
@app.route('/edit_form/<id>', methods=['GET'])
def edit_form(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()

    if not rd:
        flash("RD não encontrada.")
        return "RD não encontrada", 404

    if not can_edit(rd[6]):
        flash("Acesso negado.")
        return "Acesso negado", 403

    return render_template('edit_form.html', rd=rd)

# Rota para submeter edição
@app.route('/edit_submit/<id>', methods=['POST'])
def edit_submit(id):
    if not can_edit_status(id):
        flash("Acesso negado.")
        return "Acesso negado", 403

    solicitante     = request.form['solicitante'].strip()
    funcionario     = request.form['funcionario'].strip()
    data            = request.form['data'].strip()
    centro_custo    = request.form['centro_custo'].strip()
    observacao      = request.form.get('observacao', '').strip()
    unidade_negocio = request.form.get('unidade_negocio', '').strip()

    try:
        valor = float(request.form['valor'].replace(',', '.'))
    except ValueError:
        flash("Valor inválido.")
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, arquivos FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    original_status = row[0]
    arquivos = row[1].split(',') if row and row[1] else []

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                upload_file_to_r2(file, filename)
                arquivos.append(filename)
    arquivos_str = ','.join(arquivos) if arquivos else None

    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        UPDATE rd
        SET solicitante=%s,
            funcionario=%s,
            data=%s,
            centro_custo=%s,
            valor=%s,
            arquivos=%s,
            observacao=%s,
            unidade_negocio=%s,
            data_ultima_operacao=%s
        WHERE id=%s
    """, (solicitante, funcionario, data, centro_custo, valor,
          arquivos_str, observacao, unidade_negocio, current_date, id))
    
    if is_solicitante() and original_status == 'Fechamento Recusado':
        cursor.execute("UPDATE rd SET status='Fechamento Solicitado', motivo_recusa=NULL, data_ultima_operacao=%s WHERE id=%s", (current_date, id))
    
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD atualizada com sucesso.")
    return redirect(url_for('index'))

# Rota para aprovação e liberação
@app.route('/approve/<id>', methods=['POST'])
def approve(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional, tipo, valor_liberado FROM rd WHERE id=%s", (id,))
    rd_info = cursor.fetchone()
    if not rd_info:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    status, valor, valor_adic, rd_tipo, valor_liberado = rd_info
    if not can_approve(status):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for('index'))

    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if status == 'Pendente' and is_gestor():
        new_status = 'Aprovado'
        cursor.execute("""
            UPDATE rd SET status=%s, aprovado_data=%s, data_ultima_operacao=%s
            WHERE id=%s
        """, (new_status, current_date, current_date, id))

    elif status == 'Aprovado' and is_financeiro():
        valor_total = (valor or 0) + (valor_adic or 0)
        saldo_atual = get_saldo_global()

        if valor_total > saldo_atual:
            conn.close()
            flash("Saldo global insuficiente para liberar.")
            return redirect(url_for('index'))

        set_saldo_global(saldo_atual - valor_total)
        if rd_tipo.lower() == 'reembolso':
            new_status = 'Fechado'
            cursor.execute("""
                UPDATE rd
                SET status=%s, data_fechamento=%s, valor_liberado=%s, data_ultima_operacao=%s
                WHERE id=%s
            """, (new_status, current_date, valor_total, current_date, id))
        else:
            new_status = 'Liberado'
            cursor.execute("""
                UPDATE rd
                SET status=%s, liberado_data=%s, valor_liberado=%s, data_ultima_operacao=%s
                WHERE id=%s
            """, (new_status, current_date, valor_total, current_date, id))

    elif status == 'Fechamento Solicitado' and is_gestor():
        cursor.execute("SELECT saldo_devolver FROM rd WHERE id=%s", (id,))
        row_saldo_dev = cursor.fetchone()
        saldo_dev = row_saldo_dev[0] if row_saldo_dev else 0
        if saldo_dev and saldo_dev > 0:
            saldo_atual = get_saldo_global()
            set_saldo_global(saldo_atual + saldo_dev)

        new_status = 'Saldo Devolver'
        cursor.execute("UPDATE rd SET status=%s, data_ultima_operacao=%s WHERE id=%s", (new_status, current_date, id))

    else:
        conn.close()
        flash("Não é possível aprovar/liberar esta RD.")
        return redirect(url_for('index'))

    conn.commit()
    cursor.close()
    conn.close()
    flash("Operação realizada com sucesso.")
    return redirect(url_for('index'))

# Rota para exclusão de RD
@app.route('/delete/<id>', methods=['POST'])
def delete_rd(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT solicitante, status, valor_liberado FROM rd WHERE id=%s", (id,))
    rd_data = cursor.fetchone()
    if not rd_data:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    rd_solicitante, rd_status, rd_liberado = rd_data
    if not can_delete(rd_status, rd_solicitante):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for('index'))

    if rd_status == 'Liberado' and rd_liberado and rd_liberado > 0:
        saldo = get_saldo_global()
        set_saldo_global(saldo + rd_liberado)

    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    arquivos_str = cursor.fetchone()[0]
    if arquivos_str:
        for arq in arquivos_str.split(','):
            delete_file_from_r2(arq)

    cursor.execute("DELETE FROM rd WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD excluída com sucesso.")
    return redirect(url_for('index'))

# Rota para crédito adicional (registro individual)
@app.route('/adicional_submit/<id>', methods=['POST'])
def adicional_submit(id):
    if 'arquivo' in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        row = cursor.fetchone()
        arquivos_atuais = row[0].split(',') if (row and row[0]) else []
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                upload_file_to_r2(file, filename)
                arquivos_atuais.append(filename)
        arquivos_atuais_str = ','.join(arquivos_atuais) if arquivos_atuais else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (arquivos_atuais_str, id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        novo_valor_adicional = float(request.form['valor_adicional'].replace(',', '.'))
    except (ValueError, KeyError):
        flash("Valor adicional inválido.")
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO creditos_adicionais (rd_id, valor) VALUES (%s, %s)", (id, novo_valor_adicional))
    conn.commit()
    cursor.execute("SELECT SUM(valor) FROM creditos_adicionais WHERE rd_id=%s", (id,))
    total_adicionais = cursor.fetchone()[0] or 0
    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("UPDATE rd SET valor_adicional=%s, data_ultima_operacao=%s WHERE id=%s", (total_adicionais, current_date, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Crédito adicional registrado individualmente. A RD voltou para 'Pendente'.")
    return redirect(url_for('index'))

# Rota para fechamento da RD (direciona para 'Saldo Devolver')
@app.route('/fechamento_submit/<id>', methods=['POST'])
def fechamento_submit(id):
    if 'arquivo' in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        row = cursor.fetchone()
        arquivos_atuais = row[0].split(',') if (row and row[0]) else []
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                upload_file_to_r2(file, filename)
                arquivos_atuais.append(filename)
        arquivos_str = ','.join(arquivos_atuais) if arquivos_atuais else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (arquivos_str, id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        valor_despesa = float(request.form['valor_despesa'].replace(',', '.'))
    except (ValueError, KeyError):
        flash("Valor da despesa inválido.")
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT valor, valor_adicional, status FROM rd WHERE id=%s", (id,))
    rd_info = cursor.fetchone()
    if not rd_info:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    valor_solicitado, valor_adicional, status_atual = rd_info
    total_credit = valor_solicitado + (valor_adicional or 0)
    if total_credit < valor_despesa:
        conn.close()
        flash("Valor da despesa maior que o total de créditos solicitados.")
        return redirect(url_for('index'))
    saldo_devolver = total_credit - valor_despesa
    data_fech = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        UPDATE rd
        SET valor_despesa=%s, saldo_devolver=%s, data_fechamento=%s,
            status='Saldo Devolver', data_ultima_operacao=%s
        WHERE id=%s
    """, (valor_despesa, saldo_devolver, data_fech, data_fech, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento solicitado. RD está na aba 'Saldo Devolver'.")
    return redirect(url_for('index'))

# Rota para confirmar a devolução (muda status para 'Fechado')
@app.route('/saldo_devolvido/<id>', methods=['POST'])
def saldo_devolvido(id):
    if not (is_financeiro() or is_gestor()):
        flash("Ação não permitida.")
        return redirect(url_for('index'))
    conn = get_pg_connection()
    cursor = conn.cursor()
    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("UPDATE rd SET status='Fechado', data_fechamento=%s, data_ultima_operacao=%s WHERE id=%s", (current_date, current_date, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Saldo devolvido. RD fechada.")
    return redirect(url_for('index'))

# Rota para o supervisor editar arquivos (somente se RD estiver 'Liberado')
@app.route('/supervisor_edit/<id>', methods=['GET', 'POST'])
def supervisor_edit(id):
    if not is_supervisor():
        flash("Acesso negado.")
        return redirect(url_for('index'))
    
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    
    if rd[6] != 'Liberado':
        flash("Somente RDs liberadas podem ser editadas pelo supervisor.")
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        arquivos = rd[12].split(',') if rd[12] else []
        if 'arquivo' in request.files:
            for file in request.files.getlist('arquivo'):
                if file.filename:
                    filename = f"{id}_{file.filename}"
                    upload_file_to_r2(file, filename)
                    arquivos.append(filename)
        arquivos_str = ','.join(arquivos) if arquivos else None
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("UPDATE rd SET arquivos=%s, data_ultima_operacao=%s WHERE id=%s", (arquivos_str, current_date, id))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Arquivos atualizados com sucesso.")
        return redirect(url_for('index'))
    
    cursor.close()
    conn.close()
    return render_template('supervisor_edit.html', rd=rd)

# Rota para registrar anexo divergente (para gestor ou solicitante)
@app.route('/anexo_divergente/<id>', methods=['GET', 'POST'])
def anexo_divergente(id):
    if session.get('user_role') not in ['gestor', 'solicitante']:
        flash("Ação não permitida.")
        return redirect(url_for('index'))
    
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        motivo = request.form.get('motivo', '').strip()
        if not motivo:
            flash("Informe um motivo para a divergência.")
            return redirect(url_for('anexo_divergente', id=id))
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            UPDATE rd
            SET motivo_anexo_divergente=%s, data_divergencia=%s, status='Anexo Divergente', data_ultima_operacao=%s
            WHERE id=%s
        """, (motivo, current_date, current_date, id))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Anexo marcado como divergente.")
        return redirect(url_for('index'))
    
    cursor.close()
    conn.close()
    return render_template('anexo_divergente.html', rd=rd)

def can_edit_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return can_edit(row[0])

def can_request_additional_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    st = cursor.fetchone()
    conn.close()
    if not st:
        return False
    return can_request_additional(st[0])

def can_close_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    st = cursor.fetchone()
    conn.close()
    if not st:
        return False
    return can_close(st[0])

@app.route('/export_excel', methods=['GET'])
def export_excel():
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd ORDER BY id ASC")
    rd_list = cursor.fetchall()
    saldo_global = get_saldo_global()
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet('Relatorio')

    header = [
      "Número RD", "Data Solicitação", "Solicitante", "Funcionário", "Valor Solicitado",
      "Valor Adicional", "Data do Adicional", "Centro de Custo", "Valor Gasto", "Saldo a Devolver",
      "Data de Fechamento", "Saldo Global"
    ]
    for col, h in enumerate(header):
        worksheet.write(0, col, h)

    rownum = 1
    for rd_row in rd_list:
        rd_id = rd_row[0]
        rd_data = rd_row[3]
        rd_solic = rd_row[1]
        rd_func = rd_row[2]
        rd_valor = rd_row[5]
        rd_valor_adic = rd_row[7]
        rd_adic_data = rd_row[8]
        rd_ccusto = rd_row[4]
        rd_desp = rd_row[9]
        rd_saldo_dev = rd_row[10]
        rd_data_fech = rd_row[11]

        worksheet.write(rownum, 0, rd_id)
        worksheet.write(rownum, 1, str(rd_data))
        worksheet.write(rownum, 2, rd_solic)
        worksheet.write(rownum, 3, rd_func)
        worksheet.write(rownum, 4, float(rd_valor or 0))
        worksheet.write(rownum, 5, float(rd_valor_adic or 0))
        worksheet.write(rownum, 6, str(rd_adic_data or ''))
        worksheet.write(rownum, 7, rd_ccusto)
        worksheet.write(rownum, 8, float(rd_desp or 0))
        worksheet.write(rownum, 9, float(rd_saldo_dev or 0))
        worksheet.write(rownum, 10, str(rd_data_fech or ''))
        worksheet.write(rownum, 11, float(saldo_global))
        rownum += 1

    workbook.close()
    output.seek(0)
    conn.close()

    return send_file(
        output,
        as_attachment=True,
        download_name=f"Relatorio_RD_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/logout')
def logout():
    session.clear()
    flash("Logout realizado com sucesso.")
    return redirect(url_for('index'))

# Rotas para Funcionários
@app.route('/cadastro_funcionario', methods=['GET'])
def cadastro_funcionario():
    return render_template('cadastro_funcionario.html')

@app.route('/cadastrar_funcionario', methods=['POST'])
def cadastrar_funcionario():
    nome = request.form['nome'].strip()
    centro_custo = request.form['centroCusto'].strip()
    unidade_negocio = request.form['unidadeNegocio'].strip()

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO funcionarios (nome, centro_custo, unidade_negocio)
        VALUES (%s, %s, %s)
    """, (nome, centro_custo, unidade_negocio))
    conn.commit()
    cursor.close()
    conn.close()

    flash("Funcionário cadastrado com sucesso!")
    return redirect(url_for('cadastro_funcionario'))

@app.route('/consulta_funcionario', methods=['GET'])
def consulta_funcionario():
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, centro_custo, unidade_negocio FROM funcionarios")
    funcionarios = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('consulta_funcionario.html', funcionarios=funcionarios)

@app.route('/editar_funcionario/<int:id>', methods=['GET', 'POST'])
def editar_funcionario(id):
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        centro_custo = request.form['centroCusto'].strip()
        unidade_negocio = request.form['unidadeNegocio'].strip()
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE funcionarios
            SET nome=%s, centro_custo=%s, unidade_negocio=%s
            WHERE id=%s
        """, (nome, centro_custo, unidade_negocio, id))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Funcionário atualizado com sucesso!")
        return redirect(url_for('consulta_funcionario'))
    else:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, nome, centro_custo, unidade_negocio FROM funcionarios WHERE id=%s", (id,))
        funcionario = cursor.fetchone()
        cursor.close()
        conn.close()
        return render_template('editar_funcionario.html', funcionario=funcionario)

@app.route('/consulta_rd/<int:id_func>', methods=['GET'])
def consulta_rd(id_func):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT nome FROM funcionarios WHERE id = %s", (id_func,))
    row = cursor.fetchone()
    funcionario_nome = row[0] if row else "Desconhecido"
    
    query = """
        SELECT id, data, valor, status 
        FROM rd
        WHERE funcionario = (
            SELECT nome FROM funcionarios WHERE id = %s
        )
        AND status != 'Fechado'
        ORDER BY data DESC
    """
    cursor.execute(query, (id_func,))
    rd_list = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('listagem_rds.html', rd_list=rd_list, funcionario_nome=funcionario_nome)

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
