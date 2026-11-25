import base64
import json
import re
from datetime import datetime, timezone
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="SKZ Stream & Views Tracker", page_icon="🧭", layout="wide")

KWORB_URL = "https://kworb.net/spotify/artist/2dIgFjalVxs4ThymZ67YCE_songs.html"
YOUTUBE_HANDLES = [
    {"nome": "Stray Kids Oficial", "handle": "straykids"},
    {"nome": "Stray Kids Japan Official", "handle": "straykidsjapanofficial"},
    {"nome": "JYP Entertainment", "handle": "jypentertainment"},
]

GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")
GITHUB_REPO = st.secrets.get("GITHUB_REPO", "")
YOUTUBE_API_KEY = st.secrets.get("YOUTUBE_API_KEY", "")

ARQUIVO_METAS = "metas.json"
METAS_PLAYLIST_FIXAS = [300_000_000, 200_000_000, 100_000_000, 50_000_000]


def fmt_num(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


@st.cache_data(ttl=3600)
def buscar_dados_kworb():
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(KWORB_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

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


@st.cache_data(ttl=3600)
def buscar_dados_youtube(api_key: str):
    if not api_key:
        return []

    videos_coletados = []
    agora = datetime.now(timezone.utc)

    for canal in YOUTUBE_HANDLES:
        handle = canal["handle"]
        nome_canal = canal["nome"]

        r_ch = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "contentDetails,snippet", "forHandle": handle, "key": api_key},
            timeout=20,
        )
        if not r_ch.ok:
            continue

        items_ch = r_ch.json().get("items", [])
        if not items_ch:
            continue

        uploads_id = (
            items_ch[0]
            .get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not uploads_id:
            continue

        r_pl = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={
                "part": "contentDetails,snippet",
                "playlistId": uploads_id,
                "maxResults": 15,
                "key": api_key,
            },
            timeout=20,
        )
        if not r_pl.ok:
            continue

        video_ids = [
            it["contentDetails"]["videoId"]
            for it in r_pl.json().get("items", [])
            if it.get("contentDetails", {}).get("videoId")
        ]
        if not video_ids:
            continue

        r_vid = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,statistics",
                "id": ",".join(video_ids),
                "key": api_key,
            },
            timeout=20,
        )
        if not r_vid.ok:
            continue

        for v in r_vid.json().get("items", []):
            titulo = v.get("snippet", {}).get("title", "Sem título")
            pub_str = v.get("snippet", {}).get("publishedAt", "")
            views = int(v.get("statistics", {}).get("viewCount", 0) or 0)

            idade_dias = None
            if pub_str:
                dt_pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                idade_dias = (agora - dt_pub).total_seconds() / 86400.0

            marco_10m_atingido = (views // 10_000_000) * 10_000_000
            bateu_10m_em_7_dias = (
                marco_10m_atingido >= 10_000_000
                and idade_dias is not None
                and idade_dias <= 7.0
            )

            videos_coletados.append({
                "video_id": v.get("id", ""),
                "Título": titulo,
                "Canal": nome_canal,
                "Visualizações": views,
                "Idade (dias)": round(idade_dias, 1) if idade_dias is not None else None,
                "Marco 10M": marco_10m_atingido,
                "10M em 7 dias": bateu_10m_em_7_dias,
            })

    return videos_coletados


def carregar_metas_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ARQUIVO_METAS}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        dados = r.json()
        conteudo = base64.b64decode(dados["content"]).decode("utf-8")
        metas_raw = json.loads(conteudo)
        normalizadas = []
        for m in metas_raw:
            normalizadas.append({
                "musica": m["musica"],
                "meta": int(m["meta"]),
                "tipo": m.get("tipo", "postagem"),
                "plataforma": m.get("plataforma", "spotify"),
            })
        return normalizadas, dados["sha"]
    return [], None


def salvar_metas_github(lista_metas, sha):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ARQUIVO_METAS}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    texto_json = json.dumps(lista_metas, indent=2, ensure_ascii=False)
    conteudo_b64 = base64.b64encode(texto_json.encode("utf-8")).decode("utf-8")
    payload = {
        "message": "Atualiza metas pelo aplicativo",
        "content": conteudo_b64,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload, timeout=15)
    if not r.ok:
        st.error(f"Erro GitHub {r.status_code}: {r.text}")
        st.stop()


