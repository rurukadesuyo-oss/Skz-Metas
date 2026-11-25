import json
import os
import re
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

KWORB_URL = "https://kworb.net/spotify/artist/2dIgFjalVxs4ThymZ67YCE_songs.html"
YOUTUBE_HANDLES = [
    {"nome": "Stray Kids Oficial", "handle": "straykids"},
    {"nome": "Stray Kids Japan Official", "handle": "straykidsjapanofficial"},
    {"nome": "JYP Entertainment", "handle": "jypentertainment"},
]

ARQUIVO_METAS = "metas.json"
ARQUIVO_NOTIFICADAS = "notificadas.json"
ARQUIVO_ASSINANTES = "assinantes.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID_PADRAO = os.environ.get("TELEGRAM_CHAT_ID", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")


def fmt_num(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def eh_video_stray_kids(handle: str, titulo: str, descricao: str = "") -> bool:
    if handle != "jypentertainment":
        return True
    texto = f"{titulo} {descricao}".lower()
    termos = ["stray kids", "스트레이 키즈", "#straykids"]
    return any(t in texto for t in termos)


def buscar_dados_kworb() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(KWORB_URL, headers=headers, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tabela_musicas = None
    for t in soup.find_all("table"):
        if "Song Title" in t.get_text():
            tabela_musicas = t
            break

    itens = []
    if not tabela_musicas:
        return itens

    for linha in tabela_musicas.find_all("tr")[1:]:
        colunas = linha.find_all("td")
        if len(colunas) >= 2:
            link = colunas[0].find("a")
            titulo = link.get_text(strip=True) if link else colunas[0].get_text(strip=True)
            streams_str = re.sub(r"[^\d]", "", colunas[1].get_text())
            daily_str = re.sub(r"[^\d]", "", colunas[2].get_text()) if len(colunas) > 2 else "0"
            if titulo and streams_str:
                itens.append({
                    "musica": titulo,
                    "streams": int(streams_str),
                    "daily": int(daily_str) if daily_str else 0,
                })
    return itens


def buscar_dados_youtube(api_key: str) -> list[dict]:
    if not api_key:
        return []

    videos = []
    agora = datetime.now(timezone.utc)

    for canal in YOUTUBE_HANDLES:
        handle = canal["handle"]
        r_ch = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "contentDetails", "forHandle": handle, "key": api_key},
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

        max_busca = 30 if handle == "jypentertainment" else 15
        r_pl = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={"part": "contentDetails", "playlistId": uploads_id, "maxResults": max_busca, "key": api_key},
            timeout=20,
        )
        if not r_pl.ok:
            continue

        vids = [
            it["contentDetails"]["videoId"]
            for it in r_pl.json().get("items", [])
            if it.get("contentDetails", {}).get("videoId")
        ]
        if not vids:
            continue

        r_vid = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,statistics", "id": ",".join(vids), "key": api_key},
            timeout=20,
        )
        if not r_vid.ok:
            continue

        for v in r_vid.json().get("items", []):
            titulo = v.get("snippet", {}).get("title", "Sem título")
            descricao = v.get("snippet", {}).get("description", "")
            if not eh_video_stray_kids(handle, titulo, descricao):
                continue

            pub_str = v.get("snippet", {}).get("publishedAt", "")
            views = int(v.get("statistics", {}).get("viewCount", 0) or 0)
            idade_dias = None
            if pub_str:
                dt_pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                idade_dias = (agora - dt_pub).total_seconds() / 86400.0

            videos.append({
                "titulo": titulo,
                "canal": canal["nome"],
                "views": views,
                "idade_dias": idade_dias,
            })

    return videos


def carregar_json(caminho: str, padrao):
    if not os.path.exists(caminho):
        return padrao
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_json(caminho: str, conteudo) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(conteudo, f, indent=2, ensure_ascii=False)


def obter_destinatarios() -> list:
    assinantes = carregar_json(ARQUIVO_ASSINANTES, [])
    ids = []
    for item in assinantes:
        valor = str(item).strip()
        if valor and valor not in ids:
            ids.append(valor)
    if not ids and TELEGRAM_CHAT_ID_PADRAO:
        ids.append(str(TELEGRAM_CHAT_ID_PADRAO).strip())
    return ids


def enviar_telegram_para_todos(mensagem: str) -> None:
    destinatarios = obter_destinatarios()
    for chat_id in destinatarios:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN.strip()}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as erro:
            print(f"Não foi possível enviar para {chat_id}: {erro}")


def indicar_substitutas_spotify(lista_kworb: list[dict], meta_alvo: int, ocupadas: set) -> dict:
    candidatas = [m for m in lista_kworb if m["musica"] not in ocupadas]
    if not candidatas:
        return {"proximidade": None, "ritmo": None}

    top_prox = min(candidatas, key=lambda m: (abs(m["streams"] - meta_alvo), -m["daily"]))
    resto = [m for m in candidatas if m["musica"] != top_prox["musica"]]
    base_ritmo = resto if resto else candidatas
    top_ritmo = max(base_ritmo, key=lambda m: (m["daily"], m["streams"]))
    return {"proximidade": top_prox, "ritmo": top_ritmo}


