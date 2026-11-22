import json
import os
import re
import requests
from bs4 import BeautifulSoup

KWORB_URL = "https://kworb.net/spotify/artist/2dIgFjalVxs4ThymZ67YCE_songs.html"
ARQUIVO_METAS = "metas.json"
ARQUIVO_NOTIFICADAS = "notificadas.json"
ARQUIVO_ASSINANTES = "assinantes.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
# Mantém o seu ID como reserva caso assinantes.json não exista
TELEGRAM_CHAT_ID_PADRAO = os.environ.get("TELEGRAM_CHAT_ID", "")


def buscar_streams_kworb() -> dict:
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(KWORB_URL, headers=headers, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tabela_musicas = None
    for t in soup.find_all("table"):
        if "Song Title" in t.get_text():
            tabela_musicas = t
            break

    dados = {}
    if not tabela_musicas:
        return dados

    for linha in tabela_musicas.find_all("tr")[1:]:
        colunas = linha.find_all("td")
        if len(colunas) >= 2:
            link = colunas[0].find("a")
            titulo = link.get_text(strip=True) if link else colunas[0].get_text(strip=True)
            streams_str = re.sub(r"[^\d]", "", colunas[1].get_text())
            if titulo and streams_str:
                dados[titulo] = int(streams_str)
    return dados


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
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as erro:
            # Se alguém bloqueou o bot ou não mandou "Oi" ainda, não trava o envio para os outros
            print(f"Não foi possível enviar para {chat_id}: {erro}")


def main():
    streams_atuais = buscar_streams_kworb()
    metas = carregar_json(ARQUIVO_METAS, [])
    notificadas = carregar_json(ARQUIVO_NOTIFICADAS, {})

    alertas = []

    for item in metas:
        musica = item["musica"]
        meta = int(item["meta"])
        atuais = streams_atuais.get(musica)

        if atuais is None:
            print(f"Música não encontrada no Kworb: {musica}")
            continue

        ja_avisadas = set(notificadas.get(musica, []))

        if atuais >= meta and meta not in ja_avisadas:
            alertas.append(
                f"• <b>{musica}</b>\n"
                f"  Meta: <code>{meta:,}</code>\n"
                f"  Atual: <code>{atuais:,}</code>".replace(",", ".")
            )
            ja_avisadas.add(meta)
            notificadas[musica] = sorted(ja_avisadas)

    if alertas:
        msg = "🎉 <b>Meta de Streams Atingida — Stray Kids!</b>\n\n" + "\n\n".join(alertas)
        enviar_telegram_para_todos(msg)
        salvar_json(ARQUIVO_NOTIFICADAS, notificadas)
        print(f"{len(alertas)} alerta(s) enviado(s).")
    else:
        print("Nenhuma nova meta atingida hoje.")


if __name__ == "__main__":
    main()