def indicar_substitutas_spotify(df: pd.DataFrame, meta_alvo: int, musicas_ocupadas: set):
    candidatas = df[~df["Música"].isin(musicas_ocupadas)].copy()
    if candidatas.empty:
        return {"proximidade": None, "ritmo": None}

    candidatas["distancia"] = (candidatas["Streams Totais"] - meta_alvo).abs()
    top_prox = candidatas.sort_values(by=["distancia", "Streams Diários"], ascending=[True, False]).iloc[0]

    resto = candidatas[candidatas["Música"] != top_prox["Música"]]
    base_ritmo = resto if not resto.empty else candidatas
    top_ritmo = base_ritmo.sort_values(by=["Streams Diários", "Streams Totais"], ascending=[False, False]).iloc[0]

    return {
        "proximidade": {
            "nome": top_prox["Música"],
            "streams": int(top_prox["Streams Totais"]),
            "daily": int(top_prox["Streams Diários"]),
        },
        "ritmo": {
            "nome": top_ritmo["Música"],
            "streams": int(top_ritmo["Streams Totais"]),
            "daily": int(top_ritmo["Streams Diários"]),
        },
    }


def indicar_substitutas_youtube(videos: list[dict], meta_alvo: int, ocupados: set):
    candidatas = [v for v in videos if v["Título"] not in ocupados]
    if not candidatas:
        return {"proximidade": None, "ritmo": None}

    top_prox = min(candidatas, key=lambda v: (abs(v["Visualizações"] - meta_alvo), -v["Visualizações"]))
    resto = [v for v in candidatas if v["Título"] != top_prox["Título"]]
    base_ritmo = resto if resto else candidatas
    top_ritmo = max(base_ritmo, key=lambda v: v["Visualizações"])

    return {
        "proximidade": {
            "nome": top_prox["Título"],
            "views": int(top_prox["Visualizações"]),
            "canal": top_prox["Canal"],
        },
        "ritmo": {
            "nome": top_ritmo["Título"],
            "views": int(top_ritmo["Visualizações"]),
            "canal": top_ritmo["Canal"],
        },
    }


st.title("🧭 Monitor de Metas — Stray Kids (Spotify & YouTube)")

if not GITHUB_TOKEN or not GITHUB_REPO:
    st.error("Configure GITHUB_TOKEN e GITHUB_REPO nos Secrets do Streamlit para salvar metas pelo app.")
    st.stop()

