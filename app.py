from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
import os
from datetime import datetime
import boto3
from botocore.client import Config
import io
import xlsxwriter
import logging
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv()
logging.basicConfig(level=logging.DEBUG)

# ---- Configurações para Cloudflare R2 ----
R2_ACCESS_KEY = os.getenv('R2_ACCESS_KEY', 'sua_r2_access_key')
R2_SECRET_KEY = os.getenv('R2_SECRET_KEY', 'sua_r2_secret_key')
R2_ENDPOINT   = 'https://seu-endpoint.r2.cloudflarestorage.com'
R2_BUCKET_NAME = 'meu-bucket-r2'
R2_PUBLIC_URL = "https://seu-r2-public-url"

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

# ---- Configuração do Flask ----
app = Flask(__name__)
app.jinja_env.globals.update(get_r2_public_url=get_r2_public_url)

secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    raise ValueError("SECRET_KEY não está definida no ambiente.")
app.secret_key = secret_key
logging.debug("SECRET_KEY carregado corretamente.")

# ---- Configurações do PostgreSQL ----
PG_HOST = os.getenv('PG_HOST', 'dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com')
PG_PORT = os.getenv('PG_PORT', '5432')
PG_DB = os.getenv('PG_DB', 'programard_db')
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
    """
    Inicializa o banco, criando a tabela rd (se não existir)
    e adicionando as colunas 'tipo' e 'data_saldo_devolvido' se necessário.
    """
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
        valor_liberado NUMERIC(15, 2) DEFAULT 0,
        observacao TEXT
    );
    """
    cursor.execute(create_rd_table)

    # Adiciona coluna 'tipo' se não existir
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'rd' AND column_name = 'tipo';
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN tipo TEXT DEFAULT 'credito alelo';")

    # Adiciona coluna 'data_saldo_devolvido' se não existir
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'rd' AND column_name = 'data_saldo_devolvido';
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN data_saldo_devolvido DATE;")

    # Cria tabela saldo_global
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
    # Permite aprovação para Pendente, ou para fechamento solicitado (gestor)
    if status == 'Pendente' and is_gestor():
        return True
    if status == 'Aprovado' and is_financeiro():
        return True
    if status == 'Fechamento Solicitado' and is_gestor():
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
        pendentes=pendentes,
        aprovados=aprovados,
        liberados=liberados,
        fechamento_solicitado=fechamento_solicitado,
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
        format_currency=format_currency
    )

@app.route('/add', methods=['POST'])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado", 403

    solicitante = request.form['solicitante'].strip()
    funcionario = request.form['funcionario'].strip()
    data = request.form['data'].strip()
    centro_custo = request.form['centro_custo'].strip()
    observacao = request.form.get('observacao', '').strip()
    rd_tipo = request.form.get('tipo', 'credito alelo').strip()

    try:
        valor = float(request.form['valor'])
    except ValueError:
        flash('Valor inválido.')
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
    cursor.execute('''
        INSERT INTO rd (
            id, solicitante, funcionario, data, centro_custo,
            valor, status, arquivos, valor_liberado, observacao, tipo
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
    ''', (
        custom_id,
        solicitante,
        funcionario,
        data,
        centro_custo,
        valor,
        'Pendente',
        arquivos_str,
        observacao,
        rd_tipo
    ))
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
    if not can_edit(rd[6]):
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
    observacao = request.form.get('observacao', '').strip()
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
                upload_file_to_r2(file, filename)
                arquivos.append(filename)
    arquivos_str = ','.join(arquivos) if arquivos else None
    cursor.execute('''
        UPDATE rd
        SET solicitante=%s,
            funcionario=%s,
            data=%s,
            centro_custo=%s,
            valor=%s,
            arquivos=%s,
            observacao=%s
        WHERE id=%s
    ''', (
        solicitante,
        funcionario,
        data,
        centro_custo,
        valor,
        arquivos_str,
        observacao,
        id
    ))
    conn.commit()
    cursor.close()
    conn.close()
    flash('RD atualizada com sucesso.')
    return redirect(url_for('index'))

@app.route('/approve/<id>', methods=['POST'])
def approve(id):
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
        new_status = 'Aprovado'
        cursor.execute(
            "UPDATE rd SET status=%s, aprovado_data=%s WHERE id=%s",
            (new_status, current_date, id)
        )
    elif status == 'Aprovado' and is_financeiro():
        if rd_tipo.lower() == 'reembolso':
            new_status = 'Fechado'
            cursor.execute(
                "UPDATE rd SET status=%s, data_fechamento=%s WHERE id=%s",
                (new_status, current_date, id)
            )
        else:
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
    elif status == 'Fechamento Solicitado' and is_gestor():
        new_status = 'Fechado'
        cursor.execute("UPDATE rd SET status=%s WHERE id=%s", (new_status, id))
    else:
        conn.close()
        flash("Não é possível aprovar esta RD.")
        return redirect(url_for('index'))
    conn.commit()
    cursor.close()
    conn.close()
    flash('Operação realizada com sucesso.')
    return redirect(url_for('index'))

@app.route('/aprovar_fechamento/<id>', methods=['POST'])
def aprovar_fechamento(id):
    if not is_gestor():
        flash('Acesso negado.')
        return redirect(url_for('index'))
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd or rd[0] != 'Fechamento Solicitado':
        conn.close()
        flash('RD não encontrada ou não está em Fechamento Solicitado.')
        return redirect(url_for('index'))
    cursor.execute("UPDATE rd SET status='Fechado' WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('Fechamento aprovado. RD movida para Fechado.')
    return redirect(url_for('index'))

@app.route('/registrar_saldo_devolvido/<id>', methods=['POST'])
def registrar_saldo_devolvido(id):
    if not is_financeiro():
        flash('Acesso negado.')
        return redirect(url_for('index'))
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT valor_liberado, valor_despesa, data_saldo_devolvido FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    valor_liberado, valor_despesa, data_saldo_devolvido = rd
    if data_saldo_devolvido:
        conn.close()
        flash("Saldo já registrado.")
        return redirect(url_for('index'))
    if valor_liberado < float(valor_despesa or 0):
        conn.close()
        flash("Valor da despesa maior que o valor liberado.")
        return redirect(url_for('index'))
    saldo_devolver = valor_liberado - float(valor_despesa or 0)
    saldo = get_saldo_global()
    set_saldo_global(saldo + saldo_devolver)
    current_date = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("UPDATE rd SET data_saldo_devolvido=%s WHERE id=%s", (current_date, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash('Saldo devolvido registrado com sucesso.')
    return redirect(url_for('index'))

@app.route('/delete/<id>', methods=['POST'])
def delete_rd(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT solicitante, status, valor_liberado FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    if not can_delete(rd[1], rd[0]):
        conn.close()
        flash("Acesso negado.")
        return redirect(url_for('index'))
    if rd[1] == 'Liberado' and rd[2] and rd[2] > 0:
        saldo = get_saldo_global()
        set_saldo_global(saldo + rd[2])
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    arquivos = cursor.fetchone()[0]
    if arquivos:
        for arquivo in arquivos.split(','):
            delete_file_from_r2(arquivo)
    cursor.execute("DELETE FROM rd WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('RD excluída com sucesso.')
    return redirect(url_for('index'))

def can_edit_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    return can_edit(rd[0])

def can_request_additional_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    return can_request_additional(rd[0])

def can_close_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    return can_close(rd[0])

@app.route('/logout')
def logout():
    session.clear()
    flash('Logout realizado com sucesso.')
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
        "Data Fechamento",
        "Data Saldo Devolvido",
        "Saldo Global"
    ]
    for col, h in enumerate(header):
        worksheet.write(0, col, h)
    row_number = 1
    for rd_row in rd_list:
        worksheet.write(row_number, 0, rd_row[0])
        worksheet.write(row_number, 1, str(rd_row[3]))
        worksheet.write(row_number, 2, rd_row[1])
        worksheet.write(row_number, 3, rd_row[2])
        worksheet.write(row_number, 4, float(rd_row[5] or 0))
        worksheet.write(row_number, 5, float(rd_row[7] or 0))
        worksheet.write(row_number, 6, str(rd_row[8] or ''))
        worksheet.write(row_number, 7, rd_row[4])
        worksheet.write(row_number, 8, float(rd_row[9] or 0))
        worksheet.write(row_number, 9, float(rd_row[10] or 0))
        worksheet.write(row_number, 10, str(rd_row[11] or ''))
        worksheet.write(row_number, 11, str(rd_row[18] or ''))
        worksheet.write(row_number, 12, float(saldo_global))
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
