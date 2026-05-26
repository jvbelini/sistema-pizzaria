import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import PyPDF2
import json
import pytesseract
from PIL import Image
from datetime import datetime
import google.generativeai as genai # <-- Nova biblioteca da IA!

st.set_page_config(page_title="Compras Pizzaria - Franquia", layout="wide")

# Configurar a Inteligência Artificial do Google Gemini
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

st.sidebar.title("📍 Escolha a Unidade")
unidade_selecionada = st.sidebar.radio("Qual loja você vai cotar agora?", ["Maringá", "Bauru"])

NOME_PLANILHA = 'MARINGA ESTOQUE ' if unidade_selecionada == "Maringá" else 'BAURU ESTOQUE'
nome_aba_cozinha = 'COZINHA' if unidade_selecionada == "Maringá" else 'COZINHA '

st.title(f"🍕 Cotações com IA - {unidade_selecionada}")

if 'cotacoes_fornecedores' not in st.session_state:
    st.session_state['cotacoes_fornecedores'] = {} 
if 'resultados_calculados' not in st.session_state:
    st.session_state['resultados_calculados'] = []

@st.cache_resource
def conectar_google_sheets():
    escopo = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    try:
        credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopo)
    except Exception:
        cred_dict = json.loads(st.secrets["google_credentials"])
        credenciais = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, escopo)
    return gspread.authorize(credenciais)

# --- A MÁGICA ACONTECE AQUI ---
def extrair_precos_com_ia(texto, lista_produtos):
    if not texto.strip(): return {}
    
    # O comando (prompt) que o seu sistema dá para o meu "cérebro"
    comando = f"""
    Você é um assistente de compras especialista em restaurantes.
    Abaixo, vou te passar uma mensagem (de WhatsApp/PDF) de um fornecedor e a minha lista EXATA de produtos do estoque.
    
    Sua tarefa é ler a mensagem bagunçada e identificar quais dos meus produtos estão sendo oferecidos e qual o preço deles. 
    Seja inteligente: "Mussa" é "Mussarela", "F. Trigo" é "Farinha de Trigo", etc. Ignore emojis e erros de digitação.
    
    Texto do Fornecedor:
    {texto}
    
    Minha Lista de Produtos:
    {lista_produtos}
    
    Retorne APENAS um objeto JSON válido. A chave deve ser o nome EXATO do produto da minha lista, e o valor deve ser o preço em formato numérico (float). Não escreva NENHUM texto antes ou depois do JSON.
    Exemplo: {{"Farinha de Trigo 5kg": 25.90, "Mussarela": 35.50}}
    Se não encontrar nenhum preço claro, retorne {{}}
    """
    
    try:
        # Chama a IA super rápida do Gemini
        modelo = genai.GenerativeModel('gemini-1.5-flash')
        resposta = modelo.generate_content(comando)
        
        # Limpa o texto caso a IA mande com a formatação do JSON
        texto_limpo = resposta.text.replace('```json', '').replace('```', '').strip()
        return json.loads(texto_limpo)
    except Exception as e:
        st.error(f"Erro na interpretação da IA: {e}")
        return {}

