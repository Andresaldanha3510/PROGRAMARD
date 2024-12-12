from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
import os
from datetime import datetime

# Configuração do aplicativo Flask
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///rd.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')  # Pasta onde os arquivos serão salvos
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'doc', 'docx', 'xlsx'}  # Adicionando 'xlsx'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limite de 16MB para uploads

# Inicialização do banco de dados e SocketIO
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Função para verificar se o arquivo é permitido
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Modelo RD
class RD(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    funcionario = db.Column(db.String(50), nullable=False)
    data_solicitacao = db.Column(db.DateTime, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    centro_custo = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='Pendente')
    numero_rd = db.Column(db.String(20), nullable=True)
    arquivo = db.Column(db.String(100), nullable=True)  # Nome do arquivo armazenado

# Rota para servir o HTML principal
@app.route('/')
def index():
    return render_template('index.html')

# Rota para solicitar uma nova RD com arquivo
@app.route('/solicitar_rd', methods=['POST'])
def solicitar_rd():
    try:
        # Verifica se os campos obrigatórios estão presentes
        required_fields = ['funcionario', 'data_solicitacao', 'valor', 'centro_custo']
        for field in required_fields:
            if field not in request.form:
                return jsonify({'error': f'Campo {field} está faltando.'}), 400

        funcionario = request.form['funcionario']
        data_solicitacao_str = request.form['data_solicitacao']
        valor_str = request.form['valor']
        centro_custo = request.form['centro_custo']

        # Validação básica dos campos
        if not funcionario or not data_solicitacao_str or not valor_str or not centro_custo:
            return jsonify({'error': 'Todos os campos devem ser preenchidos.'}), 400

        try:
            data_solicitacao = datetime.strptime(data_solicitacao_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({'error': 'Formato de data inválido. Use AAAA-MM-DD.'}), 400

        try:
            valor = float(valor_str)
        except ValueError:
            return jsonify({'error': 'Valor inválido.'}), 400

        # Tratamento do arquivo
        arquivo = request.files.get('arquivo')
        filename = None
        if arquivo and allowed_file(arquivo.filename):
            filename = secure_filename(arquivo.filename)
            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
            arquivo.save(upload_path)
            print(f"Arquivo salvo em: {upload_path}")  # Debug
        elif arquivo:
            return jsonify({'error': 'Tipo de arquivo não permitido. Envie PDF, DOC, DOCX ou XLSX.'}), 400

        # Criação da nova RD
        nova_rd = RD(
            funcionario=funcionario,
            data_solicitacao=data_solicitacao,
            valor=valor,
            centro_custo=centro_custo,
            arquivo=filename
        )
        db.session.add(nova_rd)
        db.session.commit()
        print(f"RD criada com ID: {nova_rd.id}")  # Debug

        # Notifica todos os clientes sobre o novo pedido de RD
        socketio.emit('nova_rd', {
            'id': nova_rd.id,
            'funcionario': nova_rd.funcionario,
            'data_solicitacao': nova_rd.data_solicitacao.strftime("%Y-%m-%d"),
            'valor': nova_rd.valor,
            'centro_custo': nova_rd.centro_custo,
            'arquivo': nova_rd.arquivo,
            'status': nova_rd.status
        })
        return jsonify({'id': nova_rd.id, 'status': 'Pendente'}), 201

    except Exception as e:
        # Log para debug
        print(f"Erro ao solicitar RD: {e}")
        return jsonify({'error': 'Erro interno do servidor.'}), 500

# Rota para carregar RDs pendentes e aprovadas
@app.route('/rds_pendentes', methods=['GET'])
def rds_pendentes():
    try:
        rds = RD.query.filter(RD.status.in_(['Pendente', 'Aprovado'])).all()
        rd_list = [
            {
                'id': rd.id,
                'funcionario': rd.funcionario,
                'data_solicitacao': rd.data_solicitacao.strftime("%Y-%m-%d"),
                'valor': rd.valor,
                'centro_custo': rd.centro_custo,
                'arquivo': rd.arquivo,
                'status': rd.status
            }
            for rd in rds
        ]
        return jsonify(rd_list)
    except Exception as e:
        print(f"Erro ao carregar RDs pendentes: {e}")
        return jsonify({'error': 'Erro interno do servidor.'}), 500

# Rota para carregar RDs atendidas
@app.route('/rds_atendidas', methods=['GET'])
def rds_atendidas():
    try:
        rds = RD.query.filter_by(status='Atendido').all()
        rd_list = [
            {
                'id': rd.id,
                'funcionario': rd.funcionario,
                'data_solicitacao': rd.data_solicitacao.strftime("%Y-%m-%d"),
                'valor': rd.valor,
                'centro_custo': rd.centro_custo,
                'numero_rd': rd.numero_rd,
                'arquivo': rd.arquivo,
                'status': rd.status
            }
            for rd in rds
        ]
        return jsonify(rd_list)
    except Exception as e:
        print(f"Erro ao carregar RDs atendidas: {e}")
        return jsonify({'error': 'Erro interno do servidor.'}), 500

# Rota para atender e numerar a RD
@app.route('/atender_rd/<int:rd_id>', methods=['POST'])
def atender_rd(rd_id):
    try:
        rd = RD.query.get(rd_id)
        if not rd:
            return jsonify({'error': 'RD não encontrada.'}), 404

        data = request.get_json()
        if not data or 'numero_rd' not in data:
            return jsonify({'error': 'Número da RD não fornecido.'}), 400

        numero_rd = data['numero_rd']
        if not numero_rd:
            return jsonify({'error': 'Número da RD inválido.'}), 400

        rd.numero_rd = numero_rd
        rd.status = 'Atendido'
        db.session.commit()
        print(f"RD ID {rd.id} atendida com número: {rd.numero_rd}")  # Debug

        # Notifica todos os clientes que a RD foi atendida
        socketio.emit('rd_atendida', {
            'id': rd.id,
            'numero_rd': rd.numero_rd,
            'funcionario': rd.funcionario,
            'data_solicitacao': rd.data_solicitacao.strftime("%Y-%m-%d"),
            'valor': rd.valor,
            'centro_custo': rd.centro_custo,
            'arquivo': rd.arquivo,
            'status': rd.status
        })
        return jsonify({'id': rd.id, 'status': 'Atendido', 'numero_rd': rd.numero_rd}), 200

    except Exception as e:
        print(f"Erro ao atender RD: {e}")
        return jsonify({'error': 'Erro interno do servidor.'}), 500

# Rota para aprovar uma RD
@app.route('/aprovar_rd/<int:rd_id>', methods=['POST'])
def aprovar_rd(rd_id):
    try:
        rd = RD.query.get(rd_id)
        if not rd:
            return jsonify({'error': 'RD não encontrada.'}), 404

        rd.status = 'Aprovado'
        db.session.commit()
        print(f"RD ID {rd.id} aprovada pela gerência")  # Debug

        # Notifica todos os clientes que a RD foi aprovada
        socketio.emit('rd_aprovada', {
            'id': rd.id,
            'funcionario': rd.funcionario,
            'data_solicitacao': rd.data_solicitacao.strftime("%Y-%m-%d"),
            'valor': rd.valor,
            'centro_custo': rd.centro_custo,
            'arquivo': rd.arquivo,
            'status': rd.status
        })
        return jsonify({'id': rd.id, 'status': 'Aprovado'}), 200

    except Exception as e:
        print(f"Erro ao aprovar RD: {e}")
        return jsonify({'error': 'Erro interno do servidor.'}), 500

# Rota para excluir uma RD
@app.route('/excluir_rd/<int:rd_id>', methods=['DELETE'])
def excluir_rd(rd_id):
    try:
        rd = RD.query.get(rd_id)
        if not rd:
            return jsonify({'error': 'RD não encontrada.'}), 404

        db.session.delete(rd)
        db.session.commit()
        print(f"RD ID {rd.id} excluída com sucesso")  # Debug

        # Notifica todos os clientes que a RD foi excluída
        socketio.emit('rd_excluida', {'id': rd.id})
        return jsonify({'id': rd.id, 'status': 'Excluída'}), 200

    except Exception as e:
        print(f"Erro ao excluir RD: {e}")
        return jsonify({'error': 'Erro interno do servidor.'}), 500

# Rota para servir os arquivos enviados
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        print(f"Erro ao servir arquivo {filename}: {e}")
        return jsonify({'error': 'Arquivo não encontrado.'}), 404

if __name__ == '__main__':
    try:
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])
            print(f"Pasta de uploads criada em: {app.config['UPLOAD_FOLDER']}")  # Debug
        with app.app_context():
            db.create_all()
            print("Banco de dados criado ou atualizado.")  # Debug
        socketio.run(app, host="192.168.100.65", port=5000, debug=True)
    except Exception as e:
        print(f"Erro ao iniciar o servidor: {e}")


