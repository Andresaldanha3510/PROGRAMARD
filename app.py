from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
import os
from datetime import datetime

# Carrega variáveis de ambiente
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

# Exporte para Excel
import io
import xlsxwriter
import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
# Disponibiliza a função get_r2_public_url no jinja
app.jinja_env.globals.update(get_r2_public_url=get_r2_public_url)

secret_key = os.getenv('SECRET_KEY', 'secret123')
app.secret_key = secret_key
logging.debug("SECRET_KEY carregado corretamente.")

# Credenciais do PostgreSQL (ajuste se necessário)
PG_HOST = os.getenv('PG_HOST', 'dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com')
PG_PORT = os.getenv('PG_PORT', '5432')
PG_DB   = os.getenv('PG_DB',   'programard_db')
PG_USER = os.getenv('PG_USER', 'programard_db_user')
PG_PASSWORD = os.getenv('PG_PASSWORD','hU9wJmIfgiyCg02KFQ3a4AropKSMopXr')

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
    """Cria as tabelas necessárias, caso não existam."""
    conn = get_pg_connection()
    cursor = conn.cursor()

    # Cria tabela rd (com todas as colunas que usamos)
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
        data_saldo_devolvido DATE,
        unidade_negocio TEXT
    );
    """
    cursor.execute(create_rd_table)

    # Cria tabela para saldo_global
    create_saldo_global_table = """
    CREATE TABLE IF NOT EXISTS saldo_global (
        id SERIAL PRIMARY KEY,
        saldo NUMERIC(15,2) DEFAULT 30000
    );
    """
    cursor.execute(create_saldo_global_table)
    # Garante que exista um registro
    cursor.execute("SELECT COUNT(*) FROM saldo_global")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO saldo_global (saldo) VALUES (30000)")

    conn.commit()
    cursor.close()
    conn.close()

def generate_custom_id():
    """Gera ID no formato 'N.YY', incrementando do último."""
    current_year = datetime.now().year % 100
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM rd
        WHERE split_part(id, '.', 2)::INTEGER = %s
        ORDER BY (split_part(id, '.',1))::INTEGER DESC LIMIT 1
    """, (current_year,))
    last_id = cursor.fetchone()
    conn.close()
    if not last_id:
        # Começa em 400 se não há RDs desse ano
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
    return user_role() in ['solicitante','gestor','financeiro']

def can_edit(status):
    """Controla se o usuário pode editar determinada RD dado o status."""
    if status == 'Fechado':
        return False
    if is_solicitante():
        # O solicitante só pode editar quando está em Pendente
        return (status == 'Pendente')
    if is_gestor() or is_financeiro():
        return True
    return False

def can_delete(status, solicitante):
    """Controla se o usuário pode excluir determinada RD."""
    if status == 'Fechado':
        return False
    if status=='Pendente' and is_solicitante():
        return True
    if (is_gestor() or is_financeiro()) and status in ['Pendente','Aprovado','Liberado']:
        return True
    return False

def can_approve(status):
    """Verifica se o usuário pode aprovar dadas as regras de negócio."""
    if status=='Pendente' and is_gestor():
        return True
    if status=='Aprovado' and is_financeiro():
        return True
    if status=='Fechamento Solicitado' and is_gestor():
        return True
    return False

def can_request_additional(status):
    """O solicitante pode pedir adicional quando a RD está Liberada."""
    return (is_solicitante() and status=='Liberado')

def can_close(status):
    """O solicitante pode fechar a RD quando ela está Liberada."""
    return (is_solicitante() and status=='Liberado')

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
    """Converte float -> string no formato 1.234,56."""
    if value is None:
        return "0,00"
    s = f"{value:,.2f}"
    parts = s.split('.')
    left = parts[0].replace(',','.')
    right = parts[1]
    return f"{left},{right}"

