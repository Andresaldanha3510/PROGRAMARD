from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, flash
import sqlite3
import os
from datetime import datetime
import boto3

app = Flask(__name__)
app.secret_key = 'chave_secreta'

# Configuração do Cloudflare R2
R2_ACCESS_KEY = 'f1893b9eac9e40f8b992ef50c2b657ca'
R2_SECRET_KEY = '7ec391a97968077b15a9b1b886d803c5b6f6b9f8705bfb55c0ff7a7082132b5c'
R2_ENDPOINT = 'https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com'
R2_BUCKET_NAME = 'meu-bucket-r2'

# Configuração do cliente boto3
r2_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)

# Configuração de upload local (se necessário para arquivos temporários)
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Função para enviar arquivos ao Cloudflare R2
def upload_file_to_r2(file, filename):
    try:
        r2_client.upload_fileobj(file, R2_BUCKET_NAME, filename)
        print(f"Arquivo {filename} enviado para o Cloudflare R2 com sucesso!")
        return filename
    except Exception as e:
        print(f"Erro ao enviar o arquivo para o R2: {e}")
        return None

# Função para excluir arquivos do Cloudflare R2
def delete_file_from_r2(filename):
    try:
        r2_client.delete_object(Bucket=R2_BUCKET_NAME, Key=filename)
        print(f"Arquivo {filename} excluído do Cloudflare R2 com sucesso!")
    except Exception as e:
        print(f"Erro ao excluir o arquivo do R2: {e}")

# Inicializa o banco de dados
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

# Gera IDs customizados
def generate_custom_id():
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
            return render_template('index.html', error="Credenciais inválidas")
        return redirect(url_for('index'))

    if 'user_role' not in session:
        return render_template('index.html', error=None)

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
    conn.close()

    return render_template('index.html',
                           pendentes=pendentes,
                           aprovados=aprovados,
                           liberados=liberados,
                           fechados=fechados,
                           saldo_global=saldo_global)

@app.route('/add', methods=['POST'])
def add_rd():
    solicitante = request.form['solicitante']
    funcionario = request.form['funcionario']
    data = request.form['data']
    centro_custo = request.form['centro_custo']
    valor = float(request.form['valor'])
    custom_id = generate_custom_id()

    arquivos = []
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename = f"{custom_id}_{file.filename}"
                upload_result = upload_file_to_r2(file, filename)
                if upload_result:
                    arquivos.append(upload_result)

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

    delete_file_from_r2(filename)
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
