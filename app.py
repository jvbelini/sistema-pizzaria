import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import PyPDF2
import json
import pytesseract
from PIL import Image
from datetime import datetime
import google.generativeai as genai

st.set_page_config(page_title="Compras Pizzaria - Franquia", layout="wide")

# Configurar a Inteligência Artificial do Google Gemini (Puxando do Cofre)
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

st.sidebar.title("📍 Escolha a Unidade")
unidade_selecionada = st.sidebar.radio("Qual loja você vai gerenciar agora?", ["Maringá", "Bauru"])

st.sidebar.divider()
st.sidebar.title("📱 Módulo")
modulo = st.sidebar.radio("O que você deseja fazer?", ["📦 Contagem de Estoque", "🛒 Cotações com IA"])

NOME_PLANILHA = 'MARINGA ESTOQUE ' if unidade_selecionada == "Maringá" else 'BAURU ESTOQUE'
nome_aba_cozinha = 'COZINHA' if unidade_selecionada == "Maringá" else 'COZINHA'

if 'cotacoes_fornecedores' not in st.session_state:
    st.session_state['cotacoes_fornecedores'] = {} 
if 'necessidades_atuais' not in st.session_state:
    st.session_state['necessidades_atuais'] = {}

# --- ESCONDER MENU DO STREAMLIT (TELA LIMPA DE APP) ---
esconder_menu = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
    """
st.markdown(esconder_menu, unsafe_allow_html=True)

@st.cache_resource
def conectar_google_sheets():
    escopo = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    try:
        credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopo)
    except Exception:
        cred_dict = json.loads(st.secrets["google_credentials"])
        credenciais = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, escopo)
    return gspread.authorize(credenciais)

# --- IA PARA LER A LISTA DA COZINHA ---
def processar_lista_cozinha(texto, lista_produtos):
    if not texto.strip(): return {}
    comando = f"""
    Abaixo está uma mensagem de WhatsApp da cozinha de um restaurante listando ingredientes que precisam ser comprados, e a lista OFICIAL de produtos do estoque.
    
    Identifique quais produtos da lista oficial a cozinha está pedindo e extraia a quantidade solicitada em formato numérico. 
    Seja inteligente: "Mussa" é "Mussarela", "F. Trigo" é "Farinha de Trigo", etc. Ignore itens que não pareçam estar na lista oficial.
    
    Mensagem da Cozinha:
    {texto}
    
    Lista Oficial de Produtos:
    {lista_produtos}
    
    Retorne APENAS um objeto JSON válido, onde a chave é o NOME EXATO do produto da lista oficial, e o valor é a quantidade (float).
    Exemplo: {{"Farinha de Trigo 5kg": 2.0, "Mussarela": 10.5}}
    Se não encontrar nada, retorne {{}}
    """
    try:
        modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        modelo_escolhido = next((m for m in modelos if 'flash' in m.lower()), modelos[0])
        modelo = genai.GenerativeModel(modelo_escolhido)
        resposta = modelo.generate_content(comando)
        return json.loads(resposta.text.replace('```json', '').replace('```', '').strip())
    except Exception as e:
        st.error(f"Erro na IA ao ler lista da cozinha: {e}")
        return {}

# --- IA PARA LER PREÇOS DOS FORNECEDORES ---
def extrair_precos_com_ia(texto, lista_produtos):
    if not texto.strip(): return {}
    comando = f"""
    Abaixo, vou te passar uma mensagem/tabela de um fornecedor e a minha lista de necessidades.
    Identifique quais dos meus produtos estão sendo oferecidos e o preço unitário deles.
    
    Texto do Fornecedor:
    {texto}
    
    Minha Lista de Produtos:
    {lista_produtos}
    
    Retorne APENAS um objeto JSON válido. Chave = NOME EXATO da minha lista, Valor = PREÇO NUMÉRICO (float). 
    Exemplo: {{"Farinha de Trigo 5kg": 25.90, "Mussarela": 35.50}}
    """
    try:
        modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        modelo_escolhido = next((m for m in modelos if 'flash' in m.lower()), modelos[0])
        modelo = genai.GenerativeModel(modelo_escolhido)
        resposta = modelo.generate_content(comando)
        return json.loads(resposta.text.replace('```json', '').replace('```', '').strip())
    except Exception as e:
        st.error(f"Erro na IA ao ler preços: {e}")
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
    
    # Criar lista oficial com todos os nomes dos produtos da planilha
    lista_todos_produtos = []
    if dados:
        for linha in dados[1:]:
            if len(linha) >= 1 and str(linha[0]).strip() != '' and str(linha[0]).strip().upper() != 'PRODUTOS':
                lista_todos_produtos.append(str(linha[0]).strip())
            if len(linha) >= 7 and str(linha[6]).strip() != '' and str(linha[6]).strip().upper() != 'PRODUTOS':
                lista_todos_produtos.append(str(linha[6]).strip())
    
    # ==========================================
    # MÓDULO 1: CONTAGEM DE ESTOQUE (ESTOQUISTA)
    # ==========================================
    if modulo == "📦 Contagem de Estoque":
        st.title(f"📦 Lançamento de Estoque - {unidade_selecionada}")
        st.info("Digite a quantidade real que está na prateleira hoje. Depois clique em Salvar lá embaixo.")
        
        itens_estoque = []
        for i, linha in enumerate(dados):
            if len(linha) >= 2:
                prod_esq = str(linha[0]).strip()
                if prod_esq != '' and prod_esq.upper() != 'PRODUTOS':
                    itens_estoque.append({
                        'Produto': prod_esq,
                        'Quantidade Atual': str(linha[1]).strip() if str(linha[1]).strip() != '' else "0",
                        '_row': i + 1,
                        '_col': 2 
                    })
            
            if len(linha) >= 8:
                prod_dir = str(linha[6]).strip()
                if prod_dir != '' and prod_dir.upper() != 'PRODUTOS':
                    itens_estoque.append({
                        'Produto': prod_dir,
                        'Quantidade Atual': str(linha[7]).strip() if str(linha[7]).strip() != '' else "0",
                        '_row': i + 1,
                        '_col': 8 
                    })
                    
        df_estoque = pd.DataFrame(itens_estoque)
        
        df_display = df_estoque[['Produto', 'Quantidade Atual']].copy()
        df_editado = st.data_editor(df_display, use_container_width=True, hide_index=True, disabled=["Produto"])
        
        st.divider()
        if st.button("💾 Salvar Estoque no Drive", type="primary", use_container_width=True):
            with st.spinner("Enviando números para o Google Drive..."):
                cells_to_update = []
                for idx, row in df_editado.iterrows():
                    nova_qtd = str(row['Quantidade Atual'])
                    qtd_antiga = str(df_estoque.at[idx, 'Quantidade Atual'])
                    
                    if nova_qtd != qtd_antiga: 
                        linha_planilha = int(df_estoque.at[idx, '_row'])
                        col_planilha = int(df_estoque.at[idx, '_col'])
                        cells_to_update.append(gspread.Cell(row=linha_planilha, col=col_planilha, value=nova_qtd))
                
                if cells_to_update:
                    aba_cozinha.update_cells(cells_to_update)
                    st.success(f"✅ Fantástico! {len(cells_to_update)} itens foram atualizados no estoque da loja!")
                else:
                    st.warning("Nenhuma quantidade foi alterada.")

    # ==========================================
    # MÓDULO 2: COTAÇÕES E COMPRAS (COMPRADOR)
    # ==========================================
    elif modulo == "🛒 Cotações com IA":
        st.title(f"🍕 Cotações com IA - {unidade_selecionada}")
        
        # --- PASSO 1: O QUE COMPRAR ---
        st.subheader("📝 1. O que vamos comprar hoje?")
        origem_pedido = st.radio("Escolha como gerar a lista de necessidades:", 
                                 ["Usar a lista de Prioridades da Cozinha (Manual)", "Gerar Automático pela Planilha"])
        
        if origem_pedido == "Gerar Automático pela Planilha":
            necessidades_temp = {}
            for linha in dados[1:]:
                if len(linha) >= 4 and str(linha[0]).strip() != '':
                    try:
                        ped = float(linha[3].replace(',', '.'))
                        if ped > 0: necessidades_temp[linha[0].strip()] = ped
                    except: pass
                if len(linha) >= 10 and str(linha[6]).strip() != '':
                    try:
                        ped = float(linha[9].replace(',', '.'))
                        if ped > 0: necessidades_temp[linha[6].strip()] = ped
                    except: pass
            
            st.session_state['necessidades_atuais'] = necessidades_temp
            st.success(f"✅ {len(necessidades_temp)} itens puxados do pedido automático da planilha.")
            st.write(st.session_state['necessidades_atuais'])

        else:
            texto_cozinha = st.text_area("Cole aqui a mensagem de prioridades do WhatsApp da cozinha:")
            if st.button("Analisar Lista da Cozinha"):
                with st.spinner("Decifrando os pedidos da cozinha..."):
                    necessidades_temp = processar_lista_cozinha(texto_cozinha, lista_todos_produtos)
                    if necessidades_temp:
                        st.session_state['necessidades_atuais'] = necessidades_temp
                        st.success(f"✅ A IA identificou {len(necessidades_temp)} produtos no texto da cozinha!")
                    else:
                        st.error("Não consegui encontrar nenhum produto válido. Tente melhorar o texto.")
            
            if st.session_state['necessidades_atuais'] and origem_pedido == "Usar a lista de Prioridades da Cozinha (Manual)":
                st.write("**Resumo do que será orçado:**")
                st.write(st.session_state['necessidades_atuais'])

        st.divider()

        # --- PASSO 2: COTAÇÕES ---
        st.subheader("📥 2. Inserir Cotações dos Fornecedores")
        
        lista_necessidades = list(st.session_state['necessidades_atuais'].keys())
        
        if not lista_necessidades:
            st.warning("⚠️ Gere a lista de produtos no Passo 1 primeiro!")
        else:
            with st.container(border=True):
                nome_fornecedor = st.text_input("Nome do fornecedor? (Ex: Difal, Riber)")
                col1, col2, col3 = st.columns(3)
                with col1: texto_colado = st.text_area("Texto do WhatsApp:")
                with col2: arquivo_pdf = st.file_uploader("Tabela em PDF:", type=['pdf'])
                with col3: arquivo_img = st.file_uploader("Foto/Imagem:", type=['png', 'jpg', 'jpeg'])
                
                if st.button("➕ Analisar Preços do Fornecedor"):
                    if nome_fornecedor:
                        with st.spinner(f"Lendo preços da {nome_fornecedor}..."):
                            texto_extraido = (texto_colado + " \n") if texto_colado else ""
                            if arquivo_pdf:
                                leitor_pdf = PyPDF2.PdfReader(arquivo_pdf)
                                for pagina in leitor_pdf.pages: texto_extraido += pagina.extract_text() + " \n"
                            if arquivo_img:
                                imagem = Image.open(arquivo_img)
                                texto_extraido += pytesseract.image_to_string(imagem, lang='por') + " \n"
                            
                            if texto_extraido.strip():
                                precos_achados = extrair_precos_com_ia(texto_extraido, lista_necessidades)
                                if precos_achados:
                                    st.session_state['cotacoes_fornecedores'][nome_fornecedor] = precos_achados
                                    st.success(f"✅ Preços guardados! Encontrados {len(precos_achados)} produtos.")
                                else:
                                    st.warning("A IA não achou o preço de NENHUM produto da sua lista de necessidades neste texto.")
                            else:
                                st.error("Insira texto, PDF ou Imagem!")
                    else:
                        st.error("Digite o nome do fornecedor!")

            if st.session_state['cotacoes_fornecedores']:
                for forn, precos in st.session_state['cotacoes_fornecedores'].items():
                    st.caption(f"✔️ {forn} ({len(precos)} preços extraídos)")
                if st.button("Limpar Cotações"):
                    st.session_state['cotacoes_fornecedores'] = {}
                    if 'df_resultados' in st.session_state: del st.session_state['df_resultados']
                    st.rerun()

        st.divider()

        # --- PASSO 3: DIVIDIR E FINALIZAR ---
        st.subheader("🪄 3. Revisar, Dividir e Pedir")
        
        if st.button("🏆 Calcular Melhores Opções", type="primary", use_container_width=True):
            resultados = []
            for produto, qtd in st.session_state['necessidades_atuais'].items():
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
            st.session_state['df_resultados'] = pd.DataFrame(resultados)
            st.session_state['mostrar_zap'] = False

        if 'df_resultados' in st.session_state and not st.session_state['df_resultados'].empty:
            st.info("💡 **Quer dividir o pedido em 2 fornecedores?** \nEdite a tabela abaixo! Ex: Diminua a quantidade da linha da Mussarela na Riber de 10 para 5. Depois vá na última linha em branco, adicione o fornecedor Difal, escreva Mussarela e coloque a quantidade 5.")
            
            df_editado = st.data_editor(
                st.session_state['df_resultados'],
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True
            )
            
            df_editado['QUANTIDADE'] = pd.to_numeric(df_editado['QUANTIDADE'], errors='coerce').fillna(0)
            df_editado['PREÇO UNIT (R$)'] = pd.to_numeric(df_editado['PREÇO UNIT (R$)'], errors='coerce').fillna(0)
            df_editado['TOTAL (R$)'] = df_editado['QUANTIDADE'] * df_editado['PREÇO UNIT (R$)']
            
            fornecedores_vencedores = df_editado['FORNECEDOR'].unique()
            
            st.divider()
            st.subheader("📦 Resumo Final por Fornecedor")
            
            cols = st.columns(3) 
            for i, forn in enumerate(fornecedores_vencedores):
                with cols[i % 3]: 
                    df_forn = df_editado[df_editado['FORNECEDOR'] == forn]
                    total_forn = df_forn['TOTAL (R$)'].sum()
                    st.markdown(f"**{forn}**")
                    st.dataframe(df_forn[['PRODUTO', 'QUANTIDADE', 'TOTAL (R$)']], hide_index=True)
                    if forn != "Sem Cotação":
                        st.success(f"**Total: R$ {total_forn:.2f}**")
                        
            st.divider()
            
            col_btn1, col_btn2 = st.columns(2)
            
            with col_btn1:
                if st.button("💾 Guardar Histórico Drive", type="secondary", use_container_width=True):
                    data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
                    dados_historico = []
                    for _, row in df_editado.iterrows():
                        if row['FORNECEDOR'] != "Sem Cotação":
                            dados_historico.append([
                                data_hoje, unidade_selecionada, row['FORNECEDOR'], row['PRODUTO'], row['PREÇO UNIT (R$)']
                            ])
                    if dados_historico:
                        aba_historico.append_rows(dados_historico)
                        st.success("✅ Histórico guardado!")
                    else: st.warning("Nada calculado para salvar.")

            with col_btn2:
                if st.button("📱 Gerar Texto WhatsApp", type="secondary", use_container_width=True):
                    st.session_state['mostrar_zap'] = True
            
            if st.session_state.get('mostrar_zap', False):
                texto_zap = f"🛒 *RESUMO DE COMPRAS - {unidade_selecionada.upper()}*\n📅 Data: {datetime.now().strftime('%d/%m/%Y')}\n\n"
                
                for forn in fornecedores_vencedores:
                    if forn == "Sem Cotação": continue
                    df_forn = df_editado[df_editado['FORNECEDOR'] == forn]
                    texto_zap += f"📦 *Para pedir na {forn}:*\n"
                    for _, row in df_forn.iterrows():
                        texto_zap += f"- {row['QUANTIDADE']}x {row['PRODUTO']} (R$ {row['PREÇO UNIT (R$)']:.2f} un)\n"
                    texto_zap += f"💰 *Total estimado: R$ {df_forn['TOTAL (R$)'].sum():.2f}*\n\n"
                
                df_sem_cotacao = df_editado[df_editado['FORNECEDOR'] == "Sem Cotação"]
                if not df_sem_cotacao.empty:
                    texto_zap += "⚠️ *ITENS PARA COMPRAR POR FORA:*\n"
                    for _, row in df_sem_cotacao.iterrows():
                        texto_zap += f"- {row['QUANTIDADE']}x {row['PRODUTO']}\n"
                
                st.text_area("Copie o texto para mandar aos fornecedores/gerente:", value=texto_zap, height=350)

except Exception as e:
    st.error(f"Erro no sistema: {e}")