def indicar_substitutas_youtube(videos: list[dict], meta_alvo: int, ocupados: set) -> dict:
    candidatas = [v for v in videos if v["titulo"] not in ocupados]
    if not candidatas:
        return {"proximidade": None, "ritmo": None}

    top_prox = min(candidatas, key=lambda v: (abs(v["views"] - meta_alvo), -v["views"]))
    resto = [v for v in candidatas if v["titulo"] != top_prox["titulo"]]
    base_ritmo = resto if resto else candidatas
    top_ritmo = max(base_ritmo, key=lambda v: v["views"])
    return {"proximidade": top_prox, "ritmo": top_ritmo}


def main():
    lista_kworb = buscar_dados_kworb()
    mapa_streams = {m["musica"]: m["streams"] for m in lista_kworb}

    videos_yt = buscar_dados_youtube(YOUTUBE_API_KEY)
    mapa_views = {v["titulo"]: v["views"] for v in videos_yt}

    metas = carregar_json(ARQUIVO_METAS, [])
    notificadas = carregar_json(ARQUIVO_NOTIFICADAS, {})

    ocupadas_sp = {m["musica"] for m in metas if m.get("plataforma", "spotify") == "spotify"}
    ocupadas_yt = {m["musica"] for m in metas if m.get("plataforma") == "youtube"}
    alertas = []

    for item in metas:
        nome = item["musica"]
        meta = int(item["meta"])
        tipo = item.get("tipo", "postagem")
        plat = item.get("plataforma", "spotify")

        atual = mapa_views.get(nome) if plat == "youtube" else mapa_streams.get(nome)
        if atual is None:
            continue

        chave = f"{plat}:{tipo}:{nome}"
        ja_avisadas = set(notificadas.get(chave, []))

        if atual >= meta and meta not in ja_avisadas:
            if plat == "spotify" and tipo == "playlist":
                sug = indicar_substitutas_spotify(lista_kworb, meta, ocupadas_sp)
                extra_txt = ""
                if sug["proximidade"]:
                    p = sug["proximidade"]
                    extra_txt += f"\n  ↳ <i>Mais perto:</i> <b>{p['musica']}</b> (<code>{fmt_num(p['streams'])}</code> | <code>{fmt_num(p['daily'])}</code>/dia)"
                if sug["ritmo"]:
                    r = sug["ritmo"]
                    extra_txt += f"\n  ↳ <i>Maior ritmo diário:</i> <b>{r['musica']}</b> (<code>{fmt_num(r['daily'])}</code>/dia | <code>{fmt_num(r['streams'])}</code>)"
                alertas.append(
                    f"• 🎧 <b>{nome}</b> (Spotify Playlist) bateu <code>{fmt_num(meta)}</code>! (Atual: <code>{fmt_num(atual)}</code>){extra_txt}"
                )
            elif plat == "youtube" and tipo == "playlist":
                sug = indicar_substitutas_youtube(videos_yt, meta, ocupadas_yt)
                extra_txt = ""
                if sug["proximidade"]:
                    p = sug["proximidade"]
                    extra_txt += f"\n  ↳ <i>Mais perto:</i> <b>{p['nome']}</b> (<code>{fmt_num(p['valor'])}</code> views)"
                if sug["ritmo"]:
                    r = sug["ritmo"]
                    extra_txt += f"\n  ↳ <i>Mais views:</i> <b>{r['nome']}</b> (<code>{fmt_num(r['valor'])}</code> views)"
                alertas.append(
                    f"• ▶️ <b>{nome}</b> (YouTube Playlist) bateu <code>{fmt_num(meta)}</code>! (Atual: <code>{fmt_num(atual)}</code>){extra_txt}"
                )
            else:
                alertas.append(
                    f"• 📌 <b>{nome}</b> ({plat.upper()}) bateu <code>{fmt_num(meta)}</code>! (Atual: <code>{fmt_num(atual)}</code>)"
                )

            ja_avisadas.add(meta)
            notificadas[chave] = sorted(ja_avisadas)

    for v in videos_yt:
        titulo = v["titulo"]
        views = v["views"]
        canal = v["canal"]
        idade_dias = v["idade_dias"]

        marcos_atingidos = []
        if views >= 1_000_000:
            marcos_atingidos.append(1_000_000)

        max_10m = (views // 10_000_000) * 10_000_000
        for m10 in range(10_000_000, max_10m + 1, 10_000_000):
            marcos_atingidos.append(m10)

        chave_yt = f"yt_auto:{titulo}"
        ja_yt = set(notificadas.get(chave_yt, []))

        for marco in marcos_atingidos:
            if marco not in ja_yt:
                destaque_7d = ""
                if marco >= 10_000_000 and idade_dias is not None and idade_dias <= 7.0:
                    destaque_7d = f" ⚡ <b>BATIDA EM {idade_dias:.1f} DIAS!</b>"

                alertas.append(
                    f"• ▶️ <b>{titulo}</b> ({canal}) atingiu <code>{fmt_num(marco)}</code> views!{destaque_7d}\n"
                    f"  Atual: <code>{fmt_num(views)}</code>"
                )
                ja_yt.add(marco)

        if ja_yt:
            notificadas[chave_yt] = sorted(ja_yt)

    if alertas:
        msg = "🎉 <b>Atualização de Metas — Stray Kids!</b>\n\n" + "\n\n".join(alertas)
        enviar_telegram_para_todos(msg)
        salvar_json(ARQUIVO_NOTIFICADAS, notificadas)
        print(f"{len(alertas)} alerta(s) enviado(s).")
    else:
        print("Nenhuma nova meta atingida.")


if __name__ == "__main__":
    main()