@app.route('/', methods=['GET','POST'])
def index():
    if request.method=='POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        # Logins de exemplo
        if username=='gestor' and password=='115289':
            session['user_role'] = 'gestor'
            flash("Login como gestor bem-sucedido.")
        elif username=='financeiro' and password=='351073':
            session['user_role'] = 'financeiro'
            flash("Login como financeiro bem-sucedido.")
        elif username=='solicitante' and password=='102030':
            session['user_role'] = 'solicitante'
            flash("Login como solicitante bem-sucedido.")
        else:
            flash("Credenciais inválidas.")
            return render_template('index.html', error="Credenciais inválidas", format_currency=format_currency)

        return redirect(url_for('index'))

    # Se não estiver logado, carrega a tela de login
    if 'user_role' not in session:
        return render_template('index.html', error=None, format_currency=format_currency)

    conn = get_pg_connection()
    cursor = conn.cursor()

    # Carrega as RDs dividindo por status
    cursor.execute("SELECT * FROM rd WHERE status='Pendente'")
    pendentes = cursor.fetchall()

    cursor.execute("SELECT * FROM rd WHERE status='Aprovado'")
    aprovados = cursor.fetchall()

    cursor.execute("SELECT * FROM rd WHERE status='Liberado'")
    liberados = cursor.fetchall()

    cursor.execute("SELECT * FROM rd WHERE status='Fechamento Solicitado'")
    fechamento_solicitado = cursor.fetchall()

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

@app.route('/add', methods=['POST'])
def add_rd():
    """Cria uma nova RD."""
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
        valor_str = request.form['valor'].replace(',', '.')
        valor = float(valor_str)
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
    """Carrega a tela de edição da RD."""
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
    """Recebe submissão de edição da RD."""
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
        valor_str = request.form['valor'].replace(',', '.')
        valor = float(valor_str)
    except ValueError:
        flash("Valor inválido.")
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    rd_data = cursor.fetchone()
    arquivos = rd_data[0].split(',') if rd_data and rd_data[0] else []

    # Anexa novos arquivos, se houver
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
          arquivos_str, observacao, unidade_negocio,
          id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD atualizada com sucesso.")
    return redirect(url_for('index'))

@app.route('/approve/<id>', methods=['POST'])
def approve(id):
    """
    Aprovação de RD:
    - Gestor aprova Pendentes -> vira Aprovado
    - Financeiro aprova Aprovados -> abate saldo global e:
        - Se RD for reembolso, fecha
        - Caso contrário, vira Liberada
    - Gestor aprova Fechamento Solicitado -> soma saldo a devolver e fecha
    """
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional, valor_liberado, tipo FROM rd WHERE id=%s", (id,))
    rd_info = cursor.fetchone()
    if not rd_info:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    status, valor, valor_adic, valor_liberado, rd_tipo = rd_info
    if not can_approve(status):
        conn.close()
        flash("Acesso negado.")
        return redirect(url_for('index'))

    current_date = datetime.now().strftime('%Y-%m-%d')

    if status == 'Pendente' and is_gestor():
        # Gestor aprova
        new_status = 'Aprovado'
        cursor.execute("""
            UPDATE rd SET status=%s, aprovado_data=%s
            WHERE id=%s
        """, (new_status, current_date, id))

    elif status == 'Aprovado' and is_financeiro():
        # Financeiro libera (ou fecha) e abate do saldo global
        valor_total = (valor or 0) + (valor_adic or 0)
        saldo = get_saldo_global()
        if valor_total > saldo:
            conn.close()
            flash("Saldo global insuficiente para liberar.")
            return redirect(url_for('index'))
        set_saldo_global(saldo - valor_total)

        if rd_tipo.lower() == 'reembolso':
            new_status = 'Fechado'
            cursor.execute("""
                UPDATE rd
                SET status=%s, data_fechamento=%s, valor_liberado=%s
                WHERE id=%s
            """, (new_status, current_date, valor_total, id))
        else:
            new_status = 'Liberado'
            cursor.execute("""
                UPDATE rd
                SET status=%s, liberado_data=%s, valor_liberado=%s
                WHERE id=%s
            """, (new_status, current_date, valor_total, id))

    elif status == 'Fechamento Solicitado' and is_gestor():
        # Gestor aprova o fechamento -> soma saldo_devolver ao saldo global
        cursor.execute("SELECT saldo_devolver FROM rd WHERE id=%s", (id,))
        saldo_dev = cursor.fetchone()[0] or 0
        if saldo_dev > 0:
            saldo = get_saldo_global()
            set_saldo_global(saldo + saldo_dev)

        new_status = 'Fechado'
        cursor.execute("""
            UPDATE rd
            SET status=%s
            WHERE id=%s
        """, (new_status, id))

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
    """Exclui uma RD, devolvendo saldo se já estava Liberada."""
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
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Se estava Liberada e tinha valor_liberado, devolve ao saldo global
    if rd_status == 'Liberado' and rd_liberado and rd_liberado > 0:
        saldo = get_saldo_global()
        set_saldo_global(saldo + rd_liberado)

    # Deleta arquivos
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    arquivos_str = cursor.fetchone()[0]
    if arquivos_str:
        for arq in arquivos_str.split(','):
            delete_file_from_r2(arq)

    # Deleta RD
    cursor.execute("DELETE FROM rd WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD excluída com sucesso.")
    return redirect(url_for('index'))

