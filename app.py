import os
import io
import sqlite3
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, session, flash, send_file
)

# ------------------------------------------------------------
# Configurações de credenciais via variáveis de ambiente
# (Recomendado: defina no Render ou em um .env local)
# ------------------------------------------------------------
R2_ACCESS_KEY = os.environ.get('R2_ACCESS_KEY') or 'SUA_CHAVE_AQUI'
R2_SECRET_KEY = os.environ.get('R2_SECRET_KEY') or 'SUA_CHAVE_SECRETA_AQUI'
R2_ENDPOINT   = os.environ.get('R2_ENDPOINT')   or 'https://seu-endpoint.r2.cloudflarestorage.com'
R2_BUCKET_NAME= os.environ.get('R2_BUCKET_NAME')or 'nome-do-seu-bucket'

# ------------------------------------------------------------
# Imports e setup do boto3 para Cloudflare R2
# ------------------------------------------------------------
import boto3
from botocore.client import Config

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

# ------------------------------------------------------------
# Backup do próprio database.db no R2
# ------------------------------------------------------------
def download_db_from_r2():
    """Baixa o 'database.db' do bucket R2, se existir."""
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4')
    )
    db_file = 'database.db'
    try:
        print("Tentando baixar 'database.db' do Cloudflare R2 ...")
        s3.download_file(R2_BUCKET_NAME, db_file, db_file)
        print("database.db baixado com sucesso do R2.")
    except Exception as e:
        print("Não foi possível baixar o database.db do R2 (talvez não exista ainda).")
        print(e)

def upload_db_to_r2():
    """Envia o 'database.db' para o bucket R2, como backup."""
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4')
    )
    db_file = 'database.db'
    if os.path.exists(db_file):
        print("Enviando 'database.db' para o Cloudflare R2 ...")
        s3.upload_file(db_file, R2_BUCKET_NAME, db_file)
        print("database.db enviado com sucesso para R2.")
    else:
        print("database.db não existe localmente; nada para enviar.")

# ------------------------------------------------------------
# Imports para geração de Excel
# ------------------------------------------------------------
import xlsxwriter

# ------------------------------------------------------------
# Configuração do Flask
# ------------------------------------------------------------
app = Flask(__name__)
app.secret_key = 'chave_secreta'

# Pasta para uploads locais
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ------------------------------------------------------------
# Funções de inicialização e helpers
# ------------------------------------------------------------
def init_db():
    """Inicializa o banco, criando tabelas (se não existirem)
       e adicionando campos extras se necessário.
    """
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rd (
            id TEXT PRIMARY KEY,
            solicitante TEXT NOT NULL,
            funcionario TEXT NOT NULL,
            data TEXT NOT NULL,
            centro_custo TEXT NOT NULL,
            valor REAL NOT NULL,
            status TEXT DEFAULT 'Pendente',
            valor_adicional REAL DEFAULT 0,
            adicional_data TEXT,
            valor_despesa REAL,
            saldo_devolver REAL,
            data_fechamento TEXT,
            arquivos TEXT,
            aprovado_data TEXT,
            liberado_data TEXT
        )
    ''')

    # Verifica se já existe a coluna valor_liberado
    cursor.execute("PRAGMA table_info(rd)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'valor_liberado' not in columns:
        cursor.execute("ALTER TABLE rd ADD COLUMN valor_liberado REAL DEFAULT 0")

    # Tabela de saldo_global
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS saldo_global (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            saldo REAL DEFAULT 30000
        )
    ''')

    # Inicializa saldo_global caso não exista
    cursor.execute('SELECT COUNT(*) FROM saldo_global')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO saldo_global (saldo) VALUES (30000)')

    conn.commit()
    conn.close()

def generate_custom_id():
    """Gera um ID customizado do tipo 400.(ano%100), incrementando a parte inicial."""
    current_year = datetime.now().year % 100
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM rd ORDER BY CAST(substr(id, 1, instr(id, '.') - 1) AS INTEGER) DESC LIMIT 1")
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
    # Solicitante pode excluir se for dele e se estiver 'Pendente'
    if status == 'Pendente' and is_solicitante():
        return True
    # Gestor/Financeiro podem excluir em Pendente, Aprovado, Liberado
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
    return (is_solicitante() and status == 'Liberado')

