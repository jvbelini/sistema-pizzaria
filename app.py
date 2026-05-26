import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import PyPDF2
import re
import difflib
import json

st.set_page_config(page_title="Compras Pizzaria - Franquia", layout="wide")

# --- MENU LATERAL (ESCOLHA DA UNIDADE) ---
st.sidebar.title("📍 Escolha a Unidade")
unidade_selecionada = st.sidebar.radio(
    "Qual loja você vai cotar agora?", 
    ["Maringá", "Bauru"]
)

# Define o nome exato da folha de cálculo baseado na escolha do menu
if unidade_selecionada == "Maringá":
    NOME_PLANILHA = 'MARINGA ESTOQUE ' 
else:
    NOME_PLANILHA = 'BAURU ESTOQUE' # Se houver espaço no final do nome, adicione aqui

st.title(f"🍕 Sistema Inteligente de Cotações - {unidade_selecionada}")

# --- BANCO DE MEMÓRIA DO SISTEMA ---
if 'cotacoes_fornecedores' not in st.session_state:
    st.session_state['cotacoes_fornecedores'] = {} 

# 1. Ligar ao Google Drive (Versão Nuvem e Local)
@st.cache_resource
def conectar_google_sheets():
    escopo = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    try:
        # Tenta usar o ficheiro local (quando roda no seu PC)
        credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopo)
    except Exception:
        # Se não encontrar o ficheiro (quando estiver no servidor online), puxa do cofre
        cred_dict = json.loads(st.secrets["google_credentials"])
        credenciais = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, escopo)
        
    return gspread.authorize(credenciais)

# 2. Função Inteligente de Caçar Preços
def extrair_precos_do_texto(texto, lista_produtos):
    texto = texto.lower()
    precos_encontrados = {}
    padrao_preco = r'(?:r\$)?\s*(\d+[.,]\d{2})'
    linhas = texto.split('\n')
    
    for linha in linhas:
        match = re.search(padrao_preco, linha)
        if match:
            preco = float(match.group(1).replace(',', '.'))
            texto_item_fornecedor = re.sub(padrao_preco, '', linha).strip()
            
            melhor_produto_planilha = None
            maior_nota_similaridade = 0
            
            for produto_planilha in lista_produtos:
                prod_limpo = str(produto_planilha).lower().strip()
                nota = difflib.SequenceMatcher(None, texto_item_fornecedor, prod_limpo).ratio()
                
                if prod_limpo:
                    palavra_chave = prod_limpo.split()[0]
                    if palavra_chave in texto_item_fornecedor:
                        nota += 0.2 
                
                if nota > 0.4 and nota > maior_nota_similaridade:
                    maior_nota_similaridade = nota
                    melhor_produto_planilha = produto_planilha
            
            if melhor_produto_planilha:
                if melhor_produto_planilha in precos_encontrados:
                    if preco < precos_encontrados[melhor_produto_planilha]:
                        precos_encontrados[melhor_produto_planilha] = preco
                else:
                    precos_encontrados[melhor_produto_planilha] = preco
                
    return precos_encontrados