try:
    cliente = conectar_google_sheets()
    planilha = cliente.open(NOME_PLANILHA)
    aba_cozinha = planilha.worksheet(nome_aba_cozinha)
    dados = aba_cozinha.get_all_values()
    
    try:
        aba_historico = planilha.worksheet('HISTORICO')
    except gspread.exceptions.WorksheetNotFound:
        aba_historico = planilha.add_worksheet(title="HISTORICO", rows="1000", cols="5")
        aba_historico.append_row(["DATA", "UNIDADE", "FORNECEDOR", "PRODUTO", "PREÇO UNITÁRIO"])
    
    if dados:
        itens_comprar = []
        for linha in dados[1:]:
            if len(linha) >= 4 and linha[0].strip() != '':
                try:
                    if float(linha[3].replace(',', '.')) > 0: itens_comprar.append(linha[0])
                except ValueError: pass
            if len(linha) >= 10 and linha[6].strip() != '':
                try:
                    if float(linha[9].replace(',', '.')) > 0: itens_comprar.append(linha[6])
                except ValueError: pass

        lista_necessidades = list(set(itens_comprar)) 

        st.subheader("📥 1. Inserir Cotações (Texto, PDF ou Imagem)")
        
        with st.container(border=True):
            nome_fornecedor = st.text_input("Qual o nome deste fornecedor? (Ex: Difal, Riber)")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                texto_colado = st.text_area("Texto do WhatsApp:")
            with col2:
                arquivo_pdf = st.file_uploader("Tabela em PDF:", type=['pdf'])
            with col3:
                arquivo_img = st.file_uploader("Foto ou Imagem:", type=['png', 'jpg', 'jpeg'])
            
            if st.button("➕ Analisar com Inteligência Artificial"):
                if nome_fornecedor:
                    with st.spinner(f"O Gemini está lendo e interpretando a lista da {nome_fornecedor}..."):
                        texto_extraido = (texto_colado + " \n") if texto_colado else ""
                        
                        if arquivo_pdf:
                            leitor_pdf = PyPDF2.PdfReader(arquivo_pdf)
                            for pagina in leitor_pdf.pages:
                                texto_extraido += pagina.extract_text() + " \n"
                                
                        if arquivo_img:
                            imagem = Image.open(arquivo_img)
                            texto_extraido += pytesseract.image_to_string(imagem, lang='por') + " \n"
                        
                        if texto_extraido.strip():
                            # AGORA CHAMA A FUNÇÃO DA IA
                            precos_achados = extrair_precos_com_ia(texto_extraido, lista_necessidades)
                            
                            if precos_achados:
                                st.session_state['cotacoes_fornecedores'][nome_fornecedor] = precos_achados
                                st.success(f"✅ Preços guardados! A IA identificou {len(precos_achados)} produtos com sucesso.")
                            else:
                                st.warning("A IA leu o texto, mas não conseguiu associar nenhum preço aos produtos que você precisa comprar.")
                        else:
                            st.error("Insira algum texto, PDF ou Imagem!")
                else:
                    st.error("Digite o nome do fornecedor!")

        if st.session_state['cotacoes_fornecedores']:
            for forn, precos in st.session_state['cotacoes_fornecedores'].items():
                st.caption(f"✔️ {forn} ({len(precos)} preços extraídos)")
            if st.button("Limpar Cotações"):
                st.session_state['cotacoes_fornecedores'] = {}
                st.session_state['resultados_calculados'] = []
                st.rerun()

        st.divider()

        st.subheader("🪄 2. Calcular e Guardar Histórico")
        
        if st.button("🏆 Calcular Melhores Opções", type="primary", use_container_width=True):
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
                melhor_fornecedor = "Sem Cotação"
                
                for forn, precos in st.session_state['cotacoes_fornecedores'].items():
                    if produto in precos:
                        if precos[produto] < melhor_preco:
                            melhor_preco = precos[produto]
                            melhor_fornecedor = forn
                
                if melhor_preco == float('inf'): melhor_preco = 0.00
                
                resultados.append({
                    'FORNECEDOR': melhor_fornecedor,
                    'PRODUTO': produto,
                    'QUANTIDADE': qtd,
                    'PREÇO UNIT (R$)': melhor_preco,
                    'TOTAL (R$)': qtd * melhor_preco
                })
            
            st.session_state['resultados_calculados'] = resultados

        if st.session_state['resultados_calculados']:
            df_final = pd.DataFrame(st.session_state['resultados_calculados'])
            fornecedores_vencedores = df_final['FORNECEDOR'].unique()
            
            cols = st.columns(3) 
            for i, forn in enumerate(fornecedores_vencedores):
                with cols[i % 3]: 
                    df_forn = df_final[df_final['FORNECEDOR'] == forn]
                    total_forn = df_forn['TOTAL (R$)'].sum()
                    st.markdown(f"### 📦 {forn}")
                    st.dataframe(df_forn[['PRODUTO', 'QUANTIDADE', 'PREÇO UNIT (R$)']], hide_index=True)
                    if forn != "Sem Cotação":
                        st.info(f"**Total a pagar: R$ {total_forn:.2f}**")
                        
            st.divider()
            if st.button("💾 Guardar Preços no Histórico do Drive", type="secondary", use_container_width=True):
                data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
                dados_historico = []
                
                for res in st.session_state['resultados_calculados']:
                    if res['FORNECEDOR'] != "Sem Cotação":
                        dados_historico.append([
                            data_hoje, 
                            unidade_selecionada, 
                            res['FORNECEDOR'], 
                            res['PRODUTO'], 
                            res['PREÇO UNIT (R$)']
                        ])
                
                if dados_historico:
                    aba_historico.append_rows(dados_historico)
                    st.success("✅ Histórico guardado na sua planilha do Drive com sucesso!")
                else:
                    st.warning("Não há preços calculados para salvar.")

except Exception as e:
    st.error(f"Erro: {e}")
