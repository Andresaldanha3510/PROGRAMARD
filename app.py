from flask import Flask, render_template, request, redirect, url_for, session, abort, flash, send_file
import psycopg2
from psycopg2 import sql
import os
from datetime import datetime
import logging
import io
import xlsxwriter
from dotenv import load_dotenv

# ---- INÍCIO: Imports e variáveis para o Cloudflare R2 ----
import boto3
from botocore.client import Config

load_dotenv()  # Carrega as variáveis do arquivo .env

R2_ACCESS_KEY = os.getenv('R2_ACCESS_KEY', 'your_r2_access_key')
R2_SECRET_KEY = os.getenv('R2_SECRET_KEY', 'your_r2_secret_key')
R2_ENDPOINT   = 'https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com'
R2_BUCKET_NAME = 'meu-bucket-r2'

def upload_file_to_r2(local_file_path, object_name):
    """Envia um arquivo local para o Bucket R2, usando boto3."""
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4')
    )
    s3.upload_file(local_file_path, R2_BUCKET_NAME, object_name)

def delete_file_from_r2(object_name):
    """Exclui um arquivo do Bucket R2, usando boto3."""
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4')
    )
    s3.delete_object(Bucket=R2_BUCKET_NAME, Key=object_name)
# ---- FIM: Imports e variáveis para o Cloudflare R2 ----

# Se o bucket for realmente público, seu link seria algo como:
# f"{R2_ENDPOINT}/{R2_BUCKET_NAME}/{nome_do_arquivo}"
#
# Se ele for privado, você pode gerar Presigned URL. Exemplo:
# def generate_presigned_url(object_name, expires_in=3600):
#     s3 = boto3.client(
#         's3',
#         endpoint_url=R2_ENDPOINT,
#         aws_access_key_id=R2_ACCESS_KEY,
#         aws_secret_access_key=R2_SECRET_KEY,
#         config=Config(signature_version='s3v4')
#     )
#     return s3.generate_presigned_url(
#         'get_object',
#         Params={'Bucket': R2_BUCKET_NAME, 'Key': object_name},
#         ExpiresIn=expires_in
#     )

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

# Configuração do SECRET_KEY
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    raise ValueError("SECRET_KEY não está definida no ambiente.")
app.secret_key = secret_key
logging.debug(f"SECRET_KEY carregado corretamente.")

# Definimos uma pasta local temporária (efêmera) para upload antes de enviar ao R2
# No Render, esse local não é persistente, mas funciona para upload em tempo de execução.
TEMP_FOLDER = os.path.join(os.getcwd(), 'temp_uploads')
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

# Configurações do PostgreSQL
PG_HOST = os.getenv('PG_HOST', '...')
PG_PORT = os.getenv('PG_PORT', '5432')
PG_DB = os.getenv('PG_DB', 'programard_db')
PG_USER = os.getenv('PG_USER', 'programard_db_user')
PG_PASSWORD = os.getenv('PG_PASSWORD', '...')

import sys
import psycopg2

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
        sys.exit(1)

def init_db():
    """Inicializa o banco no PostgreSQL, criando tabelas (se não existirem)"""
    conn = get_pg_connection()
    cursor = conn.cursor()

    create_rd_table = """
    CREATE TABLE IF NOT EXISTS rd (
        id TEXT PRIMARY KEY,
        solicitante TEXT NOT NULL,
        funcionario TEXT NOT NULL,
        data DATE NOT NULL,
        centro_custo TEXT NOT NULL,
        valor NUMERIC(15, 2) NOT NULL,
        status TEXT DEFAULT 'Pendente',
        valor_adicional NUMERIC(15, 2) DEFAULT 0,
        adicional_data DATE,
        valor_despesa NUMERIC(15, 2),
        saldo_devolver NUMERIC(15, 2),
        data_fechamento DATE,
        arquivos TEXT,
        aprovado_data DATE,
        liberado_data DATE,
        valor_liberado NUMERIC(15, 2) DEFAULT 0
    );
    """
    cursor.execute(create_rd_table)

    # Verifica se a coluna 'valor_liberado' existe
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'rd' AND column_name = 'valor_liberado';
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN valor_liberado NUMERIC(15, 2) DEFAULT 0;")

    create_saldo_global_table = """
    CREATE TABLE IF NOT EXISTS saldo_global (
        id SERIAL PRIMARY KEY,
        saldo NUMERIC(15, 2) DEFAULT 30000
    );
    """
    cursor.execute(create_saldo_global_table)

    cursor.execute("SELECT COUNT(*) FROM saldo_global")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO saldo_global (saldo) VALUES (30000)")

    conn.commit()
    cursor.close()
    conn.close()