with st.sidebar:
    st.header("⚙️ Controles")
    if st.button("🔄 Atualizar números agora", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df_spotify = buscar_dados_kworb()
videos_yt = buscar_dados_youtube(YOUTUBE_API_KEY)
df_yt = pd.DataFrame(videos_yt) if videos_yt else pd.DataFrame()

metas, sha_atual = carregar_metas_github()
musicas_playlist_sp_ativas = {
    m["musica"]
    for m in metas
    if m.get("plataforma", "spotify") == "spotify" and m.get("tipo") == "playlist"
}
videos_yt_ativos = {m["musica"] for m in metas if m.get("plataforma") == "youtube"}

aba_sp, aba_yt = st.tabs(["🎵 Spotify (Kworb)", "▶️ YouTube (Automático)"])

# ---------------- ABA SPOTIFY ----------------
with aba_sp:
    if df_spotify.empty:
        st.warning("Não foi possível carregar os dados do Spotify no Kworb.")
    else:
        sub_post, sub_play = st.tabs(["📌 Metas de Postagem", "🎧 Metas de Playlist (300M / 200M / 100M / 50M)"])

        # --- SUB-ABA 1: APENAS POSTAGEM ---
        with sub_post:
            st.subheader("📌 Nova Meta de Postagem")
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                musica_post = st.selectbox("Escolha a música", df_spotify["Música"].tolist(), key="sp_sel_post")
            with c2:
                meta_post = st.number_input("Meta personalizada de streams", min_value=1_000_000, value=500_000_000, step=10_000_000, key="sp_num_post")
            with c3:
                st.write("")
                st.write("")
                if st.button("Salvar Postagem", use_container_width=True, key="sp_btn_post"):
                    nova = [
                        m for m in metas
                        if not (m["musica"] == musica_post and m.get("tipo", "postagem") == "postagem" and m.get("plataforma", "spotify") == "spotify")
                    ]
                    nova.append({"musica": musica_post, "meta": int(meta_post), "tipo": "postagem", "plataforma": "spotify"})
                    salvar_metas_github(nova, sha_atual)
                    st.success("Meta de postagem salva!")
                    st.rerun()

            st.divider()
            st.subheader("📋 Lista de Metas de Postagem")
            metas_postagem = [
                (i, m) for i, m in enumerate(metas)
                if m.get("plataforma", "spotify") == "spotify" and m.get("tipo", "postagem") == "postagem"
            ]

            if not metas_postagem:
                st.info("Nenhuma meta de postagem cadastrada ainda.")
            else:
                for idx, item in metas_postagem:
                    nome = item["musica"]
                    meta = int(item["meta"])
                    linha = df_spotify[df_spotify["Música"] == nome]
                    if linha.empty:
                        continue

                    atuais = int(linha.iloc[0]["Streams Totais"])
                    diarios = int(linha.iloc[0]["Streams Diários"])
                    progresso = min(atuais / meta, 1.0)
                    faltam = max(meta - atuais, 0)
                    dias_est = int(faltam / diarios) if diarios > 0 and faltam > 0 else 0

                    with st.container(border=True):
                        col_a, col_b, col_c, col_d, col_e = st.columns([3, 2, 2, 1.6, 0.8])
                        col_a.markdown(f"### 📌 {nome}")
                        col_a.progress(progresso, text=f"{progresso*100:.1f}% concluído")
                        col_b.metric("Streams Atuais", fmt_num(atuais))
                        col_c.metric("Meta", fmt_num(meta))
                        if atuais >= meta:
                            col_d.success("META BATIDA!")
                        else:
                            col_d.info(f"~{dias_est} dias" if dias_est else "Em andamento")
                        if col_e.button("🗑️", key=f"del_sp_post_{idx}"):
                            nova = [m for i, m in enumerate(metas) if i != idx]
                            salvar_metas_github(nova, sha_atual)
                            st.rerun()

        # --- SUB-ABA 2: APENAS PLAYLIST ---
        with sub_play:
            st.subheader("🎧 Adicionar Música à Playlist (Metas Fixas)")
            p1, p2, p3 = st.columns([3, 2, 1])
            with p1:
                musica_play = st.selectbox("Escolha a música para a playlist", df_spotify["Música"].tolist(), key="sp_sel_play")
            with p2:
                meta_play = st.selectbox("Meta fixa da playlist", options=METAS_PLAYLIST_FIXAS, format_func=fmt_num, key="sp_sel_meta_play")
            with p3:
                st.write("")
                st.write("")
                if st.button("Salvar Playlist", use_container_width=True, key="sp_btn_play"):
                    nova = [
                        m for m in metas
                        if not (m["musica"] == musica_play and m.get("tipo") == "playlist" and m.get("plataforma", "spotify") == "spotify")
                    ]
                    nova.append({"musica": musica_play, "meta": int(meta_play), "tipo": "playlist", "plataforma": "spotify"})
                    salvar_metas_github(nova, sha_atual)
                    st.success("Meta de playlist salva!")
                    st.rerun()

            st.divider()
            st.subheader("📋 Músicas Monitoradas na Playlist")
            metas_playlist = [
                (i, m) for i, m in enumerate(metas)
                if m.get("plataforma", "spotify") == "spotify" and m.get("tipo") == "playlist"
            ]

            if not metas_playlist:
                st.info("Nenhuma música na playlist ainda. Adicione uma acima!")
            else:
                for idx, item in metas_playlist:
                    nome = item["musica"]
                    meta = int(item["meta"])
                    linha = df_spotify[df_spotify["Música"] == nome]
                    if linha.empty:
                        continue

                    atuais = int(linha.iloc[0]["Streams Totais"])
                    diarios = int(linha.iloc[0]["Streams Diários"])
                    progresso = min(atuais / meta, 1.0)
                    faltam = max(meta - atuais, 0)
                    dias_est = int(faltam / diarios) if diarios > 0 and faltam > 0 else 0

                    with st.container(border=True):
                        col_a, col_b, col_c, col_d, col_e = st.columns([3, 2, 2, 1.6, 0.8])
                        col_a.markdown(f"### 🎧 {nome}")
                        col_a.progress(progresso, text=f"{progresso*100:.1f}% concluído")
                        col_b.metric("Streams Atuais", fmt_num(atuais))
                        col_c.metric("Meta da Playlist", fmt_num(meta))
                        if atuais >= meta:
                            col_d.success("VAGA LIBERADA!")
                        else:
                            col_d.info(f"~{dias_est} dias" if dias_est else "Na playlist")
                        if col_e.button("🗑️", key=f"del_sp_play_{idx}"):
                            nova = [m for i, m in enumerate(metas) if i != idx]
                            salvar_metas_github(nova, sha_atual)
                            st.rerun()

                        # Mostra as 2 sugestões diretamente na tela quando a meta de playlist bater
                        if atuais >= meta:
                            sug = indicar_substitutas_spotify(df_spotify, meta, musicas_playlist_sp_ativas)
                            prox = sug.get("proximidade")
                            ritmo = sug.get("ritmo")
                            s1, s2 = st.columns(2)
                            if prox:
                                s1.info(
                                    f"**1. Mais perto da meta ({fmt_num(meta)}):**\n\n"
                                    f"**{prox['nome']}** — {fmt_num(prox['streams'])} streams ({fmt_num(prox['daily'])}/dia)"
                                )
                            if ritmo:
                                s2.info(
                                    f"**2. Maior ritmo diário:**\n\n"
                                    f"**{ritmo['nome']}** — {fmt_num(ritmo['daily'])}/dia ({fmt_num(ritmo['streams'])} streams)"
                                )

# ---------------- ABA YOUTUBE ----------------
with aba_yt:
    if not YOUTUBE_API_KEY:
        st.warning("Adicione `YOUTUBE_API_KEY` nos Secrets do Streamlit para ativar o rastreamento do YouTube.")
    elif df_yt.empty:
        st.warning("Nenhum vídeo retornado pelos canais configurados no momento.")
    else:
        st.subheader("⚡ Marcos de 10M Batidos em até 7 Dias")
        destaques_7d = df_yt[df_yt["10M em 7 dias"] == True]
        if destaques_7d.empty:
            st.info("Nenhum vídeo atingiu marco de 10M dentro de 7 dias na última leitura.")
        else:
            for _, row in destaques_7d.iterrows():
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 2])
                    c1.markdown(f"**{row['Título']}**\n\nCanal: *{row['Canal']}*")
                    c2.metric("Visualizações", fmt_num(int(row["Visualizações"])))
                    c3.metric("Marco 10M / Idade", f"{fmt_num(int(row['Marco 10M']))} ({row['Idade (dias)']}d)")

        st.divider()
        st.subheader("🎧 Metas de Playlist — YouTube (300M / 200M / 100M / 50M)")
        yp1, yp2, yp3 = st.columns([3, 2, 1])
        with yp1:
            vid_play = st.selectbox("Escolha o vídeo para playlist", df_yt["Título"].tolist(), key="yt_sel_play")
        with yp2:
            meta_yt_play = st.selectbox("Meta fixa da playlist", options=METAS_PLAYLIST_FIXAS, format_func=fmt_num, key="yt_sel_meta_play")
        with yp3:
            st.write("")
            st.write("")
            if st.button("Salvar Playlist YT", use_container_width=True, key="yt_btn_play"):
                nova = [m for m in metas if not (m["musica"] == vid_play and m.get("plataforma") == "youtube" and m.get("tipo") == "playlist")]
                nova.append({"musica": vid_play, "meta": int(meta_yt_play), "tipo": "playlist", "plataforma": "youtube"})
                salvar_metas_github(nova, sha_atual)
                st.success("Meta de playlist do YouTube salva!")
                st.rerun()

        metas_yt = [
            (i, m) for i, m in enumerate(metas)
            if m.get("plataforma") == "youtube"
        ]
        if not metas_yt:
            st.info("Nenhum vídeo na playlist do YouTube ainda.")
        else:
            for idx, item in metas_yt:
                nome = item["musica"]
                meta = int(item["meta"])
                linha = df_yt[df_yt["Título"] == nome]
                if linha.empty:
                    continue

                views = int(linha.iloc[0]["Visualizações"])
                progresso = min(views / meta, 1.0)

                with st.container(border=True):
                    col_a, col_b, col_c, col_d, col_e = st.columns([3, 2, 2, 1.6, 0.8])
                    col_a.markdown(f"### 🎧 {nome}")
                    col_a.progress(progresso, text=f"{progresso*100:.1f}% concluído")
                    col_b.metric("Views Atuais", fmt_num(views))
                    col_c.metric("Meta", fmt_num(meta))
                    if views >= meta:
                        col_d.success("VAGA LIBERADA!")
                    else:
                        col_d.info("Monitorando")
                    if col_e.button("🗑️", key=f"del_yt_{idx}"):
                        nova = [m for i, m in enumerate(metas) if i != idx]
                        salvar_metas_github(nova, sha_atual)
                        st.rerun()

                    if views >= meta:
                        sug_yt = indicar_substitutas_youtube(videos_yt, meta, videos_yt_ativos)
                        prox = sug_yt.get("proximidade")
                        ritmo = sug_yt.get("ritmo")
                        ys1, ys2 = st.columns(2)
                        if prox:
                            ys1.info(f"**1. Mais perto da meta ({fmt_num(meta)}):**\n\n**{prox['nome']}** — {fmt_num(prox['views'])} views")
                        if ritmo:
                            ys2.info(f"**2. Mais visualizações:**\n\n**{ritmo['nome']}** — {fmt_num(ritmo['views'])} views")
