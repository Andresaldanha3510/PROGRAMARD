import os
import psycopg2
import io
import xlsxwriter
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from dotenv import load_dotenv

load_dotenv()

# Imports para Cloudflare R2
import boto3
from botocore.client import Config

# Estas são as keys do R2 (Cloudflare) com valores-padrão
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

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
# SECRET_KEY
secret_key = os.getenv('SECRET_KEY', 'secret123')
app.secret_key = secret_key
logging.debug("SECRET_KEY carregado corretamente.")

# Configurações do PostgreSQL (com valores-padrão)
PG_HOST = os.getenv('PG_HOST', 'localhost')
PG_PORT = os.getenv('PG_PORT', '5432')
PG_DB   = os.getenv('PG_DB', 'postgres')
PG_USER = os.getenv('PG_USER', 'postgres')
PG_PASSWORD = os.getenv('PG_PASSWORD', 'postgres')

def get_pg_connection():
    """Retorna uma conexão com o PostgreSQL."""
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
    """Inicializa (cria/atualiza) as tabelas necessárias no PostgreSQL."""
    conn = get_pg_connection()
    cursor = conn.cursor()

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
        unidade_negocio TEXT,
        motivo_recusa TEXT,
        adicionais_individuais TEXT,
        divergencia_anexos BOOLEAN DEFAULT false,
        motivo_divergencia TEXT
    );
    """
    cursor.execute(create_rd_table)

    # Cria colunas extras se necessário
    extras = [
        ("valor_liberado", "ALTER TABLE rd ADD COLUMN valor_liberado NUMERIC(15,2) DEFAULT 0;"),
        ("observacao", "ALTER TABLE rd ADD COLUMN observacao TEXT;"),
        ("tipo", "ALTER TABLE rd ADD COLUMN tipo TEXT DEFAULT 'credito alelo';"),
        ("data_saldo_devolvido", "ALTER TABLE rd ADD COLUMN data_saldo_devolvido DATE;"),
        ("unidade_negocio", "ALTER TABLE rd ADD COLUMN unidade_negocio TEXT;"),
        ("motivo_recusa", "ALTER TABLE rd ADD COLUMN motivo_recusa TEXT;"),
        ("adicionais_individuais", "ALTER TABLE rd ADD COLUMN adicionais_individuais TEXT;"),
        ("divergencia_anexos", "ALTER TABLE rd ADD COLUMN divergencia_anexos BOOLEAN DEFAULT false;"),
        ("motivo_divergencia", "ALTER TABLE rd ADD COLUMN motivo_divergencia TEXT;")
    ]
    for col_name, alter_sql in extras:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='rd' AND column_name=%s;", (col_name,))
        if not cursor.fetchone():
            cursor.execute(alter_sql)

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

    create_func_table = """
    CREATE TABLE IF NOT EXISTS funcionarios (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        centro_custo TEXT NOT NULL,
        unidade_negocio TEXT NOT NULL
    );
    """
    cursor.execute(create_func_table)

    conn.commit()
    cursor.close()
    conn.close()

def generate_custom_id():
    """Gera um ID customizado (ex.: 401.23)."""
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
    return session.get('user_role') == 'solicitante'

def is_gestor():
    return session.get('user_role') == 'gestor'

def is_financeiro():
    return session.get('user_role') == 'financeiro'

def is_supervisor():
    return session.get('user_role') == 'supervisor'

def can_add():
    # Supervisor não cria RD
    return user_role() in ['solicitante', 'gestor', 'financeiro']

def can_edit(status):
    if status=='Fechado':
        return False
    if is_solicitante():
        return status in ['Pendente','Fechamento Recusado']
    if is_gestor() or is_financeiro():
        return True
    return False

def can_delete(status, solicitante):
    if status=='Fechado':
        return False
    if status=='Pendente' and is_solicitante():
        return True
    if (is_gestor() or is_financeiro()) and status in ['Pendente','Aprovado','Liberado']:
        return True
    return False

def can_approve(status):
    # Gestor aprova pendentes e fecha Fechamento Solicitado
    # Financeiro libera Aprovado
    if status=='Pendente' and is_gestor():
        return True
    if status=='Fechamento Solicitado' and is_gestor():
        return True
    if status=='Aprovado' and is_financeiro():
        return True
    return False

def can_request_additional(status):
    return (is_solicitante() and status=='Liberado')

def can_close(status):
    return (is_solicitante() and status=='Liberado')

def get_saldo_global():
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT saldo FROM saldo_global LIMIT 1")
    s = cursor.fetchone()[0]
    conn.close()
    return s

def set_saldo_global(new_s):
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE saldo_global SET saldo=%s WHERE id=1",(new_s,))
    conn.commit()
    cursor.close()
    conn.close()

def format_currency(value):
    if value is None:
        return "0,00"
    s = f"{value:,.2f}"
    parts = s.split('.')
    left = parts[0].replace(',', '.')
    right = parts[1]
    return f"{left},{right}"

def mostrar_valores(rd):
    """Exibe valores com \n ao invés de <br>."""
    valor = rd[5] if rd[5] else 0
    val_adic = rd[7] if rd[7] else 0
    total_cred = valor + val_adic
    val_desp = rd[9] if rd[9] else 0
    saldo_dev = rd[10] if rd[10] else (total_cred - val_desp)
    lines = [
        f"Valor: R${format_currency(valor)}",
        f"Adicional: R${format_currency(val_adic)}",
        f"Total: R${format_currency(total_cred)}",
        f"Despesa: R${format_currency(val_desp)}",
        f"Saldo a Devolver: R${format_currency(saldo_dev)}"
    ]
    return "\n".join(lines)

app.jinja_env.globals.update(
    get_r2_public_url=get_r2_public_url,
    is_gestor=is_gestor,
    is_solicitante=is_solicitante,
    is_financeiro=is_financeiro,
    is_supervisor=is_supervisor,
    mostrar_valores=mostrar_valores
)

init_db()

@app.route('/', methods=['GET','POST'])
def index():
    if request.method=='POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        if username=='gestor' and password=='115289':
            session['user_role']='gestor'
            flash("Login como gestor bem-sucedido.")
        elif username=='financeiro' and password=='351073':
            session['user_role']='financeiro'
            flash("Login como financeiro bem-sucedido.")
        elif username=='solicitante' and password=='102030':
            session['user_role']='solicitante'
            flash("Login como solicitante bem-sucedido.")
        elif username=='supervisor' and password=='334455':
            session['user_role']='supervisor'
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
    cursor.execute("SELECT * FROM rd WHERE status='Fechamento Solicitado'")
    fechamento_solicitado = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Fechamento Recusado'")
    fechamento_recusado = cursor.fetchall()
    cursor.execute("SELECT * FROM rd WHERE status='Fechado'")
    fechados = cursor.fetchall()

    saldo_global = get_saldo_global()
    adicional_id = request.args.get('adicional')
    fechamento_id = request.args.get('fechamento')

    divergentes=[]
    if is_supervisor():
        cursor.execute("""
          SELECT id,data,motivo_divergencia
          FROM rd
          WHERE divergencia_anexos=true
            AND status='Liberado'
        """)
        divergentes=cursor.fetchall()

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
        fechamento_id=fechamento_id,
        divergentes=divergentes
    )

@app.route('/add', methods=['POST'])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado",403

    solicitante     = request.form['solicitante'].strip()
    funcionario     = request.form['funcionario'].strip()
    data            = request.form['data'].strip()
    centro_custo    = request.form['centro_custo'].strip()
    observacao      = request.form.get('observacao','').strip()
    rd_tipo         = request.form.get('tipo','credito alelo').strip()
    unidade_negocio = request.form.get('unidade_negocio','').strip()

    try:
        valor = float(request.form['valor'].replace(',','.'))
    except ValueError:
        flash("Valor inválido.")
        return redirect(url_for('index'))

    custom_id = generate_custom_id()
    arquivos=[]
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
        id,solicitante,funcionario,data,centro_custo,
        valor,status,arquivos,valor_liberado,observacao,
        tipo,unidade_negocio
      )
      VALUES (%s,%s,%s,%s,%s,
              %s,%s,%s,0,%s,
              %s,%s)
    """,(custom_id,solicitante,funcionario,data,centro_custo,
         valor,'Pendente',arquivos_str,observacao,
         rd_tipo,unidade_negocio))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD adicionada com sucesso.")
    return redirect(url_for('index'))

