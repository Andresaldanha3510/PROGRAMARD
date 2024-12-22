from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, abort, flash
import sqlite3
import os
from datetime import datetime
import boto3

# Definindo as credenciais do Cloudflare R2 diretamente no código
R2_ACCESS_KEY = 'f1893b9eac9e40f8b992ef50c2b657ca'
R2_SECRET_KEY = '7ec391a97968077b15a9b1b886d803c5b6f6b9f8705bfb55c0ff7a7082132b5c'
R2_ENDPOINT = 'https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com'
R2_BUCKET_NAME = 'meu-bucket-r2'

# Configuração do Flask
app = Flask(__name__)
app.secret_key = 'chave_secreta'

# Configuração do diretório de uploads
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Configuração do cliente S3 com as credenciais do Cloudflare R2
r2_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
)

def upload_file_to_r2(file, filename):
    """Função para enviar o arquivo para o Cloudflare R2."""
    try:
        r2_client.upload_fileobj(file, R2_BUCKET_NAME, filename)
        return True
    except Exception as e:
        print(f"Erro ao enviar o arquivo para o R2: {e}")
        return False

# Funções de banco de dados
def init_db():
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

    cursor.execute(''' 
        CREATE TABLE IF NOT EXISTS saldo_global (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            saldo REAL DEFAULT 30000
        )
    ''')

    cursor.execute('SELECT COUNT(*) FROM saldo_global')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO saldo_global (saldo) VALUES (30000)')

    conn.commit()
    conn.close()

# Funções de controle de permissões
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
    formatted = f"{value:,.2f}"
    parts = formatted.split('.')
    left = parts[0].replace(',', '.')
    right = parts[1]
    return f"{left},{right}"

# Rotas do Flask
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
                
                # Salvar arquivo localmente
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                arquivos.append(filename)
                
                # Enviar arquivo para o Cloudflare R2
                upload_file_to_r2(file, filename)  # Envia para o R2 também

    arquivos_str = ','.join(arquivos) if arquivos else None

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO rd (
            id, solicitante, funcionario, data, centro_custo, valor, status, arquivos
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (custom_id, solicitante, funcionario, data, centro_custo, valor, 'Pendente', arquivos_str))
    conn.commit()
    conn.close()
    flash('RD adicionada com sucesso.')
    return redirect(url_for('index'))

