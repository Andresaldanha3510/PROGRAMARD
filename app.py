from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Imports para Cloudflare R2
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

# Imports para Excel
import io
import xlsxwriter
import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
# Disponibiliza funções úteis para os templates
app.jinja_env.globals.update(
    get_r2_public_url=get_r2_public_url,
    is_gestor=lambda: session.get('user_role') == 'gestor',
    is_solicitante=lambda: session.get('user_role') == 'solicitante',
    is_financeiro=lambda: session.get('user_role') == 'financeiro'
)

secret_key = os.getenv('SECRET_KEY', 'secret123')
app.secret_key = secret_key
logging.debug("SECRET_KEY carregado corretamente.")

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

    # Cria tabela RD se não existir
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
        observacao TEXT
    );
    """
    cursor.execute(create_rd_table)

    # Garante que as colunas extras existam
    for col_name, alter_cmd in [
        ("valor_liberado", "ALTER TABLE rd ADD COLUMN valor_liberado NUMERIC(15,2) DEFAULT 0;"),
        ("observacao", "ALTER TABLE rd ADD COLUMN observacao TEXT;"),
        ("tipo", "ALTER TABLE rd ADD COLUMN tipo TEXT DEFAULT 'credito alelo';"),
        ("data_saldo_devolvido", "ALTER TABLE rd ADD COLUMN data_saldo_devolvido DATE;"),
        ("unidade_negocio", "ALTER TABLE rd ADD COLUMN unidade_negocio TEXT;"),
        ("motivo_recusa", "ALTER TABLE rd ADD COLUMN motivo_recusa TEXT;"),
        ("adicionais_individuais", "ALTER TABLE rd ADD COLUMN adicionais_individuais TEXT;")
    ]:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='rd' AND column_name=%s;", 
            (col_name,)
        )
        if not cursor.fetchone():
            cursor.execute(alter_cmd)

    # Cria tabela para saldo global
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

    # Cria tabela de Funcionários para cadastro padronizado
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

def can_add():
    return user_role() in ['solicitante', 'gestor', 'financeiro']

def can_edit(status):
    if status == 'Fechado':
        return False
    if is_solicitante():
        # Permite editar se estiver em 'Pendente' ou 'Fechamento Recusado'
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

# Rota principal (login e exibição dos RDs)
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
    cursor.execute("SELECT * FROM rd WHERE status='Fechamento Solicitado'")
    fechamento_solicitado = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Fechamento Recusado'")
    fechamento_recusado = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Fechado'")
    fechados = cursor.fetchall()
    saldo_global = get_saldo_global()
    adicional_id = request.args.get('adicional')
    fechamento_id = request.args.get('fechamento')
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
        fechamento_solicitado=fechamento_solicitado,
        fechamento_recusado=fechamento_recusado,
        fechados=fechados,
        can_add=can_add(),
        can_delete_func=can_delete,
        can_edit_func=can_edit,
        can_approve_func=can_approve,
        can_request_additional=can_request_additional,
        can_close=can_close,
        adicional_id=adicional_id,
        fechamento_id=fechamento_id
    )

# Rotas de gerenciamento de RDs (add, edit, approve, delete, etc.) permanecem conforme o código original

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
    cursor.execute("""
        INSERT INTO rd (
            id, solicitante, funcionario, data, centro_custo,
            valor, status, arquivos, valor_liberado, observacao,
            tipo, unidade_negocio
        )
        VALUES (%s, %s, %s, %s, %s,
                %s, %s, %s, 0, %s,
                %s, %s)
    """, (custom_id, solicitante, funcionario, data, centro_custo,
          valor, 'Pendente', arquivos_str, observacao,
          rd_tipo, unidade_negocio))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD adicionada com sucesso.")
    return redirect(url_for('index'))

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
    """, (solicitante, funcionario, data, centro_custo, valor,
          arquivos_str, observacao, unidade_negocio, id))
    
    if is_solicitante() and original_status == 'Fechamento Recusado':
        cursor.execute("UPDATE rd SET status='Fechamento Solicitado', motivo_recusa=NULL WHERE id=%s", (id,))
    
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD atualizada com sucesso.")
    return redirect(url_for('index'))

