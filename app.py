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
# --- ESCONDER MENU E MARCA D'ÁGUA DO STREAMLIT ---
esconder_menu = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
    """
st.markdown(esconder_menu, unsafe_allow_html=True)
# Configurar a Inteligência Artificial do Google Gemini
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

@st.cache_resource
def conectar_google_sheets():
    escopo = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    try:
        credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopo)
    except Exception:
        cred_dict = json.loads(st.secrets["google_credentials"])
        credenciais = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, escopo)
    return gspread.authorize(credenciais)

def extrair_precos_com_ia(texto, lista_produtos):
    if not texto.strip(): return {}
    
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
        modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        modelo_escolhido = modelos[0] 
        for m in modelos:
            if 'flash' in m.lower():
                modelo_escolhido = m
                break
                
        modelo = genai.GenerativeModel(modelo_escolhido)
        resposta = modelo.generate_content(comando)
        
        texto_limpo = resposta.text.replace('```json', '').replace('```', '').strip()
        return json.loads(texto_limpo)
    except Exception as e:
        st.error(f"Erro na interpretação da IA: {e}")
        return {}

try:
    # LIGAÇÃO PRINCIPAL COM O GOOGLE DRIVE
    cliente = conectar_google_sheets()
    planilha = cliente.open(NOME_PLANILHA)
    aba_cozinha = planilha.worksheet(nome_aba_cozinha)
    dados = aba_cozinha.get_all_values()
    
    try:
        aba_historico = planilha.worksheet('HISTORICO')
    except gspread.exceptions.WorksheetNotFound:
        aba_historico = planilha.add_worksheet(title="HISTORICO", rows="1000", cols="5")
        aba_historico.append_row(["DATA", "UNIDADE", "FORNECEDOR", "PRODUTO", "PREÇO UNITÁRIO"])
    
    # ==========================================
    # MÓDULO 1: CONTAGEM DE ESTOQUE (ESTOQUISTA)
    # ==========================================
    if modulo == "📦 Contagem de Estoque":
        st.title(f"📦 Lançamento de Estoque - {unidade_selecionada}")
        st.info("Digite a quantidade real que está na prateleira hoje. Depois clique em Salvar lá embaixo.")
        
        itens_estoque = []
        for i, linha in enumerate(dados):
            # Analisa Lado Esquerdo (Coluna A e B)
            if len(linha) >= 2:
                prod_esq = str(linha[0]).strip()
                if prod_esq != '' and prod_esq.upper() != 'PRODUTOS':
                    itens_estoque.append({
                        'Produto': prod_esq,
                        'Quantidade Atual': str(linha[1]).strip() if str(linha[1]).strip() != '' else "0",
                        '_row': i + 1,
                        '_col': 2  # Coluna B no Drive é a 2
                    })
            
            # Analisa Lado Direito (Coluna G e H)
            if len(linha) >= 8:
                prod_dir = str(linha[6]).strip()
                if prod_dir != '' and prod_dir.upper() != 'PRODUTOS':
                    itens_estoque.append({
                        'Produto': prod_dir,
                        'Quantidade Atual': str(linha[7]).strip() if str(linha[7]).strip() != '' else "0",
                        '_row': i + 1,
                        '_col': 8  # Coluna H no Drive é a 8
                    })
                    
        df_estoque = pd.DataFrame(itens_estoque)
        
        # Mostra a tabela editável
        df_display = df_estoque[['Produto', 'Quantidade Atual']].copy()
        df_editado = st.data_editor(
            df_display,
            use_container_width=True,
            hide_index=True,
            disabled=["Produto"], # Trava o nome para não desconfigurar o Drive
            num_rows="fixed"
        )
        
        st.divider()
        if st.button("💾 Salvar Estoque no Drive", type="primary", use_container_width=True):
            with st.spinner("Enviando números para o Google Drive..."):
                cells_to_update = []
                for idx, row in df_editado.iterrows():
                    nova_qtd = str(row['Quantidade Atual'])
                    qtd_antiga = str(df_estoque.at[idx, 'Quantidade Atual'])
                    
                    # Só atualiza a célula se o gerente tiver alterado o número
                    if nova_qtd != qtd_antiga: 
                        linha_planilha = int(df_estoque.at[idx, '_row'])
                        col_planilha = int(df_estoque.at[idx, '_col'])
                        cells_to_update.append(
                            gspread.Cell(row=linha_planilha, col=col_planilha, value=nova_qtd)
                        )
                
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
                        with st.spinner(f"O Gemini está a ler e interpretar a lista da {nome_fornecedor}..."):
                            texto_extraido = (texto_colado + " \n") if texto_colado else ""
                            
                            if arquivo_pdf:
                                leitor_pdf = PyPDF2.PdfReader(arquivo_pdf)
                                for pagina in leitor_pdf.pages:
                                    texto_extraido += pagina.extract_text() + " \n"
                                    
                            if arquivo_img:
                                imagem = Image.open(arquivo_img)
                                texto_extraido += pytesseract.image_to_string(imagem, lang='por') + " \n"
                            
                            if texto_extraido.strip():
                                precos_achados = extrair_precos_com_ia(texto_extraido, lista_necessidades)
                                
                                if precos_achados:
                                    st.session_state['cotacoes_fornecedores'][nome_fornecedor] = precos_achados
                                    st.success(f"✅ Preços guardados! A IA identificou {len(precos_achados)} produtos.")
                                else:
                                    st.warning("A IA leu o texto, mas não conseguiu associar preços aos produtos da sua lista.")
                            else:
                                st.error("Insira algum texto, PDF ou Imagem!")
                    else:
                        st.error("Digite o nome do fornecedor!")

            if st.session_state['cotacoes_fornecedores']:
                for forn, precos in st.session_state['cotacoes_fornecedores'].items():
                    st.caption(f"✔️ {forn} ({len(precos)} preços extraídos)")
                if st.button("Limpar Cotações"):
                    st.session_state['cotacoes_fornecedores'] = {}
                    if 'df_resultados' in st.session_state: del st.session_state['df_resultados']
                    st.session_state['mostrar_zap'] = False
                    st.rerun()

            st.divider()

            st.subheader("🪄 2. Revisar e Dividir Pedidos")
            
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
                
                st.session_state['df_resultados'] = pd.DataFrame(resultados)
                st.session_state['mostrar_zap'] = False

            if 'df_resultados' in st.session_state and not st.session_state['df_resultados'].empty:
                st.info("💡 **Dica:** Pode editar a tabela abaixo livremente! Se quiser dividir um pedido, diminua a quantidade original e adicione uma nova linha no fim da tabela para o outro fornecedor.")
                
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
                st.subheader("📦 Resumo por Fornecedor (Atualizado)")
                
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
                    if st.button("💾 Guardar Preços no Histórico do Drive", type="secondary", use_container_width=True):
                        data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
                        dados_historico = []
                        
                        for _, row in df_editado.iterrows():
                            if row['FORNECEDOR'] != "Sem Cotação":
                                dados_historico.append([
                                    data_hoje, 
                                    unidade_selecionada, 
                                    row['FORNECEDOR'], 
                                    row['PRODUTO'], 
                                    row['PREÇO UNIT (R$)']
                                ])
                        
                        if dados_historico:
                            aba_historico.append_rows(dados_historico)
                            st.success("✅ Histórico guardado na sua planilha do Drive com sucesso!")
                        else:
                            st.warning("Não há preços calculados para salvar.")

                with col_btn2:
                    if st.button("📱 Gerar Texto para WhatsApp", type="secondary", use_container_width=True):
                        st.session_state['mostrar_zap'] = True
                
                if st.session_state.get('mostrar_zap', False):
                    texto_zap = f"🛒 *RESUMO DE COMPRAS - {unidade_selecionada.upper()}*\n"
                    texto_zap += f"📅 Data: {datetime.now().strftime('%d/%m/%Y')}\n\n"
                    
                    for forn in fornecedores_vencedores:
                        if forn == "Sem Cotação": 
                            continue
                        
                        df_forn = df_editado[df_editado['FORNECEDOR'] == forn]
                        total_forn = df_forn['TOTAL (R$)'].sum()
                        
                        texto_zap += f"📦 *Fornecedor: {forn}*\n"
                        for _, row in df_forn.iterrows():
                            texto_zap += f"- {row['QUANTIDADE']}x {row['PRODUTO']} (R$ {row['PREÇO UNIT (R$)']:.2f} unid.)\n"
                        texto_zap += f"💰 *Total {forn}: R$ {total_forn:.2f}*\n\n"
                    
                    df_sem_cotacao = df_editado[df_editado['FORNECEDOR'] == "Sem Cotação"]
                    if not df_sem_cotacao.empty:
                        texto_zap += "⚠️ *ITENS SEM COTAÇÃO (Verificar):*\n"
                        for _, row in df_sem_cotacao.iterrows():
                            texto_zap += f"- {row['QUANTIDADE']}x {row['PRODUTO']}\n"
                    
                    st.text_area("Copie o texto abaixo e mande para o gerente:", value=texto_zap, height=350)

except Exception as e:
    st.error(f"Erro: {e}")
