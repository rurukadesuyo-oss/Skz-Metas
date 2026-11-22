import base64
import json
import re
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="SKZ Stream Tracker", page_icon="🧭", layout="wide")

KWORB_URL = "https://kworb.net/spotify/artist/2dIgFjalVxs4ThymZ67YCE_songs.html"
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")
GITHUB_REPO = st.secrets.get("GITHUB_REPO", "")
ARQUIVO_METAS = "metas.json"


@st.cache_data(ttl=3600)
def buscar_dados_kworb():
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(KWORB_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Procura especificamente a tabela que contém "Song Title"
    tabela_musicas = None
    for t in soup.find_all("table"):
        if "Song Title" in t.get_text():
            tabela_musicas = t
            break

    musicas = []
    if not tabela_musicas:
        return pd.DataFrame()

    for linha in tabela_musicas.find_all("tr")[1:]:
        colunas = linha.find_all("td")
        if len(colunas) >= 2:
            # Pega o texto do link <a> da música (evita pegar o número do ranking)
            link = colunas[0].find("a")
            titulo = link.get_text(strip=True) if link else colunas[0].get_text(strip=True)
            streams_str = re.sub(r"[^\d]", "", colunas[1].get_text())
            daily_str = re.sub(r"[^\d]", "", colunas[2].get_text()) if len(colunas) > 2 else "0"
            if titulo and streams_str:
                musicas.append({
                    "Música": titulo,
                    "Streams Totais": int(streams_str),
                    "Streams Diários": int(daily_str) if daily_str else 0,
                })
    return pd.DataFrame(musicas)


def carregar_metas_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ARQUIVO_METAS}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        dados = r.json()
        conteudo = base64.b64decode(dados["content"]).decode("utf-8")
        return json.loads(conteudo), dados["sha"]
    return [], None


def salvar_metas_github(lista_metas, sha):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ARQUIVO_METAS}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    texto_json = json.dumps(lista_metas, indent=2, ensure_ascii=False)
    conteudo_b64 = base64.b64encode(texto_json.encode("utf-8")).decode("utf-8")
    payload = {
        "message": "Atualiza metas pelo aplicativo",
        "content": conteudo_b64,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload, timeout=15)
    r.raise_for_status()


def enviar_telegram(token: str, chat_id: str, mensagem: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()


st.title("🧭 Monitor de Metas de Streams — Stray Kids")

if not GITHUB_TOKEN or not GITHUB_REPO:
    st.error("Configure GITHUB_TOKEN e GITHUB_REPO nos Secrets do Streamlit para salvar metas pelo app.")
    st.stop()

with st.sidebar:
    st.header("📲 Teste manual do Telegram")
    tg_token = st.text_input("Bot Token", type="password")
    tg_chat_id = st.text_input("Chat ID")
    st.divider()
    if st.button("🔄 Atualizar números agora"):
        st.cache_data.clear()
        st.rerun()

df = buscar_dados_kworb()
if df.empty:
    st.error("Não foi possível encontrar a tabela de músicas no Kworb.")
    st.stop()

metas, sha_atual = carregar_metas_github()

st.subheader("➕ Adicionar ou atualizar meta")
col1, col2, col3 = st.columns([3, 2, 1])
with col1:
    musica_escolhida = st.selectbox("Escolha a música", df["Música"].tolist())
with col2:
    nova_meta = st.number_input("Meta de streams", min_value=1_000_000, value=500_000_000, step=10_000_000)
with col3:
    st.write("")
    st.write("")
    if st.button("Salvar no App", use_container_width=True):
        nova_lista = [m for m in metas if m["musica"] != musica_escolhida]
        nova_lista.append({"musica": musica_escolhida, "meta": int(nova_meta)})
        salvar_metas_github(nova_lista, sha_atual)
        st.success("Salvo! O robô diário já vai usar essa meta.")
        st.rerun()

st.divider()

st.subheader("📊 Metas Ativas")
alertas_prontos = []

for item in metas:
    nome = item["musica"]
    meta = int(item["meta"])
    linha = df[df["Música"] == nome]
    if linha.empty:
        continue

    atuais = int(linha.iloc[0]["Streams Totais"])
    diarios = int(linha.iloc[0]["Streams Diários"])
    progresso = min(atuais / meta, 1.0)
    faltam = max(meta - atuais, 0)
    dias_estimados = int(faltam / diarios) if diarios > 0 and faltam > 0 else 0

    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 1.5, 0.8])
        c1.markdown(f"### {nome}")
        c1.progress(progresso, text=f"{progresso*100:.1f}% concluído")
        c2.metric("Streams Atuais", f"{atuais:,}".replace(",", "."))
        c3.metric("Meta", f"{meta:,}".replace(",", "."))

        if atuais >= meta:
            c4.success("META BATIDA!")
            alertas_prontos.append(
                f"• <b>{nome}</b> bateu <code>{meta:,}</code> streams! (Atual: <code>{atuais:,}</code>)".replace(",", ".")
            )
        else:
            c4.info(f"~{dias_estimados} dias" if dias_estimados else "Em andamento")

        if c5.button("🗑️", key=f"del_{nome}", help="Remover meta"):
            lista_filtrada = [m for m in metas if m["musica"] != nome]
            salvar_metas_github(lista_filtrada, sha_atual)
            st.rerun()

if alertas_prontos and tg_token and tg_chat_id:
    if st.button("📨 Enviar alerta agora no Telegram"):
        msg = "🎉 <b>Metas de Streams Atingidas — Stray Kids!</b>\n\n" + "\n".join(alertas_prontos)
        enviar_telegram(tg_token, tg_chat_id, msg)
        st.toast("Alerta enviado!", icon="✅")
