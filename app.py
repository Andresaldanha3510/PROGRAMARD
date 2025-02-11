from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, abort, flash, send_file
import psycopg2
from psycopg2 import sql
import os
from datetime import datetime
from dotenv import load_dotenv
import boto3
from botocore.client import Config
import io
import xlsxwriter

# Carrega variáveis de ambiente
load_dotenv()  # Carrega as variáveis do arquivo .env

# ---- INÍCIO: Imports e variáveis para o Cloudflare R2 ----
R2_ACCESS_KEY = os.getenv('R2_ACCESS_KEY', 'your_r2_access_key')
R2_SECRET_KEY = os.getenv('R2_SECRET_KEY', 'your_r2_secret_key')
R2_ENDPOINT = os.getenv('R2_ENDPOINT', 'https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com')
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME', 'meu-bucket-r2')
R2_PUBLIC_URL = os.getenv('R2_PUBLIC_URL', 'https://pub-1e6f8559bc2b413c889fbf4860462599.r2.dev')

def get_r2_public_url(object_name):
    """Retorna o URL público de um objeto no R2."""
    return f"{R2_PUBLIC_URL}/{object_name}"

def upload_file_to_r2(file_obj, object_name):
    """
    Envia um arquivo para o Bucket R2 diretamente do objeto em memória,
    usando upload_fileobj (sem salvar localmente).
    """
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

# ---- INÍCIO: Imports para geração de Excel ----
import io
import xlsxwriter
# ---- FIM: Imports para geração de Excel ----

# Configuração do logging
import logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.jinja_env.globals.update(get_r2_public_url=get_r2_public_url)

# Configuração do SECRET_KEY
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    raise ValueError("SECRET_KEY não está definida no ambiente.")
app.secret_key = secret_key
logging.debug(f"SECRET_KEY carregado corretamente.")