@app.route('/adicional_submit/<id>', methods=['POST'])
def adicional_submit(id):
    """Solicita crédito adicional, anexando arquivos, e volta para status Pendente."""
    if not can_request_additional_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Anexa arquivos se houver
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
        valor_adicional_str = request.form['valor_adicional'].replace(',', '.')
        valor_adicional_novo = float(valor_adicional_str)
    except (ValueError, KeyError):
        flash("Valor adicional inválido.")
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor_adicional FROM rd WHERE id=%s", (id,))
    rd_status = cursor.fetchone()
    if not rd_status:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    status_atual, valor_adic_atual = rd_status
    if not can_request_additional(status_atual):
        conn.close()
        flash("Não é possível solicitar adicional agora.")
        return redirect(url_for('index'))

    novo_valor_adic = (valor_adic_atual or 0) + valor_adicional_novo
    add_data = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        UPDATE rd
        SET valor_adicional=%s, adicional_data=%s, status='Pendente'
        WHERE id=%s
    """, (novo_valor_adic, add_data, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Crédito adicional solicitado. A RD voltou para 'Pendente'.")
    return redirect(url_for('index'))

@app.route('/fechamento_submit/<id>', methods=['POST'])
def fechamento_submit(id):
    """
    O solicitante informa o valor gasto, anexa os arquivos finais e muda status para 'Fechamento Solicitado'.
    Em seguida, aguarda aprovação do gestor para fechar.
    """
    if not can_close_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Anexa arquivos, se houver
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
        valor_despesa_str = request.form['valor_despesa'].replace(',', '.')
        valor_despesa = float(valor_despesa_str)
    except (ValueError, KeyError):
        flash("Valor da despesa inválido.")
        return redirect(url_for('index'))

    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor_liberado FROM rd WHERE id=%s", (id,))
    rd_info = cursor.fetchone()
    if not rd_info:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    status_atual, valor_liberado = rd_info
    if not can_close(status_atual):
        conn.close()
        flash("Não é possível fechar esta RD agora.")
        return redirect(url_for('index'))

    if (valor_liberado or 0) < valor_despesa:
        conn.close()
        flash("Valor da despesa maior que o valor liberado.")
        return redirect(url_for('index'))

    saldo_dev = (valor_liberado or 0) - valor_despesa
    data_fech = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        UPDATE rd
        SET valor_despesa=%s, saldo_devolver=%s, data_fechamento=%s,
            status='Fechamento Solicitado'
        WHERE id=%s
    """, (valor_despesa, saldo_dev, data_fech, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento solicitado. Aguarde aprovação do gestor.")
    return redirect(url_for('index'))

@app.route('/export_excel', methods=['GET'])
def export_excel():
    """
    Exporta todas as RDs para Excel, colocando o saldo_global na planilha.
    """
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
        rd_id          = rd_row[0]
        rd_data        = rd_row[3]
        rd_solic       = rd_row[1]
        rd_func        = rd_row[2]
        rd_valor       = rd_row[5]
        rd_valor_adic  = rd_row[7]
        rd_adic_data   = rd_row[8]
        rd_ccusto      = rd_row[4]
        rd_desp        = rd_row[9]
        rd_saldo_dev   = rd_row[10]
        rd_data_fech   = rd_row[11]

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
        attachment_filename="relatorio.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def can_edit_status(id):
    """Retorna se a RD (pelo ID) pode ser editada pelo status."""
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return can_edit(row[0])

def can_request_additional_status(id):
    """Retorna se a RD (pelo ID) pode receber adicional."""
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    st = cursor.fetchone()
    conn.close()
    if not st:
        return False
    return can_request_additional(st[0])

def can_close_status(id):
    """Retorna se a RD (pelo ID) pode ser fechada."""
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    st = cursor.fetchone()
    conn.close()
    if not st:
        return False
    return can_close(st[0])

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