import base64
import json
import re
from datetime import datetime, timezone
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="SKZ Stream & Views Tracker", page_icon="🧭", layout="wide")

KWORB_URL = "https://kworb.net/spotify/artist/2dIgFjalVxs4ThymZ67YCE_songs.html"
YOUTUBE_HANDLES = [
    {"nome": "Stray Kids Oficial", "handle": "straykids"},
    {"nome": "Stray Kids Japan Official", "handle": "straykidsjapanofficial"},
    {"nome": "JYP Entertainment", "handle": "jypentertainment"},
]

GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")
GITHUB_REPO = st.secrets.get("GITHUB_REPO", "")
YOUTUBE_API_KEY = st.secrets.get("YOUTUBE_API_KEY", "")

ARQUIVO_METAS = "metas.json"
METAS_PLAYLIST_FIXAS = [300_000_000, 200_000_000, 100_000_000, 50_000_000]


def fmt_num(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


@st.cache_data(ttl=3600)
def buscar_dados_kworb():
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(KWORB_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

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


@st.cache_data(ttl=3600)
def buscar_dados_youtube(api_key: str):
    if not api_key:
        return []

    videos_coletados = []
    agora = datetime.now(timezone.utc)

    for canal in YOUTUBE_HANDLES:
        handle = canal["handle"]
        nome_canal = canal["nome"]

        r_ch = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "contentDetails,snippet", "forHandle": handle, "key": api_key},
            timeout=20,
        )
        if not r_ch.ok:
            continue

        items_ch = r_ch.json().get("items", [])
        if not items_ch:
            continue

        uploads_id = (
            items_ch[0]
            .get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not uploads_id:
            continue

        r_pl = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={
                "part": "contentDetails,snippet",
                "playlistId": uploads_id,
                "maxResults": 15,
                "key": api_key,
            },
            timeout=20,
        )
        if not r_pl.ok:
            continue

        video_ids = [
            it["contentDetails"]["videoId"]
            for it in r_pl.json().get("items", [])
            if it.get("contentDetails", {}).get("videoId")
        ]
        if not video_ids:
            continue

        r_vid = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,statistics",
                "id": ",".join(video_ids),
                "key": api_key,
            },
            timeout=20,
        )
        if not r_vid.ok:
            continue

        for v in r_vid.json().get("items", []):
            titulo = v.get("snippet", {}).get("title", "Sem título")
            pub_str = v.get("snippet", {}).get("publishedAt", "")
            views = int(v.get("statistics", {}).get("viewCount", 0) or 0)

            idade_dias = None
            if pub_str:
                dt_pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                idade_dias = (agora - dt_pub).total_seconds() / 86400.0

            marco_10m_atingido = (views // 10_000_000) * 10_000_000
            bateu_10m_em_7_dias = (
                marco_10m_atingido >= 10_000_000
                and idade_dias is not None
                and idade_dias <= 7.0
            )

            videos_coletados.append({
                "video_id": v.get("id", ""),
                "Título": titulo,
                "Canal": nome_canal,
                "Visualizações": views,
                "Idade (dias)": round(idade_dias, 1) if idade_dias is not None else None,
                "Marco 10M": marco_10m_atingido,
                "10M em 7 dias": bateu_10m_em_7_dias,
            })

    return videos_coletados


def carregar_metas_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ARQUIVO_METAS}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        dados = r.json()
        conteudo = base64.b64decode(dados["content"]).decode("utf-8")
        metas_raw = json.loads(conteudo)
        normalizadas = []
        for m in metas_raw:
            normalizadas.append({
                "musica": m["musica"],
                "meta": int(m["meta"]),
                "tipo": m.get("tipo", "postagem"),
                "plataforma": m.get("plataforma", "spotify"),
            })
        return normalizadas, dados["sha"]
    return [], None