@app.route('/edit_form/<id>', methods=['GET'])
def edit_form(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    conn.close()

    if not rd:
        return "RD não encontrada", 404
    status = rd[6]
    if not can_edit(status):
        return "Acesso negado", 403

    return render_template('edit_form.html', rd=rd)

@app.route('/edit_submit/<id>', methods=['POST'])
def edit_submit(id):
    if not can_edit_status(id):
        return "Acesso negado", 403

    solicitante = request.form['solicitante']
    funcionario = request.form['funcionario']
    data = request.form['data']
    centro_custo = request.form['centro_custo']
    valor = float(request.form['valor'])

    # Gerenciar novos arquivos
    arquivos = []
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT arquivos FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    if rd and rd[0]:
        arquivos = rd[0].split(',')

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{id}_{file.filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                arquivos.append(filename)

                # Enviar arquivo para o Cloudflare R2
                upload_file_to_r2(file, filename)  # Envia para o R2 também

    arquivos_str = ','.join(arquivos) if arquivos else None

    cursor.execute('''
        UPDATE rd
        SET solicitante=?, funcionario=?, data=?, centro_custo=?, valor=?, arquivos=?
        WHERE id=?
    ''', (solicitante, funcionario, data, centro_custo, valor, arquivos_str, id))
    conn.commit()
    conn.close()
    flash('RD atualizada com sucesso.')
    return redirect(url_for('index'))

@app.route('/approve/<id>', methods=['POST'])
def approve(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status, valor, valor_adic = rd
    if not can_approve(status):
        conn.close()
        flash("Acesso negado.")
        return redirect(url_for('index'))

    current_date = datetime.now().strftime('%Y-%m-%d')

    # Fluxo de aprovação:
    # Pendente -> Aprovado (Gestor)
    # Aprovado -> Liberado (Financeiro)
    if status == 'Pendente' and is_gestor():
        new_status = 'Aprovado'
        aprovado_data = current_date
        cursor.execute("UPDATE rd SET status=?, aprovado_data=? WHERE id=?", (new_status, aprovado_data, id))
    elif status == 'Aprovado' and is_financeiro():
        new_status = 'Liberado'
        liberado_data = current_date
        # Subtrair valor_total do saldo global
        total = valor + (valor_adic if valor_adic else 0)
        saldo = get_saldo_global()
        if total > saldo:
            conn.close()
            flash('Saldo global insuficiente.')
            return redirect(url_for('index'))
        set_saldo_global(saldo - total)
        cursor.execute("UPDATE rd SET status=?, liberado_data=? WHERE id=?", (new_status, liberado_data, id))
    else:
        conn.close()
        flash("Não é possível aprovar esta RD.")
        return redirect(url_for('index'))

    conn.commit()
    conn.close()
    flash('RD aprovada com sucesso.')
    return redirect(url_for('index'))

@app.route('/delete/<id>', methods=['POST'])
def delete_rd(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT solicitante, status, valor, valor_adicional FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    rd_solicitante, rd_status, rd_valor, rd_valad = rd

    if not can_delete(rd_status, rd_solicitante):
        conn.close()
        flash("Acesso negado.")
        return redirect(url_for('index'))

    # Se RD estava 'Liberado', devolver o valor total ao saldo global antes de deletar
    if rd_status == 'Liberado':
        total = rd_valor + (rd_valad if rd_valad else 0)
        saldo = get_saldo_global()
        set_saldo_global(saldo + total)

    # Deletar arquivos associados
    cursor.execute("SELECT arquivos FROM rd WHERE id=?", (id,))
    arquivos = cursor.fetchone()[0]
    if arquivos:
        for arquivo in arquivos.split(','):
            arquivo_path = os.path.join(app.config['UPLOAD_FOLDER'], arquivo)
            if os.path.exists(arquivo_path):
                os.remove(arquivo_path)

    cursor.execute("DELETE FROM rd WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash('RD excluída com sucesso.')
    return redirect(url_for('index'))

@app.route('/adicional_submit/<id>', methods=['POST'])
def adicional_submit(id):
    if not can_request_additional_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    try:
        valor_adicional = float(request.form['valor_adicional'])
    except (ValueError, KeyError):
        flash('Valor adicional inválido.')
        return redirect(url_for('index'))

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status, valor, valor_adic_atual = rd

    if not can_request_additional(status):
        conn.close()
        flash("Não é possível solicitar adicional neste momento.")
        return redirect(url_for('index'))

    # Estamos em Liberado. Precisamos voltar para Pendente.
    # Isso implica devolver o valor total atual ao saldo global, já que antes foi subtraído ao liberar.
    total_atual = valor + (valor_adic_atual if valor_adic_atual else 0)
    saldo = get_saldo_global()
    # Devolve o total atual ao saldo, pois vamos recomeçar o processo
    set_saldo_global(saldo + total_atual)

    # Atualiza a RD para adicionar o valor adicional e status 'Pendente'
    novo_valor_adic = valor_adic_atual + valor_adicional if valor_adic_atual else valor_adicional
    adicional_data = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("UPDATE rd SET valor_adicional=?, adicional_data=?, status='Pendente' WHERE id=?",
                   (novo_valor_adic, adicional_data, id))
    conn.commit()
    conn.close()
    flash('Crédito adicional solicitado com sucesso.')
    return redirect(url_for('index'))

@app.route('/fechamento_submit/<id>', methods=['POST'])
def fechamento_submit(id):
    if not can_close_status(id):
        flash("Acesso negado.")
        return redirect(url_for('index'))

    try:
        valor_despesa = float(request.form['valor_despesa'])
    except (ValueError, KeyError):
        flash('Valor da despesa inválido.')
        return redirect(url_for('index'))

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, valor, valor_adicional FROM rd WHERE id=?", (id,))
    rd = cursor.fetchone()
    if not rd:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status, valor, valor_adic = rd

    if not can_close(status):
        conn.close()
        flash("Não é possível fechar esta RD neste momento.")
        return redirect(url_for('index'))

    valor_total = valor + (valor_adic if valor_adic else 0)
    saldo_devolver = valor_total - valor_despesa
    if saldo_devolver < 0:
        conn.close()
        flash("Valor da despesa maior que o valor total da RD.")
        return redirect(url_for('index'))

    # Devolve a diferença ao saldo global
    saldo = get_saldo_global()
    set_saldo_global(saldo + saldo_devolver)

    data_fechamento = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("UPDATE rd SET valor_despesa=?, saldo_devolver=?, data_fechamento=?, status='Fechado' WHERE id=?",
                   (valor_despesa, saldo_devolver, data_fechamento, id))
    conn.commit()
    conn.close()
    flash('RD fechada com sucesso.')
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

    # Remove o arquivo do sistema de arquivos
    arquivo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(arquivo_path):
        os.remove(arquivo_path)
    else:
        flash('Arquivo não encontrado no servidor.')

    # Remove o arquivo da lista e atualiza o banco de dados
    arquivos.remove(filename)
    updated_arquivos = ','.join(arquivos) if arquivos else None
    cursor.execute("UPDATE rd SET arquivos=? WHERE id=?", (updated_arquivos, id))
    conn.commit()
    conn.close()

    flash('Arquivo excluído com sucesso.')
    return redirect(request.referrer or url_for('index'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)