def can_edit_status(id):
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    conn.close()
    if not row:
        return False
    return can_edit(row[0])

@app.route('/edit_form/<id>', methods=['GET'])
def edit_form(id):
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT * FROM rd WHERE id=%s",(id,))
    rd=cursor.fetchone()
    conn.close()
    if not rd:
        flash("RD não encontrada.")
        return "RD não encontrada",404

    if not can_edit(rd[6]):
        flash("Acesso negado.")
        return "Acesso negado",403

    return render_template('edit_form.html',rd=rd)

@app.route('/edit_submit/<id>', methods=['POST'])
def edit_submit(id):
    if not can_edit_status(id):
        flash("Acesso negado.")
        return "Acesso negado",403

    solicitante = request.form['solicitante'].strip()
    funcionario = request.form['funcionario'].strip()
    data = request.form['data'].strip()
    centro_custo = request.form['centro_custo'].strip()
    observacao = request.form.get('observacao','').strip()
    unidade_negocio = request.form.get('unidade_negocio','').strip()

    try:
        valor = float(request.form['valor'].replace(',','.'))
    except ValueError:
        flash("Valor inválido.")
        return redirect(url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT status,arquivos FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    original_status = row[0]
    arqs = row[1].split(',') if row[1] else []

    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                filename=f"{id}_{file.filename}"
                upload_file_to_r2(file,filename)
                arqs.append(filename)
    updated_arqs = ','.join(arqs) if arqs else None

    cursor.execute("""
      UPDATE rd
      SET solicitante=%s,funcionario=%s,data=%s,
          centro_custo=%s,valor=%s,
          arquivos=%s,observacao=%s,
          unidade_negocio=%s
      WHERE id=%s
    """,(solicitante,funcionario,data,
         centro_custo,valor,
         updated_arqs,observacao,
         unidade_negocio,id))

    # Se estava em Fechamento Recusado e é solicitante => volta para Fechamento Solicitado
    if is_solicitante() and original_status=='Fechamento Recusado':
        cursor.execute("UPDATE rd SET status='Fechamento Solicitado',motivo_recusa=NULL WHERE id=%s",(id,))

    conn.commit()
    cursor.close()
    conn.close()
    flash("RD atualizada com sucesso.")
    return redirect(url_for('index'))

@app.route('/approve/<id>', methods=['POST'])
def approve(id):
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT status,valor,valor_adicional,tipo FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    status,valor,valor_adic,rd_tipo = row

    if not can_approve(status):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for('index'))

    cdate = datetime.now().strftime('%Y-%m-%d')
    if status=='Pendente' and is_gestor():
        new_status='Aprovado'
        cursor.execute("UPDATE rd SET status=%s, aprovado_data=%s WHERE id=%s",(new_status,cdate,id))

    elif status=='Aprovado' and is_financeiro():
        if rd_tipo.lower()=='reembolso':
            new_status='Fechado'
            cursor.execute("""
              UPDATE rd
              SET status=%s,data_fechamento=%s
              WHERE id=%s
            """,(new_status,cdate,id))
        else:
            new_status='Liberado'
            cursor.execute("""
              UPDATE rd
              SET status=%s,liberado_data=%s
              WHERE id=%s
            """,(new_status,cdate,id))

    elif status=='Fechamento Solicitado' and is_gestor():
        new_status='Fechado'
        cursor.execute("UPDATE rd SET status=%s WHERE id=%s",(new_status,id))
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
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT solicitante,status,valor_liberado FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    rd_solic,rd_status,rd_liberado = row

    if not can_delete(rd_status,rd_solic):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for('index'))

    if rd_status=='Liberado' and rd_liberado and rd_liberado>0:
        saldo = get_saldo_global()
        set_saldo_global(saldo+rd_liberado)

    cursor.execute("SELECT arquivos FROM rd WHERE id=%s",(id,))
    arqrow=cursor.fetchone()
    if arqrow and arqrow[0]:
        for f in arqrow[0].split(','):
            delete_file_from_r2(f)
    cursor.execute("DELETE FROM rd WHERE id=%s",(id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD excluída com sucesso.")
    return redirect(url_for('index'))

@app.route('/adicional_submit/<id>', methods=['POST'])
def adicional_submit(id):
    # Anexa arquivos
    if 'arquivo' in request.files:
        conn=get_pg_connection()
        cursor=conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s",(id,))
        rw=cursor.fetchone()
        existing = rw[0].split(',') if rw and rw[0] else []
        for f in request.files.getlist('arquivo'):
            if f.filename:
                filename = f"{id}_{f.filename}"
                upload_file_to_r2(f, filename)
                existing.append(filename)
        up_str = ','.join(existing) if existing else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s",(up_str,id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        novo_valor_adic = float(request.form['valor_adicional'].replace(',','.'))
    except ValueError:
        flash("Valor adicional inválido.")
        return redirect(url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT status,valor_adicional,adicionais_individuais FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    st_atual, val_adic, adic_ind = row

    if not can_request_additional(st_atual):
        conn.close()
        flash("Não é possível solicitar adicional agora.")
        return redirect(url_for('index'))

    soma = (val_adic or 0) + novo_valor_adic
    if adic_ind:
        sp = [x.strip() for x in adic_ind.split(',')]
        i_next = len(sp)+1
        new_ind = adic_ind + f", Adicional {i_next}:{novo_valor_adic}"
    else:
        new_ind = f"Adicional 1:{novo_valor_adic}"
    data_add = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
      UPDATE rd
      SET valor_adicional=%s, adicional_data=%s, status='Pendente',
          adicionais_individuais=%s
      WHERE id=%s
    """,(soma,data_add,new_ind,id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Crédito adicional solicitado. A RD voltou para 'Pendente'.")
    return redirect(url_for('index'))

@app.route('/fechamento_submit/<id>', methods=['POST'])
def fechamento_submit(id):
    # Anexa arquivos (opcional)
    if 'arquivo' in request.files:
        conn=get_pg_connection()
        cursor=conn.cursor()
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s",(id,))
        r=cursor.fetchone()
        existing = r[0].split(',') if r and r[0] else []
        for f in request.files.getlist('arquivo'):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                existing.append(fname)
        final_str = ','.join(existing) if existing else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s",(final_str,id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        val_desp = float(request.form['valor_despesa'].replace(',','.'))
    except ValueError:
        flash("Valor da despesa inválido.")
        return redirect(url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT valor,valor_adicional,status FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    val_sol,val_adic,st = row
    if not can_close(st):
        conn.close()
        flash("Não é possível fechar esta RD agora.")
        return redirect(url_for('index'))
    total_cred = (val_sol or 0)+(val_adic or 0)
    if val_desp>total_cred:
        conn.close()
        flash("Despesa maior que o total de créditos.")
        return redirect(url_for('index'))
    saldo_dev = total_cred-val_desp
    data_fech = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
      UPDATE rd
      SET valor_despesa=%s,saldo_devolver=%s,
          data_fechamento=%s,status='Fechamento Solicitado'
      WHERE id=%s
    """,(val_desp,saldo_dev,data_fech,id))
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
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT status FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    if not row or row[0] != 'Fechamento Solicitado':
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for('index'))

    motivo = request.form.get('motivo','').strip()
    if not motivo:
        flash("Informe um motivo para a recusa.")
        return redirect(url_for('index'))

    cursor.execute("""
      UPDATE rd
      SET status='Fechamento Recusado',motivo_recusa=%s
      WHERE id=%s
    """,(motivo,id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento recusado com sucesso.")
    return redirect(url_for('index'))

@app.route('/reenviar_fechamento/<id>', methods=['POST'])
def reenviar_fechamento(id):
    flash("Use o botão 'Corrigir e reenviar' para editar a RD.")
    return redirect(url_for('index'))

@app.route('/edit_saldo', methods=['POST'])
def edit_saldo():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for('index'))
    try:
        ns = float(request.form['saldo_global'].replace(',','.'))
    except:
        flash("Saldo inválido.")
        return redirect(url_for('index'))
    set_saldo_global(ns)
    flash("Saldo Global atualizado com sucesso.")
    return redirect(url_for('index'))

@app.route('/delete_file/<id>', methods=['POST'])
def delete_file(id):
    fn = request.form.get('filename')
    if not fn:
        flash("Nenhum arquivo selecionado para excluir.")
        return redirect(request.referrer or url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT arquivos,status,solicitante FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(request.referrer or url_for('index'))
    arqs_str,rd_status,rd_solic = row

    perm = False
    if is_supervisor() and rd_status=='Liberado':
        perm=True
    elif can_edit(rd_status) or can_delete(rd_status,rd_solic):
        perm=True

    if not perm:
        conn.close()
        flash("Você não pode excluir arquivos desta RD.")
        return redirect(request.referrer or url_for('index'))

    if arqs_str:
        arr = arqs_str.split(',')
        if fn in arr:
            delete_file_from_r2(fn)
            arr.remove(fn)
            up = ','.join(arr) if arr else None
            cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s",(up,id))
            conn.commit()
    cursor.close()
    conn.close()
    flash("Arquivo excluído com sucesso.")
    return redirect(request.referrer or url_for('index'))

@app.route('/supervisor_add_files/<id>', methods=['POST'])
def supervisor_add_files(id):
    if not is_supervisor():
        flash("Acesso negado (supervisor).")
        return redirect(url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT status,arquivos FROM rd WHERE id=%s",(id,))
    row=cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    st,arqs_str=row
    if st!='Liberado':
        conn.close()
        flash("Não é possível adicionar anexos se a RD não está em 'Liberado'.")
        return redirect(url_for('index'))

    existing=arqs_str.split(',') if arqs_str else []
    if 'arquivo' in request.files:
        for file in request.files.getlist('arquivo'):
            if file.filename:
                fname=f"{id}_{file.filename}"
                upload_file_to_r2(file,fname)
                existing.append(fname)
    final_str = ','.join(existing) if existing else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s",(final_str,id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Anexos adicionados com sucesso.")
    return redirect(url_for('index'))

@app.route('/marcar_divergencia/<id>', methods=['POST'])
def marcar_divergencia(id):
    if not (is_gestor() or is_solicitante()):
        flash("Acesso negado para marcar divergência.")
        return redirect(url_for('index'))

    motivo = request.form.get('motivo_div','').strip()
    if not motivo:
        flash("Informe o motivo da divergência.")
        return redirect(url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT id FROM rd WHERE id=%s",(id,))
    if not cursor.fetchone():
        conn.close()
        flash("RD não encontrada para divergência.")
        return redirect(url_for('index'))

    cursor.execute("""
      UPDATE rd
      SET divergencia_anexos=true,
          motivo_divergencia=%s
      WHERE id=%s
    """,(motivo,id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Anexos divergentes marcados.")
    return redirect(url_for('index'))

@app.route('/corrigir_divergencia/<id>', methods=['POST'])
def corrigir_divergencia(id):
    if not is_supervisor():
        flash("Acesso negado para corrigir divergência.")
        return redirect(url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT id FROM rd WHERE id=%s",(id,))
    if not cursor.fetchone():
        conn.close()
        flash("RD não encontrada para divergência.")
        return redirect(url_for('index'))

    cursor.execute("""
      UPDATE rd
      SET divergencia_anexos=false,
          motivo_divergencia=NULL
      WHERE id=%s
    """,(id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Divergência removida com sucesso.")
    return redirect(url_for('index'))

@app.route('/registrar_saldo_devolvido/<id>', methods=['POST'])
def registrar_saldo_devolvido(id):
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for('index'))

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("""
      SELECT valor,valor_adicional,valor_despesa,data_saldo_devolvido
      FROM rd
      WHERE id=%s
    """,(id,))
    row=cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for('index'))
    val,val_adic,vdesp,dsal=row
    if dsal:
        conn.close()
        flash("Saldo já registrado.")
        return redirect(url_for('index'))
    totalc=(val or 0)+(val_adic or 0)
    if (vdesp or 0)>totalc:
        conn.close()
        flash("Despesa maior que o total de créditos.")
        return redirect(url_for('index'))
    saldo_dev=totalc-(vdesp or 0)
    s_atual=get_saldo_global()
    set_saldo_global(s_atual+saldo_dev)
    hj=datetime.now().strftime('%Y-%m-%d')
    cursor.execute("UPDATE rd SET data_saldo_devolvido=%s WHERE id=%s",(hj,id))
    conn.commit()
    cursor.close()
    conn.close()
    flash(f"Saldo devolvido com sucesso. Valor= R${saldo_dev:,.2f}")
    return redirect(url_for('index'))

@app.route('/export_excel', methods=['GET'])
def export_excel():
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT * FROM rd ORDER BY id ASC")
    rd_list=cursor.fetchall()
    saldo_global=get_saldo_global()

    out=io.BytesIO()
    wb=xlsxwriter.Workbook(out,{'in_memory':True})
    ws=wb.add_worksheet('Relatorio')

    header=[
      "Número RD","Data Solicitação","Solicitante","Funcionário","Valor Solicitado",
      "Valor Adicional","Data Adicional","Centro de Custo","Valor Gasto","Saldo a Devolver",
      "Data de Fechamento","Saldo Global"
    ]
    for c,h in enumerate(header):
        ws.write(0,c,h)

    rnum=1
    for r in rd_list:
        rd_id         = r[0]
        rd_data       = r[3]
        rd_solic      = r[1]
        rd_func       = r[2]
        rd_valor      = r[5]
        rd_val_adic   = r[7]
        rd_adic_data  = r[8]
        rd_cc         = r[4]
        rd_desp       = r[9]
        rd_sdev       = r[10]
        rd_data_fech  = r[11]

        ws.write(rnum,0,rd_id)
        ws.write(rnum,1,str(rd_data or ''))
        ws.write(rnum,2,rd_solic)
        ws.write(rnum,3,rd_func)
        ws.write(rnum,4,float(rd_valor or 0))
        ws.write(rnum,5,float(rd_val_adic or 0))
        ws.write(rnum,6,str(rd_adic_data or ''))
        ws.write(rnum,7,rd_cc)
        ws.write(rnum,8,float(rd_desp or 0))
        ws.write(rnum,9,float(rd_sdev or 0))
        ws.write(rnum,10,str(rd_data_fech or ''))
        ws.write(rnum,11,float(saldo_global))
        rnum+=1

    wb.close()
    out.seek(0)
    conn.close()
    return send_file(
        out,
        as_attachment=True,
        download_name=f"Relatorio_RD_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/logout')
def logout():
    session.clear()
    flash("Logout realizado com sucesso.")
    return redirect(url_for('index'))

# Rotas de funcionários
@app.route('/cadastro_funcionario', methods=['GET'])
def cadastro_funcionario():
    return render_template('cadastro_funcionario.html')

@app.route('/cadastrar_funcionario', methods=['POST'])
def cadastrar_funcionario():
    nome = request.form['nome'].strip()
    ccusto = request.form['centroCusto'].strip()
    unid = request.form['unidadeNegocio'].strip()

    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("""
      INSERT INTO funcionarios (nome,centro_custo,unidade_negocio)
      VALUES (%s,%s,%s)
    """,(nome,ccusto,unid))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Funcionário cadastrado com sucesso.")
    return redirect(url_for('cadastro_funcionario'))

@app.route('/consulta_funcionario', methods=['GET'])
def consulta_funcionario():
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT id,nome,centro_custo,unidade_negocio FROM funcionarios ORDER BY id ASC")
    fns=cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('consulta_funcionario.html', funcionarios=fns)

@app.route('/delete_funcionario/<int:id>', methods=['POST'])
def delete_funcionario(id):
    conn=get_pg_connection()
    cursor=conn.cursor()
    cursor.execute("DELETE FROM funcionarios WHERE id=%s",(id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Funcionário excluído com sucesso.")
    return redirect(url_for('consulta_funcionario'))

if __name__=='__main__':
    app.run(debug=True)