try:
    cliente = conectar_google_sheets()
    
    # Ele abre a planilha dependendo do que estiver selecionado no menu lateral
    planilha = cliente.open(NOME_PLANILHA).worksheet('COZINHA')
    dados = planilha.get_all_values()
    
    if dados:
        itens_comprar = []
        for linha in dados[1:]:
            if len(linha) >= 4 and linha[0].strip() != '':
                try:
                    pedir_esq = float(linha[3].replace(',', '.'))
                    if pedir_esq > 0: itens_comprar.append(linha[0])
                except ValueError: pass
            if len(linha) >= 10 and linha[6].strip() != '':
                try:
                    pedir_dir = float(linha[9].replace(',', '.'))
                    if pedir_dir > 0: itens_comprar.append(linha[6])
                except ValueError: pass

        lista_necessidades = list(set(itens_comprar)) 

        st.subheader(f"📥 1. Inserir Cotações (Aplicado para: {unidade_selecionada})")
        st.write("Insira o nome do fornecedor, cole os preços dele e clique em guardar.")
        
        with st.container(border=True):
            nome_fornecedor = st.text_input("Qual o nome deste fornecedor? (Ex: Difal, Riber)")
            
            col1, col2 = st.columns(2)
            with col1:
                texto_colado = st.text_area("Cole a mensagem do WhatsApp aqui:")
            with col2:
                arquivo_pdf = st.file_uploader("Ou envie a tabela em PDF:", type=['pdf'])
            
            if st.button("➕ Guardar Preços deste Fornecedor"):
                if nome_fornecedor:
                    texto_extraido = (texto_colado + " ") if texto_colado else ""
                    if arquivo_pdf:
                        leitor_pdf = PyPDF2.PdfReader(arquivo_pdf)
                        for pagina in leitor_pdf.pages:
                            texto_extraido += pagina.extract_text() + " "
                    
                    if texto_extraido.strip():
                        precos_achados = extrair_precos_do_texto(texto_extraido, lista_necessidades)
                        st.session_state['cotacoes_fornecedores'][nome_fornecedor] = precos_achados
                        st.success(f"✅ Preços da {nome_fornecedor} guardados! (Encontrados {len(precos_achados)} produtos).")
                    else:
                        st.error("Cole um texto ou envie um PDF primeiro!")
                else:
                    st.error("Por favor, digite o nome do fornecedor antes de guardar.")

        if st.session_state['cotacoes_fornecedores']:
            st.write("📊 **Fornecedores já registados nesta sessão:**")
            for forn, precos in st.session_state['cotacoes_fornecedores'].items():
                st.caption(f"✔️ {forn} ({len(precos)} preços extraídos)")
            
            if st.button("Limpar todas as cotações guardadas"):
                st.session_state['cotacoes_fornecedores'] = {}
                st.rerun()

        st.divider()

        st.subheader("🪄 2. Calcular Melhor Preço e Gerar Listas")
        
        if st.button("🏆 Calcular Listas de Compras Automáticas", type="primary", use_container_width=True):
            
            resultados = []
            df_quantidades = []
            for linha in dados[1:]:
                if len(linha) >= 4 and linha[0].strip() != '':
                    try:
                        ped = float(linha[3].replace(',', '.'))
                        if ped > 0: df_quantidades.append({'PRODUTO': linha[0], 'QTD': ped})
                    except: pass
                if len(linha) >= 10 and linha[6].strip() != '':
                    try:
                        ped = float(linha[9].replace(',', '.'))
                        if ped > 0: df_quantidades.append({'PRODUTO': linha[6], 'QTD': ped})
                    except: pass
            
            for item in df_quantidades:
                produto = item['PRODUTO']
                qtd = item['QTD']
                
                melhor_preco = float('inf')
                melhor_fornecedor = "Sem Cotação (Verificar)"
                
                for forn, precos in st.session_state['cotacoes_fornecedores'].items():
                    if produto in precos:
                        if precos[produto] < melhor_preco:
                            melhor_preco = precos[produto]
                            melhor_fornecedor = forn
                
                if melhor_preco == float('inf'):
                    melhor_preco = 0.00
                
                resultados.append({
                    'FORNECEDOR': melhor_fornecedor,
                    'PRODUTO': produto,
                    'QUANTIDADE': qtd,
                    'PREÇO UNIT (R$)': melhor_preco,
                    'TOTAL (R$)': qtd * melhor_preco
                })
            
            df_final = pd.DataFrame(resultados)
            fornecedores_vencedores = df_final['FORNECEDOR'].unique()
            
            st.success(f"Cálculo concluído! As listas abaixo são referentes à unidade de {unidade_selecionada}.")
            
            cols = st.columns(3) 
            for i, forn in enumerate(fornecedores_vencedores):
                with cols[i % 3]: 
                    df_forn = df_final[df_final['FORNECEDOR'] == forn]
                    total_forn = df_forn['TOTAL (R$)'].sum()
                    st.markdown(f"### 📦 {forn}")
                    st.dataframe(df_forn[['PRODUTO', 'QUANTIDADE', 'PREÇO UNIT (R$)']], hide_index=True)
                    if forn != "Sem Cotação (Verificar)":
                        st.info(f"**Total a pagar: R$ {total_forn:.2f}**")
                    else:
                        st.warning("Estes itens não foram encontrados nas cotações enviadas.")

except Exception as e:
    st.error(f"Erro no sistema: Verifique se o robô tem acesso à planilha ou se o nome está correto. Detalhe do erro: {e}")