def salvar_metas_github(lista_metas, sha):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ARQUIVO_METAS}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    texto_json = json.dumps(lista_metas, indent=2, ensure_ascii=False)
    conteudo_b64 = base64.b64encode(texto_json.encode("utf-8")).decode("utf-8")
    payload = {
        "message": "Atualiza metas pelo aplicativo",
        "content": conteudo_b64,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload, timeout=15)
    if not r.ok:
        st.error(f"Erro GitHub {r.status_code}: {r.text}")
        st.stop()


def indicar_substitutas_spotify(df: pd.DataFrame, meta_alvo: int, musicas_ocupadas: set):
    candidatas = df[~df["Música"].isin(musicas_ocupadas)].copy()
    if candidatas.empty:
        return {"proximidade": None, "ritmo": None}

    candidatas["distancia"] = (candidatas["Streams Totais"] - meta_alvo).abs()
    top_prox = candidatas.sort_values(by=["distancia", "Streams Diários"], ascending=[True, False]).iloc[0]

    resto = candidatas[candidatas["Música"] != top_prox["Música"]]
    base_ritmo = resto if not resto.empty else candidatas
    top_ritmo = base_ritmo.sort_values(by=["Streams Diários", "Streams Totais"], ascending=[False, False]).iloc[0]

    return {
        "proximidade": {
            "nome": top_prox["Música"],
            "streams": int(top_prox["Streams Totais"]),
            "daily": int(top_prox["Streams Diários"]),
        },
        "ritmo": {
            "nome": top_ritmo["Música"],
            "streams": int(top_ritmo["Streams Totais"]),
            "daily": int(top_ritmo["Streams Diários"]),
        },
    }