# Configurações do PostgreSQL
PG_HOST = os.getenv('PG_HOST', 'dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com')
PG_PORT = os.getenv('PG_PORT', '5432')
PG_DB = os.getenv('PG_DB', 'programard_db')
PG_USER = os.getenv('PG_USER', 'programard_db_user')
PG_PASSWORD = os.getenv('PG_PASSWORD', 'hU9wJmIfgiyCg02KFQ3a4AropKSMopXr')  # Mude isso imediatamente

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
    Inicializa o banco no PostgreSQL, criando tabelas (se não existirem) e adicionando o campo
    'valor_liberado' na rd (se necessário), bem como saldo_global.
    Se precisar do campo 'observacao', também pode criar/alterar aqui.
    """
    conn = get_pg_connection()
    cursor = conn.cursor()
    # Cria tabela RD, caso não exista
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
    # Verifica se a coluna 'valor_liberado' existe
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'rd' AND column_name = 'valor_liberado';
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN valor_liberado NUMERIC(15, 2) DEFAULT 0;")
    # Verifica se a coluna 'observacao' existe (se não, cria)
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'rd' AND column_name = 'observacao';
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN observacao TEXT;")
    # Cria tabela saldo_global
    create_saldo_global_table = """
    CREATE TABLE IF NOT EXISTS saldo_global (
        id SERIAL PRIMARY KEY,
        saldo NUMERIC(15, 2) DEFAULT 30000
    );
    """
    cursor.execute(create_saldo_global_table)
    # Inicializa saldo global caso não exista
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
    # Solicitante, Gestor ou Financeiro podem adicionar
    return user_role() in ['solicitante', 'gestor', 'financeiro']

def can_edit(status):
    # Pode editar se não estiver Fechado e for gestor ou financeiro.
    if status == 'Fechado':
        return False
    if is_gestor() or is_financeiro():
        return True
    return False

def can_delete(status, solicitante):
    """
    - Solicitante pode excluir enquanto estiver pendente e for dele mesmo.
    - Gestor e Financeiro podem excluir em Pendente, Aprovado e Liberado.
    - Ninguém pode excluir se Fechado.
    """
    if status == 'Fechado':
        return False
    if status == 'Pendente' and is_solicitante():
        return True
    if (is_gestor() or is_financeiro()) and status in ['Pendente', 'Aprovado', 'Liberado']:
        return True
    return False

def can_approve(status):
    """
    Pendente -> Aprovado (Gestor)
    Aprovado -> Liberado (Financeiro)
    """
    if status == 'Pendente' and is_gestor():
        return True
    if status == 'Aprovado' and is_financeiro():
        return True
    return False

def can_request_additional(status):
    # Solicitante solicita adicional se estiver Liberado
    return is_solicitante() and status == 'Liberado'

def can_close(status):
    """
    Solicitante pode enviar para 'Pendente de Aprovação de Fechamento' se estiver Liberado.
    """
    return is_solicitante() and status == 'Liberado'

def can_approve_fechamento(status):
    """
    Gestor pode aprovar o fechamento se o status for 'Pendente de Aprovação de Fechamento'.
    """
    return is_gestor() and status == 'Pendente de Aprovação de Fechamento'

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
    """Formata o valor para estilo BR."""
    formatted = f"{value:,.2f}"  # Ex: "30,000.00"
    parts = formatted.split('.')
    left = parts[0].replace(',', '.')
    right = parts[1]
    return f"{left},{right}"

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        logging.debug(f"Dados do formulário: {request.form}")
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        logging.debug(f"Tentativa de login com username: '{username}' e password: '{password}'")
        if username == 'gestor' and password == '115289':
            session['user_role'] = 'gestor'
            flash('Login como gestor bem-sucedido.')
            logging.debug("Login como gestor bem-sucedido.")
        elif username == 'financeiro' and password == '351073':
            session['user_role'] = 'financeiro'
            flash('Login como financeiro bem-sucedido.')
            logging.debug("Login como financeiro bem-sucedido.")
        elif username == 'solicitante' and password == '102030':
            session['user_role'] = 'solicitante'
            flash('Login como solicitante bem-sucedido.')
            logging.debug("Login como solicitante bem-sucedido.")
        else:
            flash('Credenciais inválidas.')
            logging.warning("Tentativa de login com credenciais inválidas.")
            return render_template('index.html', error="Credenciais inválidas", format_currency=format_currency)
        return redirect(url_for('index'))

    if 'user_role' not in session:
        logging.debug("Usuário não autenticado. Mostrando formulário de login.")
        return render_template('index.html', error=None, format_currency=format_currency)

    logging.debug(f"Usuário autenticado como: {session['user_role']}")
    conn = get_pg_connection()
    cursor = conn.cursor()
    # Seleciona as RDs por status
    cursor.execute("SELECT * FROM rd WHERE status='Pendente'")
    pendentes = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Aprovado'")
    aprovados = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Liberado'")
    liberados = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Pendente de Aprovação de Fechamento'")
    pendentes_aprovacao_fechamento = cursor.fetchall()
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
        pendentes_aprovacao_fechamento=pendentes_aprovacao_fechamento,
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
        can_approve_fechamento=can_approve_fechamento,
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
    # Lê a observacao
    observacao = request.form.get('observacao', '').strip()
    try:
        valor = float(request.form['valor'])
    except ValueError:
        flash('Valor inválido.')
        return redirect(url_for('index'))
    custom_id = generate_custom_id()
    # Gerenciar arquivos: envia para R2
    arquivos = []
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{custom_id}_{file.filename}"
                upload_file_to_r2(file, filename)
                arquivos.append(filename)
    arquivos_str = ','.join(arquivos) if arquivos else None
    # Insere no BD com valor_liberado = 0, e observa se passamos 'observacao'
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO rd (
            id, solicitante, funcionario, data, centro_custo,
            valor, status, arquivos, valor_liberado, observacao
        ) VALUES (%s, %s, %s, %s, %s,
                   %s, %s, %s, 0, %s)
    ''', (
        custom_id,
        solicitante,
        funcionario,
        data,
        centro_custo,
        valor,
        'Pendente',
        arquivos_str,
        observacao
    ))
    conn.commit()
    cursor.close()
    conn.close()
    flash('RD adicionada com sucesso.')
    return redirect(url_for('index'))

@app.route('/edit_form/', methods=['GET'])
def edit_form(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        flash('RD não encontrada.')
        return "RD não encontrada", 404
    status = rd[6]  # Índice da coluna 'status'
    if not can_edit(status):
        flash('Acesso negado.')
        return "Acesso negado", 403
    return render_template('edit_form.html', rd=rd)

@app.route('/edit_submit/', methods=['POST'])
def edit_submit(id):
    # Verifica se pode editar
    if not can_edit_status(id):
        flash('Acesso negado.')
        return "Acesso negado", 403
    solicitante = request.form['solicitante'].strip()
    funcionario = request.form['funcionario'].strip()
    data = request.form['data'].strip()
    centro_custo = request.form['centro_custo'].strip()
    # Ajuste de indentação aqui:
    observacao = request.form.get('observacao', '').strip()
    try:
        valor = float(request.form['valor'])
    except ValueError:
        flash('Valor inválido.')
        return redirect(url_for('index'))
    conn = get_pg_connection()
    cursor = conn.cursor()
    # Pega arquivos atuais
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    arquivos = rd[0].split(',') if (rd and rd[0]) else []
    # Se enviou novos arquivos, faz upload no R2
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                upload_file_to_r2(file, filename)
                arquivos.append(filename)
    arquivos_str = ','.join(arquivos) if arquivos else None
    # UPDATE incluindo 'observacao'
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

@app.route('/approve/', methods=['POST'])
def approve(id):
    """
    Pendente->Aprovado (Gestor), Aprovado->Liberado (Financeiro).
    Ao liberar, subtrai apenas a diferença não-liberada do saldo.
    """
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
        # Passa para 'Aprovado'
        new_status = 'Aprovado'
        cursor.execute(
            "UPDATE rd SET status=%s, aprovado_data=%s WHERE id=%s",
            (new_status, current_date, id)
        )
    elif status == 'Aprovado' and is_financeiro():
        # Liberar: subtrair apenas o delta (valor_total - valor_liberado)
        new_status = 'Liberado'
        valor_total = valor + (valor_adic or 0)
        falta_liberar = valor_total - (valor_liberado or 0)
        if falta_liberar > 0:
            saldo = get_saldo_global()
            if falta_liberar > saldo:
                conn.close()
                flash('Saldo global insuficiente para liberar a diferença adicional.')
                return redirect(url_for('index'))
            # Subtrai do saldo
            set_saldo_global(saldo - falta_liberar)
            # Atualiza o valor_liberado para o total atual
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

@app.route('/delete/', methods=['POST'])
def delete_rd(id):
    """
    Se RD estava Liberado, devolve ao saldo o valor_liberado (que já saiu do caixa).
    """
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
    # Exclui arquivos associados (apenas no R2)
    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    arquivos = cursor.fetchone()[0]
    if arquivos:
        for arquivo in arquivos.split(','):
            delete_file_from_r2(arquivo)
    # Deleta do BD
    cursor.execute("DELETE FROM rd WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('RD excluída com sucesso.')
    return redirect(url_for('index'))

@app.route('/adicional_submit/', methods=['POST'])
def adicional_submit(id):
    """
    Solicita adicional: volta a RD para 'Pendente' e soma valor_adicional.
    """
    if not can_request_additional_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))
    # Se houver arquivos adicionais
    if 'arquivo' in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        rd_atual = cursor.fetchone()
        arquivos_atuais = rd_atual[0].split(',') if (rd_atual and rd_atual[0]) else []
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
    # Valor adicional
    try:
        valor_adicional_novo = float(request.form['valor_adicional'])
    except (ValueError, KeyError):
        flash('Valor adicional inválido.')
        return redirect(url_for('index'))
    # Atualiza RD (status -> Pendente, soma o adicional)
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

@app.route('/fechamento_submit/<id>', methods=['POST'])
def fechamento_submit(id):
    """
    Envia a RD para 'Pendente de Aprovação de Fechamento'.
    """
    if not can_close_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # 2) Se houver arquivos, faz upload para R2
    if 'arquivo' in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        rd_atual = cursor.fetchone()
        arquivos_atuais = rd_atual[0].split(',') if (rd_atual and rd_atual[0]) else []
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

    # 3) Captura e valida o valor da despesa informado
    try:
        valor_despesa = float(request.form['valor_despesa'])
    except (ValueError, KeyError):
        flash('Valor da despesa inválido.')
        return redirect(url_for('index'))

    # 4) Busca dados da RD
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

    # 5) Atualiza o status para 'Pendente de Aprovação de Fechamento'
    saldo_devolver = valor_liberado - valor_despesa
    cursor.execute("""
        UPDATE rd
        SET valor_despesa=%s, saldo_devolver=%s, status='Pendente de Aprovação de Fechamento'
        WHERE id=%s
    """, (valor_despesa, saldo_devolver, id))
    conn.commit()
    cursor.close()
    conn.close()

    flash(f'RD enviada para aprovação de fechamento. Saldo devolvido = R${saldo_devolver:.2f}')
    return redirect(url_for('index'))

@app.route('/approve_fechamento/<id>', methods=['POST'])
def approve_fechamento(id):
    """
    Aprova o fechamento do RD, movendo-o para 'Fechado'.
    """
    if not can_approve_fechamento_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # 1) Devolve o saldo ao saldo global
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT valor_despesa, saldo_devolver FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))

    valor_despesa, saldo_devolver = rd
    saldo_global = get_saldo_global()
    set_saldo_global(saldo_global + saldo_devolver)

    # 2) Atualiza o status para 'Fechado'
    data_fechamento = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        UPDATE rd
        SET status='Fechado', data_fechamento=%s
        WHERE id=%s
    """, (data_fechamento, id))
    conn.commit()
    cursor.close()
    conn.close()

    flash(f'RD fechada com sucesso. Saldo devolvido = R${saldo_devolver:.2f}')
    return redirect(url_for('index'))

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

