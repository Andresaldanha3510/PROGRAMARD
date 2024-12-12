import customtkinter as ctk
import sqlite3
from tkinter import messagebox

# Configuração do CustomTkinter
ctk.set_appearance_mode("System")  # Modos: "System" (padrão), "Dark", "Light"
ctk.set_default_color_theme("blue")  # Temas: "blue" (padrão), "green", "dark-blue"

# Conexão com o banco de dados
conn = sqlite3.connect('creditos.db')
cursor = conn.cursor()

# Criação da tabela de solicitações (se não existir)
cursor.execute('''
CREATE TABLE IF NOT EXISTS solicitacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filial TEXT,
    funcionario TEXT,
    valor REAL,
    data_solicitacao TEXT,
    prestado_contas INTEGER DEFAULT 0
)
''')
conn.commit()

# Função para adicionar uma nova solicitação
def adicionar_solicitacao():
    filial = entry_filial.get()
    funcionario = entry_funcionario.get()
    valor = entry_valor.get()
    data_solicitacao = entry_data.get()

    if not (filial and funcionario and valor and data_solicitacao):
        messagebox.showwarning("Atenção", "Todos os campos devem ser preenchidos.")
        return

    try:
        valor = float(valor)
    except ValueError:
        messagebox.showerror("Erro", "O valor do crédito deve ser numérico.")
        return

    cursor.execute('''
    INSERT INTO solicitacoes (filial, funcionario, valor, data_solicitacao)
    VALUES (?, ?, ?, ?)
    ''', (filial, funcionario, valor, data_solicitacao))
    conn.commit()
    messagebox.showinfo("Sucesso", "Solicitação adicionada com sucesso!")
    entry_filial.delete(0, ctk.END)
    entry_funcionario.delete(0, ctk.END)
    entry_valor.delete(0, ctk.END)
    entry_data.delete(0, ctk.END)

# Criação da janela principal
janela = ctk.CTk()
janela.title("Gerenciamento de Créditos")

# Configuração do layout
frame = ctk.CTkFrame(janela)
frame.pack(pady=20, padx=60, fill="both", expand=True)

label_titulo = ctk.CTkLabel(frame, text="Cadastro de Solicitação de Crédito", font=ctk.CTkFont(size=20, weight="bold"))
label_titulo.pack(pady=12, padx=10)

# Campos de entrada
entry_filial = ctk.CTkEntry(frame, placeholder_text="Filial")
entry_filial.pack(pady=12, padx=10)

entry_funcionario = ctk.CTkEntry(frame, placeholder_text="Funcionário")
entry_funcionario.pack(pady=12, padx=10)

entry_valor = ctk.CTkEntry(frame, placeholder_text="Valor do Crédito")
entry_valor.pack(pady=12, padx=10)

entry_data = ctk.CTkEntry(frame, placeholder_text="Data da Solicitação (AAAA-MM-DD)")
entry_data.pack(pady=12, padx=10)

# Botão para adicionar solicitação
btn_adicionar = ctk.CTkButton(frame, text="Adicionar Solicitação", command=adicionar_solicitacao)
btn_adicionar.pack(pady=12, padx=10)

# Iniciar o loop da interface
janela.mainloop()

# Fechar a conexão com o banco de dados ao encerrar o programa
conn.close()


