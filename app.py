from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'chave_secreta'

# Configuração do caminho de uploads
if os.getenv('DYNO'):  # Detecta se está rodando no Heroku
    UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')  # Caminho relativo para Heroku
else:
    UPLOAD_FOLDER = r'C:\Users\financeiro01\Desktop\programa RD'  # Caminho local

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Certifique-se de que a pasta existe
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rd (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            data_fechamento TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS anexos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rd_id INTEGER NOT NULL,
            arquivo TEXT NOT NULL,
            caminho TEXT,
            FOREIGN KEY (rd_id) REFERENCES rd (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

def can_approve():
    return 'user_role' in session and session['user_role'] in ['gestor', 'financeiro']

def can_release():
    return 'user_role' in session and session['user_role'] == 'financeiro'

def can_add():
    return 'user_role' in session

def can_delete():
    return 'user_role' in session

def can_request_additional():
    return 'user_role' in session

def is_solicitante():
    return 'user_role' in session and session['user_role'] == 'solicitante'

@app.route('/', methods=['GET', 'POST'])
def index():
    # Login
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
            return render_template('index.html', error="Credenciais inválidas", adicional_id=None, fechamento_id=None)
        return redirect(url_for('index'))

    if 'user_role' not in session:
        return render_template('index.html', adicional_id=None, fechamento_id=None)

    adicional_id = request.args.get('adicional')
    fechamento_id = request.args.get('fechamento')

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM rd WHERE status = 'Pendente'")
    pendentes = cursor.fetchall()

    cursor.execute("SELECT * FROM rd WHERE status = 'Aprovado'")
    aprovados = cursor.fetchall()

    cursor.execute("SELECT * FROM rd WHERE status = 'Liberado'")
    liberados = cursor.fetchall()

    cursor.execute("SELECT * FROM rd WHERE status = 'Fechado'")
    fechados = cursor.fetchall()

    cursor.execute("SELECT rd_id, arquivo FROM anexos")
    anexos = cursor.fetchall()
    anexos_dict = {}
    for anexo in anexos:
        if anexo[0] not in anexos_dict:
            anexos_dict[anexo[0]] = []
        anexos_dict[anexo[0]].append(anexo[1])

    conn.close()

    return render_template('index.html', 
                           adicional_id=int(adicional_id) if adicional_id else None,
                           fechamento_id=int(fechamento_id) if fechamento_id else None,
                           pendentes=pendentes,
                           aprovados=aprovados,
                           liberados=liberados,
                           fechados=fechados,
                           anexos=anexos_dict,
                           can_approve=can_approve(),
                           can_release=can_release(),
                           can_add=can_add(),
                           can_delete=can_delete(),
                           can_request_additional=can_request_additional(),
                           is_solicitante=is_solicitante(),
                           user_role=session['user_role'])

# Outras rotas (add_rd, approve, release, adicional_submit, fechamento_submit, delete) continuam como no código original

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    app.run(debug=False)