def indicar_substitutas_youtube(videos: list[dict], meta_alvo: int, ocupados: set):
    candidatas = [v for v in videos if v["Título"] not in ocupados]
    if not candidatas:
        return {"proximidade": None, "ritmo": None}

    top_prox = min(candidatas, key=lambda v: (abs(v["Visualizações"] - meta_alvo), -v["Visualizações"]))
    resto = [v for v in candidatas if v["Título"] != top_prox["Título"]]
    base_ritmo = resto if resto else candidatas
    top_ritmo = max(base_ritmo, key=lambda v: v["Visualizações"])

    return {
        "proximidade": {
            "nome": top_prox["Título"],
            "views": int(top_prox["Visualizações"]),
            "canal": top_prox["Canal"],
        },
        "ritmo": {
            "nome": top_ritmo["Título"],
            "views": int(top_ritmo["Visualizações"]),
            "canal": top_ritmo["Canal"],
        },
    }


st.title("🧭 Monitor de Metas — Stray Kids (Spotify & YouTube)")

if not GITHUB_TOKEN or not GITHUB_REPO:
    st.error("Configure GITHUB_TOKEN e GITHUB_REPO nos Secrets do Streamlit para salvar metas pelo app.")
    st.stop()

with st.sidebar:
    st.header("⚙️ Controles")
    if st.button("🔄 Atualizar números agora", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df_spotify = buscar_dados_kworb()
videos_yt = buscar_dados_youtube(YOUTUBE_API_KEY)
df_yt = pd.DataFrame(videos_yt) if videos_yt else pd.DataFrame()

metas, sha_atual = carregar_metas_github()
musicas_playlist_sp_ativas = {
    m["musica"]
    for m in metas
    if m.get("plataforma", "spotify") == "spotify" and m.get("tipo") == "playlist"
}
videos_yt_ativos = {m["musica"] for m in metas if m.get("plataforma") == "youtube"}

aba_sp, aba_yt = st.tabs(["🎵 Spotify (Kworb)", "▶️ YouTube (Automático)"])

# ---------------- ABA SPOTIFY ----------------
with aba_sp:
    if df_spotify.empty:
        st.warning("Não foi possível carregar os dados do Spotify no Kworb.")
    else:
        sub_post, sub_play = st.tabs(["📌 Metas de Postagem", "🎧 Metas de Playlist (300M / 200M / 100M / 50M)"])

        # --- SUB-ABA 1: APENAS POSTAGEM ---
        with sub_post:
            st.subheader("📌 Nova Meta de Postagem")
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                musica_post = st.selectbox("Escolha a música", df_spotify["Música"].tolist(), key="sp_sel_post")
            with c2:
                meta_post = st.number_input("Meta personalizada de streams", min_value=1_000_000, value=500_000_000, step=10_000_000, key="sp_num_post")
            with c3:
                st.write("")
                st.write("")
                if st.button("Salvar Postagem", use_container_width=True, key="sp_btn_post"):
                    nova = [
                        m for m in metas
                        if not (m["musica"] == musica_post and m.get("tipo", "postagem") == "postagem" and m.get("plataforma", "spotify") == "spotify")
                    ]
                    nova.append({"musica": musica_post, "meta": int(meta_post), "tipo": "postagem", "plataforma": "spotify"})
                    salvar_metas_github(nova, sha_atual)
                    st.success("Meta de postagem salva!")
                    st.rerun()

            st.divider()
            st.subheader("📋 Lista de Metas de Postagem")
            metas_postagem = [
                (i, m) for i, m in enumerate(metas)
                if m.get("plataforma", "spotify") == "spotify" and m.get("tipo", "postagem") == "postagem"
            ]

            if not metas_postagem:
                st.info("Nenhuma meta de postagem cadastrada ainda.")
            else:
                for idx, item in metas_postagem:
                    nome = item["musica"]
                    meta = int(item["meta"])
                    linha = df_spotify[df_spotify["Música"] == nome]
                    if linha.empty:
                        continue

                    atuais = int(linha.iloc[0]["Streams Totais"])
                    diarios = int(linha.iloc[0]["Streams Diários"])
                    progresso = min(atuais / meta, 1.0)
                    faltam = max(meta - atuais, 0)
                    dias_est = int(faltam / diarios) if diarios > 0 and faltam > 0 else 0

                    with st.container(border=True):
                        col_a, col_b, col_c, col_d, col_e = st.columns([3, 2, 2, 1.6, 0.8])
                        col_a.markdown(f"### 📌 {nome}")
                        col_a.progress(progresso, text=f"{progresso*100:.1f}% concluído")
                        col_b.metric("Streams Atuais", fmt_num(atuais))
                        col_c.metric("Meta", fmt_num(meta))
                        if atuais >= meta:
                            col_d.success("META BATIDA!")
                        else:
                            col_d.info(f"~{dias_est} dias" if dias_est else "Em andamento")
                        if col_e.button("🗑️", key=f"del_sp_post_{idx}"):
                            nova = [m for i, m in enumerate(metas) if i != idx]
                            salvar_metas_github(nova, sha_atual)
                            st.rerun()

        # --- SUB-ABA 2: APENAS PLAYLIST ---
        with sub_play:
            st.subheader("🎧 Adicionar Música à Playlist (Metas Fixas)")
            p1, p2, p3 = st.columns([3, 2, 1])
            with p1:
                musica_play = st.selectbox("Escolha a música para a playlist", df_spotify["Música"].tolist(), key="sp_sel_play")
            with p2:
                meta_play = st.selectbox("Meta fixa da playlist", options=METAS_PLAYLIST_FIXAS, format_func=fmt_num, key="sp_sel_meta_play")
            with p3:
                st.write("")
                st.write("")
                if st.button("Salvar Playlist", use_container_width=True, key="sp_btn_play"):
                    nova = [
                        m for m in metas
                        if not (m["musica"] == musica_play and m.get("tipo") == "playlist" and m.get("plataforma", "spotify") == "spotify")
                    ]
                    nova.append({"musica": musica_play, "meta": int(meta_play), "tipo": "playlist", "plataforma": "spotify"})
                    salvar_metas_github(nova, sha_atual)
                    st.success("Meta de playlist salva!")
                    st.rerun()

            st.divider()
            st.subheader("📋 Músicas Monitoradas na Playlist")
            metas_playlist = [
                (i, m) for i, m in enumerate(metas)
                if m.get("plataforma", "spotify") == "spotify" and m.get("tipo") == "playlist"
            ]

            if not metas_playlist:
                st.info("Nenhuma música na playlist ainda. Adicione uma acima!")
            else:
                for idx, item in metas_playlist:
                    nome = item["musica"]
                    meta = int(item["meta"])
                    linha = df_spotify[df_spotify["Música"] == nome]
                    if linha.empty:
                        continue

                    atuais = int(linha.iloc[0]["Streams Totais"])
                    diarios = int(linha.iloc[0]["Streams Diários"])
                    progresso = min(atuais / meta, 1.0)
                    faltam = max(meta - atuais, 0)
                    dias_est = int(faltam / diarios) if diarios > 0 and faltam > 0 else 0

                    with st.container(border=True):
                        col_a, col_b, col_c, col_d, col_e = st.columns([3, 2, 2, 1.6, 0.8])
                        col_a.markdown(f"### 🎧 {nome}")
                        col_a.progress(progresso, text=f"{progresso*100:.1f}% concluído")
                        col_b.metric("Streams Atuais", fmt_num(atuais))
                        col_c.metric("Meta da Playlist", fmt_num(meta))
                        if atuais >= meta:
                            col_d.success("VAGA LIBERADA!")
                        else:
                            col_d.info(f"~{dias_est} dias" if dias_est else "Na playlist")
                        if col_e.button("🗑️", key=f"del_sp_play_{idx}"):
                            nova = [m for i, m in enumerate(metas) if i != idx]
                            salvar_metas_github(nova, sha_atual)
                            st.rerun()

                        # Mostra as 2 sugestões diretamente na tela quando a meta de playlist bater
                        if atuais >= meta:
                            sug = indicar_substitutas_spotify(df_spotify, meta, musicas_playlist_sp_ativas)
                            prox = sug.get("proximidade")
                            ritmo = sug.get("ritmo")
                            s1, s2 = st.columns(2)
                            if prox:
                                s1.info(
                                    f"**1. Mais perto da meta ({fmt_num(meta)}):**\n\n"
                                    f"**{prox['nome']}** — {fmt_num(prox['streams'])} streams ({fmt_num(prox['daily'])}/dia)"
                                )
                            if ritmo:
                                s2.info(
                                    f"**2. Maior ritmo diário:**\n\n"
                                    f"**{ritmo['nome']}** — {fmt_num(ritmo['daily'])}/dia ({fmt_num(ritmo['streams'])} streams)"
                                )

# ---------------- ABA YOUTUBE ----------------
with aba_yt:
    if not YOUTUBE_API_KEY:
        st.warning("Adicione `YOUTUBE_API_KEY` nos Secrets do Streamlit para ativar o rastreamento do YouTube.")
    elif df_yt.empty:
        st.warning("Nenhum vídeo retornado pelos canais configurados no momento.")
    else:
        st.subheader("⚡ Marcos de 10M Batidos em até 7 Dias")
        destaques_7d = df_yt[df_yt["10M em 7 dias"] == True]
        if destaques_7d.empty:
            st.info("Nenhum vídeo atingiu marco de 10M dentro de 7 dias na última leitura.")
        else:
            for _, row in destaques_7d.iterrows():
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 2])
                    c1.markdown(f"**{row['Título']}**\n\nCanal: *{row['Canal']}*")
                    c2.metric("Visualizações", fmt_num(int(row["Visualizações"])))
                    c3.metric("Marco 10M / Idade", f"{fmt_num(int(row['Marco 10M']))} ({row['Idade (dias)']}d)")

        st.divider()
        st.subheader("🎧 Metas de Playlist — YouTube (300M / 200M / 100M / 50M)")
        yp1, yp2, yp3 = st.columns([3, 2, 1])
        with yp1:
            vid_play = st.selectbox("Escolha o vídeo para playlist", df_yt["Título"].tolist(), key="yt_sel_play")
        with yp2:
            meta_yt_play = st.selectbox("Meta fixa da playlist", options=METAS_PLAYLIST_FIXAS, format_func=fmt_num, key="yt_sel_meta_play")
        with yp3:
            st.write("")
            st.write("")
            if st.button("Salvar Playlist YT", use_container_width=True, key="yt_btn_play"):
                nova = [m for m in metas if not (m["musica"] == vid_play and m.get("plataforma") == "youtube" and m.get("tipo") == "playlist")]
                nova.append({"musica": vid_play, "meta": int(meta_yt_play), "tipo": "playlist", "plataforma": "youtube"})
                salvar_metas_github(nova, sha_atual)
                st.success("Meta de playlist do YouTube salva!")
                st.rerun()

        metas_yt = [
            (i, m) for i, m in enumerate(metas)
            if m.get("plataforma") == "youtube"
        ]
        if not metas_yt:
            st.info("Nenhum vídeo na playlist do YouTube ainda.")
        else:
            for idx, item in metas_yt:
                nome = item["musica"]
                meta = int(item["meta"])
                linha = df_yt[df_yt["Título"] == nome]
                if linha.empty:
                    continue

                views = int(linha.iloc[0]["Visualizações"])
                progresso = min(views / meta, 1.0)

                with st.container(border=True):
                    col_a, col_b, col_c, col_d, col_e = st.columns([3, 2, 2, 1.6, 0.8])
                    col_a.markdown(f"### 🎧 {nome}")
                    col_a.progress(progresso, text=f"{progresso*100:.1f}% concluído")
                    col_b.metric("Views Atuais", fmt_num(views))
                    col_c.metric("Meta", fmt_num(meta))
                    if views >= meta:
                        col_d.success("VAGA LIBERADA!")
                    else:
                        col_d.info("Monitorando")
                    if col_e.button("🗑️", key=f"del_yt_{idx}"):
                        nova = [m for i, m in enumerate(metas) if i != idx]
                        salvar_metas_github(nova, sha_atual)
                        st.rerun()

                    if views >= meta:
                        sug_yt = indicar_substitutas_youtube(videos_yt, meta, videos_yt_ativos)
                        prox = sug_yt.get("proximidade")
                        ritmo = sug_yt.get("ritmo")
                        ys1, ys2 = st.columns(2)
                        if prox:
                            ys1.info(f"**1. Mais perto da meta ({fmt_num(meta)}):**\n\n**{prox['nome']}** — {fmt_num(prox['views'])} views")
                        if ritmo:
                            ys2.info(f"**2. Mais visualizações:**\n\n**{ritmo['nome']}** — {fmt_num(ritmo['views'])} views")