def can_close(status):
    return (is_solicitante() and status == 'Liberado')

def get_saldo_global():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT saldo FROM saldo_global LIMIT 1")
    saldo = cursor.fetchone()[0]
    conn.close()
    return saldo

def set_saldo_global(novo_saldo):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE saldo_global SET saldo = ? WHERE id = 1', (novo_saldo,))
    conn.commit()
    conn.close()

def format_currency(value):
    formatted = f"{value:,.2f}"  # Ex: 30,000.00
    parts = formatted.split('.')
    left = parts[0].replace(',', '.')
    right = parts[1]
    return f"{left},{right}"

# ------------------------------------------------------------
# Rotas
# ------------------------------------------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST' and 'action' not in request.form:
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'gestor' and password == '115289':
            session['user_role'] = 'gestor'
        elif username == 'financeiro' and password == '351073':
            session['user_role'] = 'financeiro'
        elif username == 'solicitante' and password == '102030':
            session['user_role'] = 'solicitante'
        else:
            return render_template('index.html', error="Credenciais inválidas", format_currency=format_currency)
        return redirect(url_for('index'))

    if 'user_role' not in session:
        return render_template('index.html', error=None, format_currency=format_currency)

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # Selecionar RDs por status
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
        return "Acesso negado", 403

    solicitante = request.form['solicitante']
    funcionario = request.form['funcionario']
    data = request.form['data']
    centro_custo = request.form['centro_custo']
    valor = float(request.form['valor'])
    custom_id = generate_custom_id()

    # Gerenciar arquivos
    arquivos = []
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{custom_id}_{file.filename}"
                local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(local_path)
                upload_file_to_r2(local_path, filename)
                arquivos.append(filename)
    arquivos_str = ','.join(arquivos) if arquivos else None

    # Inserir no BD
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO rd (
            id, solicitante, funcionario, data, centro_custo,
            valor, status, arquivos, valor_liberado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
    ''', (custom_id, solicitante, funcionario, data, centro_custo, valor, 'Pendente', arquivos_str))
    conn.commit()
    conn.close()

    # Upload do database.db no R2 (backup)
    upload_db_to_r2()

    flash('RD adicionada com sucesso.')
    return redirect(url_for('index'))

@app.route('/edit_form/<id>', methods=['GET'])
def edit_form(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=?", (id,))
    rd_data = cursor.fetchone()
    conn.close()

    if not rd_data:
        return "RD não encontrada", 404

    status = rd_data[6]  # Campo status
    if not can_edit(status):
        return "Acesso negado", 403

    return render_template('edit_form.html', rd=rd_data)

@app.route('/edit_submit/<id>', methods=['POST'])
def edit_submit(id):
    if not can_edit_status(id):
        return "Acesso negado", 403

    solicitante = request.form['solicitante']
    funcionario = request.form['funcionario']
    data = request.form['data']
    centro_custo = request.form['centro_custo']
    valor = float(request.form['valor'])

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    arquivos = rd[0].split(',') if (rd and rd[0]) else []

    # Se vierem novos arquivos
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(local_path)
                upload_file_to_r2(local_path, filename)
                arquivos.append(filename)

    arquivos_str = ','.join(arquivos) if arquivos else None

    cursor.execute('''
        UPDATE rd
        SET solicitante=?, funcionario=?, data=?,
            centro_custo=?, valor=?, arquivos=?
        WHERE id=?
    ''', (solicitante, funcionario, data, centro_custo, valor, arquivos_str, id))
    conn.commit()
    conn.close()

    # Upload do database.db (backup)
    upload_db_to_r2()

    flash('RD atualizada com sucesso.')
    return redirect(url_for('index'))

@app.route('/approve/<id>', methods=['POST'])
def approve(id):
    """Pendente->Aprovado (Gestor), Aprovado->Liberado (Financeiro).
       Ao liberar, subtrai apenas a diferença não-liberada do saldo global.
    """
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional, valor_liberado FROM rd WHERE id=?", (id,))
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
        # Pendente -> Aprovado
        new_status = 'Aprovado'
        cursor.execute(
            "UPDATE rd SET status=?, aprovado_data=? WHERE id=?",
            (new_status, current_date, id)
        )

    elif status == 'Aprovado' and is_financeiro():
        # Aprovado -> Liberado
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
            "UPDATE rd SET status=?, liberado_data=?, valor_liberado=? WHERE id=?",
            (new_status, current_date, valor_liberado, id)
        )
    else:
        conn.close()
        flash("Não é possível aprovar/liberar esta RD.")
        return redirect(url_for('index'))

    conn.commit()
    conn.close()

    # Upload do database.db (backup)
    upload_db_to_r2()

    flash('Operação realizada com sucesso.')
    return redirect(url_for('index'))

@app.route('/delete/<id>', methods=['POST'])
def delete_rd(id):
    """Se RD estava Liberado, devolve ao saldo o valor_liberado."""
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT solicitante, status, valor_liberado FROM rd WHERE id=?", (id,))
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

    if rd_status == 'Liberado' and rd_liberado and rd_liberado > 0:
        saldo = get_saldo_global()
        set_saldo_global(saldo + rd_liberado)

    # Excluir arquivos associados
    cursor.execute("SELECT arquivos FROM rd WHERE id=?", (id,))
    arquivos_str = cursor.fetchone()[0]
    if arquivos_str:
        for arquivo in arquivos_str.split(','):
            # Remove do disco local
            arquivo_path = os.path.join(app.config['UPLOAD_FOLDER'], arquivo)
            if os.path.exists(arquivo_path):
                os.remove(arquivo_path)
            # Remove do R2
            delete_file_from_r2(arquivo)

    # Deletar do BD
    cursor.execute("DELETE FROM rd WHERE id=?", (id,))
    conn.commit()
    conn.close()

    # Upload do database.db (backup)
    upload_db_to_r2()

    flash('RD excluída com sucesso.')
    return redirect(url_for('index'))

@app.route('/adicional_submit/<id>', methods=['POST'])
def adicional_submit(id):
    """Solicita adicional: volta a RD para Pendente e soma valor_adicional,
       sem devolver nada ao saldo.
    """
    if not can_request_additional_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Se houver arquivos, faz upload
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=?", (id,))
    rd_atual = cursor.fetchone()
    arquivos_atuais = rd_atual[0].split(',') if (rd_atual and rd_atual[0]) else []

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(local_path)
                upload_file_to_r2(local_path, filename)
                arquivos_atuais.append(filename)

    arquivos_atuais_str = ','.join(arquivos_atuais) if arquivos_atuais else None
    cursor.execute("UPDATE rd SET arquivos=? WHERE id=?", (arquivos_atuais_str, id))
    conn.commit()
    conn.close()

    try:
        valor_adicional_novo = float(request.form['valor_adicional'])
    except (ValueError, KeyError):
        flash('Valor adicional inválido.')
        return redirect(url_for('index'))

    # Atualiza RD: status = Pendente, soma valor_adicional
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor_adicional FROM rd WHERE id=?", (id,))
    rd2 = cursor.fetchone()
    if not rd2:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status_atual, valor_adic_atual = rd2

    if not can_request_additional(status_atual):
        conn.close()
        flash("Não é possível solicitar adicional neste momento.")
        return redirect(url_for('index'))

    novo_valor_adic = (valor_adic_atual or 0) + valor_adicional_novo
    adicional_data = datetime.now().strftime('%Y-%m-%d')

    cursor.execute("""
        UPDATE rd
        SET valor_adicional=?, adicional_data=?, status='Pendente'
        WHERE id=?
    """, (novo_valor_adic, adicional_data, id))
    conn.commit()
    conn.close()

    # Upload do database.db (backup)
    upload_db_to_r2()

    flash('Crédito adicional solicitado com sucesso (sem devolver saldo).')
    return redirect(url_for('index'))

@app.route('/fechamento_submit/<id>', methods=['POST'])
def fechamento_submit(id):
    """No fechamento, devolve (valor_liberado - valor_despesa) ao saldo.
       E também faz upload de arquivos, caso enviados.
    """
    if not can_close_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # 1) Upload de arquivos no fechamento
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=?", (id,))
    rd_atual = cursor.fetchone()
    arquivos_atuais = rd_atual[0].split(',') if (rd_atual and rd_atual[0]) else []

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(local_path)
                upload_file_to_r2(local_path, filename)
                arquivos_atuais.append(filename)

    arquivos_atuais_str = ','.join(arquivos_atuais) if arquivos_atuais else None
    cursor.execute("UPDATE rd SET arquivos=? WHERE id=?", (arquivos_atuais_str, id))
    conn.commit()
    conn.close()

    # 2) Agora, valida valor da despesa
    try:
        valor_despesa = float(request.form['valor_despesa'])
    except (ValueError, KeyError):
        flash('Valor da despesa inválido.')
        return redirect(url_for('index'))

    # 3) Verificar se pode fechar
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor_liberado FROM rd WHERE id=?", (id,))
    rd2 = cursor.fetchone()
    if not rd2:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status_atual, valor_liberado = rd2

    if not can_close(status_atual):
        conn.close()
        flash("Não é possível fechar esta RD neste momento.")
        return redirect(url_for('index'))

    if valor_liberado < valor_despesa:
        conn.close()
        flash("Valor da despesa maior que o valor liberado.")
        return redirect(url_for('index'))

    # 4) Devolver diferença ao saldo
    saldo_devolver = valor_liberado - valor_despesa
    saldo = get_saldo_global()
    set_saldo_global(saldo + saldo_devolver)

    data_fechamento = datetime.now().strftime('%Y-%m-%d')

    # 5) Atualiza RD como Fechada
    cursor.execute("""
        UPDATE rd
        SET valor_despesa=?, saldo_devolver=?,
            data_fechamento=?, status='Fechado'
        WHERE id=?
    """, (valor_despesa, saldo_devolver, data_fechamento, id))
    conn.commit()
    conn.close()

    # Upload do database.db (backup)
    upload_db_to_r2()

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

    # Upload do database.db (backup)
    upload_db_to_r2()

    flash('Saldo Global atualizado com sucesso.')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/delete_file/<id>', methods=['POST'])
def delete_file(id):
    filename = request.form.get('filename')
    if not filename:
        flash('Nenhum arquivo especificado para exclusão.')
        return redirect(request.referrer or url_for('index'))

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=?", (id,))
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

    # Remove local
    arquivo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(arquivo_path):
        os.remove(arquivo_path)
    else:
        flash('Arquivo não encontrado no servidor.')

    # Remove do R2
    delete_file_from_r2(filename)

    # Atualiza no BD
    arquivos.remove(filename)
    updated_arquivos = ','.join(arquivos) if arquivos else None
    cursor.execute("UPDATE rd SET arquivos=? WHERE id=?", (updated_arquivos, id))
    conn.commit()
    conn.close()

    # Upload do database.db (backup)
    upload_db_to_r2()

    flash('Arquivo excluído com sucesso.')
    return redirect(request.referrer or url_for('index'))

# Helpers para checar status
def can_edit_status(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    status = rd[0]
    return can_edit(status)

def can_request_additional_status(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    status = rd[0]
    return can_request_additional(status)

def can_close_status(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    conn.close()
    if not rd:
        return False
    status = rd[0]
    return can_close(status)

# ------------------------------------------------------------
# Rota para exportar em Excel
# ------------------------------------------------------------
@app.route('/export_excel', methods=['GET'])
def export_excel():
    """Exporta todas as RDs para Excel."""
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd ORDER BY id ASC")
    rd_list = cursor.fetchall()

    saldo_global = get_saldo_global()

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
        "Saldo Global Atual"
    ]
    for col, h in enumerate(header):
        worksheet.write(0, col, h)

    row_number = 1
    # Indices para cada campo na tabela:
    # (id, solicitante, funcionario, data, centro_custo, valor, status,
    #  valor_adicional, adicional_data, valor_despesa, saldo_devolver,
    #  data_fechamento, arquivos, aprovado_data, liberado_data, valor_liberado)
    for rd_row in rd_list:
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

# ------------------------------------------------------------
# Função principal: baixar DB do R2, iniciar DB e rodar
# ------------------------------------------------------------
def main():
    # Tenta baixar database.db do R2 (caso exista)
    download_db_from_r2()
    # Inicia/cria tabelas
    init_db()
    # Roda a aplicação
    app.run(host="0.0.0.0", port=5000, debug=True)

# ------------------------------------------------------------
# Executa se for chamado diretamente
# ------------------------------------------------------------
if __name__ == '__main__':
    main()