from datetime import datetime

def generate_custom_id():
    current_year = datetime.now().year % 100
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM rd
        WHERE split_part(id, '.', 2)::INTEGER = %s
        ORDER BY (split_part(id, '.', 1))::INTEGER DESC LIMIT 1
    """, (current_year,))
    last_id = cursor.fetchone()
    conn.close()

    if not last_id:
        return f"400.{current_year}"

    last_str = last_id[0]
    last_number_str, last_year_str = last_str.split('.')
    last_number = int(last_number_str)
    return f"{last_number + 1}.{current_year}"

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
    return (is_gestor() or is_financeiro())

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
    if status == 'Aprovado' and is_financeiro():
        return True
    return False

def can_request_additional(status):
    return is_solicitante() and status == 'Liberado'

def can_close(status):
    return is_solicitante() and status == 'Liberado'

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
    cursor.execute('UPDATE saldo_global SET saldo = %s WHERE id = 1', (novo_saldo,))
    conn.commit()
    conn.close()

def format_currency(value):
    formatted = f"{value:,.2f}"
    parts = formatted.split('.')
    left = parts[0].replace(',', '.')
    right = parts[1]
    return f"{left},{right}"

from flask import request, render_template, redirect, url_for, flash

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if username == 'gestor' and password == '115289':
            session['user_role'] = 'gestor'
            flash('Login como gestor bem-sucedido.')
        elif username == 'financeiro' and password == '351073':
            session['user_role'] = 'financeiro'
            flash('Login como financeiro bem-sucedido.')
        elif username == 'solicitante' and password == '102030':
            session['user_role'] = 'solicitante'
            flash('Login como solicitante bem-sucedido.')
        else:
            flash('Credenciais inválidas.')
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

    cursor.execute("SELECT * FROM rd WHERE status='Fechado'")
    fechados = cursor.fetchall()

    saldo_global = get_saldo_global()
    adicional_id = request.args.get('adicional')
    fechamento_id = request.args.get('fechamento')

    conn.close()

    return render_template('index.html',
                           pendentes=pendentes,
                           aprovados=aprovados,
                           liberados=liberados,
                           fechados=fechados,
                           user_role=user_role(),
                           can_add=can_add(),
                           saldo_global=saldo_global if is_financeiro() else None,
                           adicional_id=adicional_id,
                           fechamento_id=fechamento_id,
                           can_delete_func=can_delete,
                           can_edit_func=can_edit,
                           can_approve_func=can_approve,
                           can_request_additional=can_request_additional,
                           can_close=can_close,
                           is_solicitante=is_solicitante(),
                           format_currency=format_currency)

@app.route('/add', methods=['POST'])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado", 403

    solicitante = request.form['solicitante'].strip()
    funcionario = request.form['funcionario'].strip()
    data = request.form['data'].strip()
    centro_custo = request.form['centro_custo'].strip()
    try:
        valor = float(request.form['valor'])
    except ValueError:
        flash('Valor inválido.')
        return redirect(url_for('index'))
    custom_id = generate_custom_id()

    # Gerencia arquivos: agora só salva localmente de forma TEMPORÁRIA (em TEMP_FOLDER)
    # depois do upload para R2, exclui localmente.
    arquivos = []
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{custom_id}_{file.filename}"
                local_path = os.path.join(TEMP_FOLDER, filename)
                file.save(local_path)
                # Envia para R2
                upload_file_to_r2(local_path, filename)
                # Remove arquivo local
                os.remove(local_path)
                # Armazena o nome do arquivo (que está agora no R2)
                arquivos.append(filename)
    arquivos_str = ','.join(arquivos) if arquivos else None

    # Insere no BD
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO rd (
            id, solicitante, funcionario, data, centro_custo, valor, status, arquivos, valor_liberado
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)
    ''', (custom_id, solicitante, funcionario, data, centro_custo, valor, 'Pendente', arquivos_str))
    conn.commit()
    cursor.close()
    conn.close()
    flash('RD adicionada com sucesso.')
    return redirect(url_for('index'))

@app.route('/edit_form/<id>', methods=['GET'])
def edit_form(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()

    if not rd:
        flash('RD não encontrada.')
        return "RD não encontrada", 404
    status = rd[6]  # coluna 'status'
    if not can_edit(status):
        flash('Acesso negado.')
        return "Acesso negado", 403

    return render_template('edit_form.html', rd=rd)

@app.route('/edit_submit/<id>', methods=['POST'])
def edit_submit(id):
    if not can_edit_status(id):
        flash('Acesso negado.')
        return "Acesso negado", 403

    solicitante = request.form['solicitante'].strip()
    funcionario = request.form['funcionario'].strip()
    data = request.form['data'].strip()
    centro_custo = request.form['centro_custo'].strip()
    try:
        valor = float(request.form['valor'])
    except ValueError:
        flash('Valor inválido.')
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    arquivos = rd[0].split(',') if (rd and rd[0]) else []

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                local_path = os.path.join(TEMP_FOLDER, filename)
                file.save(local_path)
                upload_file_to_r2(local_path, filename)
                os.remove(local_path)
                arquivos.append(filename)

    arquivos_str = ','.join(arquivos) if arquivos else None

    cursor.execute('''
        UPDATE rd
        SET solicitante=%s, funcionario=%s, data=%s, centro_custo=%s, valor=%s, arquivos=%s
        WHERE id=%s
    ''', (solicitante, funcionario, data, centro_custo, valor, arquivos_str, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash('RD atualizada com sucesso.')
    return redirect(url_for('index'))

def can_edit_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    status = rd[0]
    return can_edit(status)

@app.route('/approve/<id>', methods=['POST'])
def approve(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional, valor_liberado FROM rd WHERE id=%s", (id,))
    rd_info = cursor.fetchone()
    if not rd_info:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    status, valor, valor_adic, valor_liberado = rd_info
    if not can_approve(status):
        conn.close()
        flash("Acesso negado.")
        return redirect(url_for('index'))

    current_date = datetime.now().strftime('%Y-%m-%d')

    if status == 'Pendente' and is_gestor():
        new_status = 'Aprovado'
        cursor.execute(
            "UPDATE rd SET status=%s, aprovado_data=%s WHERE id=%s",
            (new_status, current_date, id)
        )
    elif status == 'Aprovado' and is_financeiro():
        new_status = 'Liberado'
        valor_total = valor + (valor_adic or 0)
        falta_liberar = valor_total - (valor_liberado or 0)

        if falta_liberar > 0:
            saldo = get_saldo_global()
            if falta_liberar > saldo:
                conn.close()
                flash('Saldo global insuficiente para liberar a diferença adicional.')
                return redirect(url_for('index'))
            set_saldo_global(saldo - falta_liberar)
            valor_liberado = valor_total

        cursor.execute(
            "UPDATE rd SET status=%s, liberado_data=%s, valor_liberado=%s WHERE id=%s",
            (new_status, current_date, valor_liberado, id)
        )
    else:
        conn.close()
        flash("Não é possível aprovar/liberar esta RD.")
        return redirect(url_for('index'))

    conn.commit()
    conn.close()
    flash('Operação realizada com sucesso.')
    return redirect(url_for('index'))

@app.route('/delete/<id>', methods=['POST'])
def delete_rd(id):
    """Se RD estava Liberado, devolve ao saldo o valor_liberado (que já saiu do caixa)."""
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT solicitante, status, valor_liberado FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    rd_solicitante, rd_status, rd_liberado = rd
    if not can_delete(rd_status, rd_solicitante):
        conn.close()
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Se RD estava 'Liberado', devolvemos o que foi liberado ao saldo global
    if rd_status == 'Liberado' and rd_liberado and rd_liberado > 0:
        saldo = get_saldo_global()
        set_saldo_global(saldo + rd_liberado)

    # Exclui arquivos associados no R2
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    arquivos = cursor.fetchone()[0]
    if arquivos:
        for arquivo in arquivos.split(','):
            # Remove do R2
            delete_file_from_r2(arquivo)

    # Deleta do BD
    cursor.execute("DELETE FROM rd WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('RD excluída com sucesso.')
    return redirect(url_for('index'))

@app.route('/adicional_submit/<id>', methods=['POST'])
def adicional_submit(id):
    if not can_request_additional_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Se houver arquivos, faz upload para R2
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    rd_atual = cursor.fetchone()
    arquivos_atuais = rd_atual[0].split(',') if (rd_atual and rd_atual[0]) else []

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                local_path = os.path.join(TEMP_FOLDER, filename)
                file.save(local_path)
                upload_file_to_r2(local_path, filename)
                os.remove(local_path)
                arquivos_atuais.append(filename)

    arquivos_atuais_str = ','.join(arquivos_atuais) if arquivos_atuais else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (arquivos_atuais_str, id))
    conn.commit()
    cursor.close()
    conn.close()

    # Valor adicional
    try:
        valor_adicional_novo = float(request.form['valor_adicional'])
    except (ValueError, KeyError):
        flash('Valor adicional inválido.')
        return redirect(url_for('index'))

    # Atualiza RD -> status Pendente
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor_adicional FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status_atual, valor_adic_atual = rd

    if not can_request_additional(status_atual):
        conn.close()
        flash("Não é possível solicitar adicional neste momento.")
        return redirect(url_for('index'))

    novo_valor_adic = (valor_adic_atual or 0) + valor_adicional_novo
    adicional_data = datetime.now().strftime('%Y-%m-%d')

    cursor.execute("""
        UPDATE rd
        SET valor_adicional=%s, adicional_data=%s, status='Pendente'
        WHERE id=%s
    """, (novo_valor_adic, adicional_data, id))
    conn.commit()
    cursor.close()
    conn.close()

    flash('Crédito adicional solicitado com sucesso (sem devolver saldo).')
    return redirect(url_for('index'))

def can_request_additional_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    status = rd[0]
    return can_request_additional(status)

@app.route('/fechamento_submit/<id>', methods=['POST'])
def fechamento_submit(id):
    if not can_close_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Se houver arquivos, faz upload ao R2
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    rd_atual = cursor.fetchone()
    arquivos_atuais = rd_atual[0].split(',') if (rd_atual and rd_atual[0]) else []

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                local_path = os.path.join(TEMP_FOLDER, filename)
                file.save(local_path)
                upload_file_to_r2(local_path, filename)
                os.remove(local_path)
                arquivos_atuais.append(filename)

    arquivos_atuais_str = ','.join(arquivos_atuais) if arquivos_atuais else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (arquivos_atuais_str, id))
    conn.commit()
    cursor.close()
    conn.close()

    # Captura valor da despesa
    try:
        valor_despesa = float(request.form['valor_despesa'])
    except (ValueError, KeyError):
        flash('Valor da despesa inválido.')
        return redirect(url_for('index'))

    # Busca dados da RD
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor_liberado FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status_atual, valor_liberado = rd

    if not can_close(status_atual):
        conn.close()
        flash("Não é possível fechar esta RD neste momento.")
        return redirect(url_for('index'))

    if valor_liberado < valor_despesa:
        conn.close()
        flash("Valor da despesa maior que o valor liberado.")
        return redirect(url_for('index'))

    # Devolve a diferença ao saldo global
    saldo_devolver = valor_liberado - valor_despesa
    saldo = get_saldo_global()
    set_saldo_global(saldo + saldo_devolver)

    data_fechamento = datetime.now().strftime('%Y-%m-%d')

    cursor.execute("""
        UPDATE rd
        SET valor_despesa=%s, saldo_devolver=%s, data_fechamento=%s, status='Fechado'
        WHERE id=%s
    """, (valor_despesa, saldo_devolver, data_fechamento, id))
    conn.commit()
    cursor.close()
    conn.close()

    flash(f'RD fechada com sucesso. Saldo devolvido = R${saldo_devolver:.2f}')
    return redirect(url_for('index'))

def can_close_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    status = rd[0]
    return can_close(status)

@app.route('/edit_saldo', methods=['POST'])
def edit_saldo():
    if not is_financeiro():
        flash('Acesso negado.')
        return redirect(url_for('index'))

    try:
        novo_saldo = float(request.form['saldo_global'])
    except (ValueError, KeyError):
        flash('Saldo inválido.')
        return redirect(url_for('index'))

    set_saldo_global(novo_saldo)
    flash('Saldo Global atualizado com sucesso.')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Logout realizado com sucesso.')
    return redirect(url_for('index'))

# Removido o send_from_directory e a rota /uploads/<filename>, pois não servimos arquivos localmente.

@app.route('/delete_file/<id>', methods=['POST'])
def delete_file(id):
    """Exclui um arquivo específico do Cloudflare R2 e remove-o da lista 'arquivos' da RD."""
    filename = request.form.get('filename')
    if not filename:
        flash('Nenhum arquivo especificado para exclusão.')
        return redirect(request.referrer or url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd or not rd[0]:
        conn.close()
        flash('RD ou arquivos não encontrados.')
        return redirect(request.referrer or url_for('index'))

    arquivos = rd[0].split(',')
    if filename not in arquivos:
        conn.close()
        flash('Arquivo não encontrado na RD especificada.')
        return redirect(request.referrer or url_for('index'))

    # Remove do R2
    delete_file_from_r2(filename)

    # Atualiza banco
    arquivos.remove(filename)
    updated_arquivos = ','.join(arquivos) if arquivos else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (updated_arquivos, id))
    conn.commit()
    cursor.close()
    conn.close()

    flash('Arquivo excluído com sucesso.')
    return redirect(request.referrer or url_for('index'))

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
        "Número RD",
        "Data Solicitação",
        "Solicitante",
        "Funcionário",
        "Valor Solicitado",
        "Valor Adicional",
        "Data do Adicional",
        "Centro de Custo",
        "Valor Gasto",
        "Saldo a Devolver",
        "Data de Fechamento",
        "Saldo Global"
    ]
    for col, h in enumerate(header):
        worksheet.write(0, col, h)

    row_number = 1
    for rd_row in rd_list:
        # Lembrando a estrutura da tabela:
        # (id, solicitante, funcionario, data, centro_custo, valor, status,
        #  valor_adicional, adicional_data, valor_despesa, saldo_devolver,
        #  data_fechamento, arquivos, aprovado_data, liberado_data, valor_liberado)
        rd_id              = rd_row[0]
        rd_solicitante     = rd_row[1]
        rd_funcionario     = rd_row[2]
        rd_data            = rd_row[3]
        rd_centro_custo    = rd_row[4]
        rd_valor           = rd_row[5]
        rd_valor_adicional = rd_row[7]
        rd_adicional_data  = rd_row[8]
        rd_valor_despesa   = rd_row[9]
        rd_saldo_devolver  = rd_row[10]
        rd_data_fechamento = rd_row[11]

        worksheet.write(row_number, 0, rd_id)
        worksheet.write(row_number, 1, rd_data)
        worksheet.write(row_number, 2, rd_solicitante)
        worksheet.write(row_number, 3, rd_funcionario)
        worksheet.write(row_number, 4, rd_valor)
        worksheet.write(row_number, 5, rd_valor_adicional)
        worksheet.write(row_number, 6, rd_adicional_data)
        worksheet.write(row_number, 7, rd_centro_custo)
        worksheet.write(row_number, 8, rd_valor_despesa)
        worksheet.write(row_number, 9, rd_saldo_devolver)
        worksheet.write(row_number, 10, rd_data_fechamento)
        worksheet.write(row_number, 11, saldo_global)
        row_number += 1

    workbook.close()
    output.seek(0)
    conn.close()

    return send_file(
        output,
        as_attachment=True,
        download_name=f"Relatorio_RD_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