@app.route('/delete_file/', methods=['POST'])
def delete_file(id):
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

def can_approve_fechamento_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    status = rd[0]
    return can_approve_fechamento(status)

#
# ROTA PARA EXPORTAR DADOS EM EXCEL
#

@app.route('/export_excel', methods=['GET'])
def export_excel():
    """
    Exporta as RDs para um relatório em Excel com as colunas:
    - Número da RD (id)
    - Data da Solicitação (data)
    - Solicitante
    - Funcionário
    - Valor Solicitado
    - Valor Adicional
    - Data do Adicional
    - Centro de Custo
    - Valor Gasto (Despesa)
    - Saldo a Devolver
    - Data de Fechamento
    - Saldo Global (atual, no momento da exportação)
    """
    conn = get_pg_connection()
    cursor = conn.cursor()
    # Vamos buscar todas as RDs
    cursor.execute("SELECT * FROM rd ORDER BY id ASC")
    rd_list = cursor.fetchall()
    # Pega o saldo global atual
    saldo_global = get_saldo_global()
    # Montamos o Excel em memória
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet('Relatorio')
    # Cabeçalho
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
    # Índices de cada campo na tupla retornada por fetchall():
    # (id, solicitante, funcionario, data, centro_custo, valor, status,
    #  valor_adicional, adicional_data, valor_despesa, saldo_devolver,
    #  data_fechamento, arquivos, aprovado_data, liberado_data, valor_liberado, observacao?)
    # Depende da ordem real em CREATE TABLE.
    row_number = 1
    for rd_row in rd_list:
        rd_id = rd_row[0]
        rd_solicitante = rd_row[1]
        rd_funcionario = rd_row[2]
        rd_data = rd_row[3]
        rd_centro_custo = rd_row[4]
        rd_valor = rd_row[5]
        rd_valor_adicional = rd_row[7]
        rd_adicional_data = rd_row[8]
        rd_valor_despesa = rd_row[9]
        rd_saldo_devolver = rd_row[10]
        rd_data_fechamento = rd_row[11]
        worksheet.write(row_number, 0, rd_id)  # Número RD
        worksheet.write(row_number, 1, str(rd_data))  # Data Solicitação
        worksheet.write(row_number, 2, rd_solicitante)  # Solicitante
        worksheet.write(row_number, 3, rd_funcionario)  # Funcionário
        worksheet.write(row_number, 4, float(rd_valor or 0))  # Valor Solicitado
        worksheet.write(row_number, 5, float(rd_valor_adicional or 0))  # Valor Adicional
        worksheet.write(row_number, 6, str(rd_adicional_data or ''))  # Data do Adicional
        worksheet.write(row_number, 7, rd_centro_custo)  # Centro de custo
        worksheet.write(row_number, 8, float(rd_valor_despesa or 0))  # Valor Gasto (Despesa)
        worksheet.write(row_number, 9, float(rd_saldo_devolver or 0))  # Saldo a Devolver
        worksheet.write(row_number, 10, str(rd_data_fechamento or ''))  # Data de Fechamento
        worksheet.write(row_number, 11, float(saldo_global))  # Saldo Global atual
        row_number += 1
    workbook.close()
    output.seek(0)
    conn.close()
    # Retorna o arquivo Excel como anexo para download
   
    return send_file(
        output,
        as_attachment=True,
        download_name=f"Relatorio_RD_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if __name__ == '__main__':
    init_db()
    app.run(debug=True)