@app.route('/approve/<id>', methods=['POST'])
def approve(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional, tipo FROM rd WHERE id=%s", (id,))
    rd_info = cursor.fetchone()
    if not rd_info:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    status, valor, valor_adic, rd_tipo = rd_info
    if not can_approve(status):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for('index'))

    current_date = datetime.now().strftime('%Y-%m-%d')

    if status == 'Pendente' and is_gestor():
        new_status = 'Aprovado'
        cursor.execute("""
            UPDATE rd SET status=%s, aprovado_data=%s
            WHERE id=%s
        """, (new_status, current_date, id))
    elif status == 'Aprovado' and is_financeiro():
        if rd_tipo.lower() == 'reembolso':
            new_status = 'Fechado'
            cursor.execute("""
                UPDATE rd SET status=%s, data_fechamento=%s
                WHERE id=%s
            """, (new_status, current_date, id))
        else:
            new_status = 'Liberado'
            total_credit = valor + (valor_adic or 0)
            cursor.execute("""
                UPDATE rd SET status=%s, liberado_data=%s
                WHERE id=%s
            """, (new_status, current_date, id))
    elif status == 'Fechamento Solicitado' and is_gestor():
        new_status = 'Fechado'
        cursor.execute("UPDATE rd SET status=%s WHERE id=%s", (new_status, id))
    else:
        conn.close()
        flash("Não é possível aprovar/liberar esta RD.")
        return redirect(url_for('index'))

    conn.commit()
    cursor.close()
    conn.close()
    flash("Operação realizada com sucesso.")
    return redirect(url_for('index'))

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
    cursor.execute("SELECT status, valor_adicional, adicionais_individuais, valor FROM rd WHERE id=%s", (id,))
    rd_status = cursor.fetchone()
    if not rd_status:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status_atual, valor_adic_atual, adicionais_individuais, valor_solicitado = rd_status
    if not can_request_additional(status_atual):
        conn.close()
        flash("Não é possível solicitar adicional agora.")
        return redirect(url_for('index'))

    novo_total = (valor_adic_atual or 0) + novo_valor_adicional
    if adicionais_individuais:
        additional_entries = [x.strip() for x in adicionais_individuais.split(',')]
        next_index = len(additional_entries) + 1
        novos_adicionais = adicionais_individuais + f", Adicional {next_index}:{novo_valor_adicional}"
    else:
        novos_adicionais = f"Adicional 1:{novo_valor_adicional}"
    
    add_data = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        UPDATE rd
        SET valor_adicional=%s, adicional_data=%s, status='Pendente', adicionais_individuais=%s
        WHERE id=%s
    """, (novo_total, add_data, novos_adicionais, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Crédito adicional solicitado. A RD voltou para 'Pendente'.")
    return redirect(url_for('index'))

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
    if not can_close(status_atual):
        conn.close()
        flash("Não é possível fechar esta RD agora.")
        return redirect(url_for('index'))
    total_credit = valor_solicitado + (valor_adicional or 0)
    if total_credit < valor_despesa:
        conn.close()
        flash("Valor da despesa maior que o total de créditos solicitados.")
        return redirect(url_for('index'))
    saldo_devolver = total_credit - valor_despesa
    data_fech = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        UPDATE rd
        SET valor_despesa=%s, saldo_devolver=%s, data_fechamento=%s,
            status='Fechamento Solicitado'
        WHERE id=%s
    """, (valor_despesa, saldo_devolver, data_fech, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento solicitado. Aguarde aprovação do gestor.")
    return redirect(url_for('index'))

@app.route('/reject_fechamento/<id>', methods=['POST'])
def reject_fechamento(id):
    if not is_gestor():
        flash("Acesso negado.")
        return redirect(url_for('index'))
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row or row[0] != 'Fechamento Solicitado':
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for('index'))
    
    motivo = request.form.get('motivo', '').strip()
    if not motivo:
        flash("Informe um motivo para a recusa.")
        return redirect(url_for('index'))
    
    cursor.execute("""
        UPDATE rd 
        SET status='Fechamento Recusado', motivo_recusa=%s 
        WHERE id=%s
    """, (motivo, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento recusado com sucesso.")
    return redirect(url_for('index'))

@app.route('/reenviar_fechamento/<id>', methods=['POST'])
def reenviar_fechamento(id):
    flash("Utilize o botão 'Corrigir e reenviar' para editar a RD.")
    return redirect(url_for('index'))

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

@app.route('/delete_file/<id>', methods=['POST'])
def delete_file(id):
    filename = request.form.get('filename')
    if not filename:
        flash("Nenhum arquivo para excluir.")
        return redirect(request.referrer or url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos, status, solicitante FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(request.referrer or url_for('index'))

    arquivos_str, rd_status, rd_solic = row
    if not arquivos_str:
        conn.close()
        flash("Nenhum arquivo na RD.")
        return redirect(request.referrer or url_for('index'))

    if not (can_edit(rd_status) or can_delete(rd_status, rd_solic)):
        conn.close()
        flash("Você não pode excluir arquivos desta RD.")
        return redirect(request.referrer or url_for('index'))

    arqs = arquivos_str.split(',')
    if filename not in arqs:
        conn.close()
        flash("Arquivo não pertence a esta RD.")
        return redirect(request.referrer or url_for('index'))

    delete_file_from_r2(filename)
    arqs.remove(filename)
    updated = ','.join(arqs) if arqs else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (updated, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Arquivo excluído com sucesso.")
    return redirect(request.referrer or url_for('index'))

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

@app.route('/registrar_saldo_devolvido/<id>', methods=['POST'])
def registrar_saldo_devolvido(id):
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT valor, valor_adicional, valor_despesa, data_saldo_devolvido FROM rd WHERE id=%s", (id,))
    rd_info = cursor.fetchone()
    if not rd_info:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    valor, valor_adic, valor_desp, data_sal_dev = rd_info
    if data_sal_dev:
        conn.close()
        flash("Saldo já registrado antes.")
        return redirect(url_for('index'))
    total_credit = valor + (valor_adic or 0)
    if total_credit < (valor_desp or 0):
        conn.close()
        flash("Despesa maior que o total de créditos.")
        return redirect(url_for('index'))
    saldo_devolver = total_credit - (valor_desp or 0)
    saldo = get_saldo_global()
    set_saldo_global(saldo + saldo_devolver)
    current_date = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("UPDATE rd SET data_saldo_devolvido=%s WHERE id=%s", (current_date, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash(f"Saldo devolvido com sucesso. Valor = R${saldo_devolver:,.2f}")
    return redirect(url_for('index'))

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

# -------------------------------------------------
# Novas Rotas para Funcionários

# Tela de cadastro de funcionários
@app.route('/cadastro_funcionario', methods=['GET'])
def cadastro_funcionario():
    return render_template('cadastro_funcionario.html')

# Processa o cadastro do funcionário
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

# Tela de consulta de funcionários
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





# -------------------------------------------------
# Nova Rota: Consulta de RDs (não fechados) para um Funcionário
@app.route('/consulta_rd/<int:id_func>', methods=['GET'])
def consulta_rd(id_func):
    conn = get_pg_connection()
    cursor = conn.cursor()
    # Busca o nome do funcionário para exibir no template
    cursor.execute("SELECT nome FROM funcionarios WHERE id = %s", (id_func,))
    row = cursor.fetchone()
    funcionario_nome = row[0] if row else "Desconhecido"
    
    # Busca os RDs que não estão fechados para este funcionário
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
