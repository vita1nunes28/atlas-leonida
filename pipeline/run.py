"""Atlas de Leonida: coleta fontes (YouTube, Reddit, wiki), extrai fichas com o Gemini e atualiza public/data/fichas.json."""
import difflib, html, json, mimetypes, os, re, sys, time, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from google import genai
from google.genai import types as genai_types

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "public" / "data" / "fichas.json"
STATE = ROOT / "pipeline" / "state.json"
MODEL_ENV = os.getenv("GEMINI_MODEL")
MODEL_CANDIDATOS = ([MODEL_ENV] if MODEL_ENV else []) + ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-flash-latest"]
_modelo_ok = {"nome": None}
MAX_NOVOS = int(os.getenv("MAX_NOVOS", "40"))  # fontes novas processadas por rodada (controla custo)
UA = "AtlasLeonida/1.0 (guia de fa de GTA 6)"
REGIOES = ["Vice City", "Leonida Keys", "Grassrivers", "Port Gellhorn", "Ambrosia", "Mount Kalaga"]
CATS = {"col", "car", "gun", "heist", "farm"}
KEYWORDS = re.compile(r"locat|where|found|find|guide|collect|spawn|how to|money|cash|heist|robber|rare|secret|hidden|map|weapon|gun|car|vehicle|farm", re.I)


def log(*a):
    print(*a, flush=True)


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:80]


# ---------------- Fontes ----------------

def fonte_youtube():
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        log("YouTube: sem YOUTUBE_API_KEY, pulando")
        return []
    desde = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    buscas = ["GTA 6 all collectibles locations", "GTA 6 rare car location", "GTA 6 money guide",
              "GTA 6 weapon locations", "GTA 6 secret location", "GTA 6 heist guide",
              "GTA 6 localização carro raro", "GTA 6 como ganhar dinheiro"]
    ids = []
    for q in buscas:
        r = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=30, params={
            "key": key, "q": q, "part": "id", "type": "video", "maxResults": 15,
            "publishedAfter": desde, "order": "relevance"})
        if r.ok:
            ids += [i["id"]["videoId"] for i in r.json().get("items", [])]
        else:
            log("YouTube busca falhou:", r.status_code, r.text[:200])
    out = []
    for i in range(0, len(set(ids)), 50):
        lote = list(dict.fromkeys(ids))[i:i + 50]
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=30,
                         params={"key": key, "id": ",".join(lote), "part": "snippet"})
        if not r.ok:
            continue
        for v in r.json().get("items", []):
            s = v["snippet"]
            thumb = (s.get("thumbnails", {}).get("high") or s.get("thumbnails", {}).get("default") or {}).get("url")
            out.append({"id": "yt:" + v["id"], "tipo": "YouTube", "url": f"https://www.youtube.com/watch?v={v['id']}",
                        "autor": s.get("channelTitle", ""), "titulo": s.get("title", ""),
                        "texto": s.get("title", "") + "\n\n" + s.get("description", ""),
                        "imagens": [thumb] if thumb else [], "video_id": v["id"]})
    log(f"YouTube: {len(out)} vídeos")
    return out


def fonte_reddit():
    cid, sec = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and sec):
        log("Reddit: sem credenciais, pulando")
        return []
    tok = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, sec),
                        data={"grant_type": "client_credentials"}, headers={"User-Agent": UA}, timeout=30)
    if not tok.ok:
        log("Reddit: falha no token", tok.status_code)
        return []
    h = {"Authorization": "bearer " + tok.json()["access_token"], "User-Agent": UA}
    out = []
    for sub in ["GTA6", "GTA", "GTAVI"]:
        r = requests.get(f"https://oauth.reddit.com/r/{sub}/new", headers=h, params={"limit": 100}, timeout=30)
        if not r.ok:
            continue
        for c in r.json()["data"]["children"]:
            p = c["data"]
            texto = p.get("title", "") + "\n\n" + (p.get("selftext") or "")
            if p.get("over_18") or not KEYWORDS.search(texto) or p.get("score", 0) < 20:
                continue
            imgs = []
            if p.get("post_hint") == "image" and p.get("url"):
                imgs.append(p["url"])
            for m in list((p.get("media_metadata") or {}).values())[:3]:
                u = (m.get("s") or {}).get("u")
                if u:
                    imgs.append(html.unescape(u))
            out.append({"id": "rd:" + p["id"], "tipo": "Reddit", "url": "https://www.reddit.com" + p["permalink"],
                        "autor": "u/" + p.get("author", ""), "titulo": p.get("title", ""), "texto": texto[:8000],
                        "imagens": imgs[:3]})
        time.sleep(1)
    log(f"Reddit: {len(out)} posts")
    return out


def fonte_wiki():
    api = "https://gta.fandom.com/api.php"
    termos = ["Grand Theft Auto VI vehicles", "Grand Theft Auto VI weapons", "Grand Theft Auto VI collectibles",
              "Grand Theft Auto VI heists", "Grand Theft Auto VI money", "Leonida"]
    out, vistos = [], set()
    for t in termos:
        r = requests.get(api, headers={"User-Agent": UA}, timeout=30, params={
            "action": "query", "list": "search", "srsearch": t, "srlimit": 15, "format": "json"})
        if not r.ok:
            continue
        for s in r.json().get("query", {}).get("search", []):
            if s["pageid"] in vistos:
                continue
            vistos.add(s["pageid"])
            p = requests.get(api, headers={"User-Agent": UA}, timeout=30, params={
                "action": "parse", "pageid": s["pageid"], "prop": "text", "format": "json"})
            if not p.ok:
                continue
            raw = p.json().get("parse", {}).get("text", {}).get("*", "")
            texto = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[\s\S]*?</\1>", "", raw))))
            if "VI" not in texto and "Leonida" not in texto:
                continue
            titulo = s["title"]
            out.append({"id": f"wk:{s['pageid']}:{s.get('timestamp', '')}", "tipo": "Wiki da comunidade",
                        "url": "https://gta.fandom.com/wiki/" + titulo.replace(" ", "_"), "autor": "GTA Wiki (CC BY-SA)",
                        "titulo": titulo, "texto": texto[:12000], "imagens": []})
            time.sleep(0.5)
    log(f"Wiki: {len(out)} páginas")
    return out


# ---------------- Extração ----------------

PROMPT = """Você é o editor do "Atlas de Leonida", um guia em português do Brasil para GTA 6 (estado de Leonida: {regioes}).
Leia a fonte abaixo (e as imagens, se houver) e extraia TODAS as entradas úteis: cada carro, arma, colecionável, golpe/roubo ou rota de dinheiro com localização ou método vira uma ficha separada. Se a fonte não tiver nada disso (notícia, opinião, meme, especulação de lançamento), responda {{"fichas":[]}}.

Regras:
- Use só o que a fonte diz ou mostra. Nunca invente local, nome, valor ou passo. Se algo não estiver na fonte, deixe vazio.
- Português do Brasil claro, tom de guia ("Vá até…", "Procure…"). Reescreva, não copie frases longas.
- Fonte vaga, sensacionalista ou sem prova visual: "confianca":"baixa" e explique em "alerta".
- Conteúdo que revela trama de missão: "spoiler": true.

Responda SOMENTE com JSON:
{{"fichas":[{{"titulo":"","categoria":"col|car|gun|heist|farm","regiao":"uma das regiões ou vazio","resumo":"1-2 frases","resumo_original":"frase curta no idioma da fonte","passos":["…"],"locais":[{{"nome":"","onde":"","detalhe":""}}],"dificuldade":1,"tempo":"","ganho":0,"spoiler":false,"confianca":"alta|media|baixa","alerta":""}}]}}

Tipo: {tipo}
Autor: {autor}
Título: {titulo}
Texto:
\"\"\"{texto}\"\"\""""


def baixar_imagem(url):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": UA})
        if not r.ok:
            return None
        mime = r.headers.get("Content-Type", "").split(";")[0].strip()
        if not mime or not mime.startswith("image/"):
            mime = mimetypes.guess_type(url)[0] or "image/jpeg"
        return genai_types.Part.from_bytes(data=r.content, mime_type=mime)
    except Exception as e:
        log("  falha ao baixar imagem:", str(e)[:120])
        return None


def gerar(client, parts):
    """Chama o Gemini tentando a lista de modelos candidatos ate um funcionar."""
    candidatos = [_modelo_ok["nome"]] if _modelo_ok["nome"] else MODEL_CANDIDATOS
    ultimo_erro = None
    for nome in candidatos:
        try:
            resp = client.models.generate_content(
                model=nome, contents=parts,
                config=genai_types.GenerateContentConfig(max_output_tokens=4000))
            if _modelo_ok["nome"] != nome:
                log(f"  usando modelo: {nome}")
                _modelo_ok["nome"] = nome
            return resp
        except Exception as e:
            msg = str(e)
            if "NOT_FOUND" in msg or "is no longer available" in msg or " 404" in msg:
                log(f"  modelo {nome} indisponivel, tentando proximo")
                ultimo_erro = e
                continue
            raise
    raise ultimo_erro or RuntimeError("nenhum modelo Gemini disponivel")


def extrair(client, src):
    texto = PROMPT.format(regioes=", ".join(REGIOES), tipo=src["tipo"], autor=src["autor"],
                          titulo=src["titulo"], texto=src["texto"][:12000])
    for usar_imgs in (True, False):
        parts = []
        if usar_imgs:
            for u in src["imagens"][:3]:
                p = baixar_imagem(u)
                if p:
                    parts.append(p)
        parts.append(texto)
        try:
            resp = gerar(client, parts)
            out = (resp.text or "").strip()
            out = re.sub(r"^```(json)?|```$", "", out, flags=re.M).strip()
            return json.loads(out[out.find("{"):out.rfind("}") + 1]).get("fichas", [])
        except (json.JSONDecodeError, ValueError):
            log("  resposta não era JSON válido")
            return []
        except Exception as e:
            if usar_imgs and src["imagens"]:
                log("  imagem recusada, tentando só texto:", str(e)[:120])
                continue
            log("  erro:", str(e)[:200])
            return []
    return []


# ---------------- Mescla e confirmação ----------------

def status_de(f):
    tipos = {x["tipo"] for x in f["fontes"]}
    if f["spoiler"] or (f["confianca"] == "baixa" and len(f["fontes"]) < 2):
        return "retido"
    if len(tipos) >= 2 or len(f["fontes"]) >= 3 or f["confianca"] == "alta":
        return "confirmado"
    return "aguardando"


def achar(fichas, nova):
    k = slug(nova["categoria"] + " " + nova["titulo"])
    for f in fichas:
        if f["id"] == k:
            return f
    for f in fichas:
        if f["categoria"] == nova["categoria"] and (f["regiao"] == nova["regiao"] or not nova["regiao"]):
            if difflib.SequenceMatcher(None, slug(f["titulo"]), slug(nova["titulo"])).ratio() > 0.86:
                return f
    return None


def mesclar(fichas, bruta, src):
    cat = bruta.get("categoria") if bruta.get("categoria") in CATS else None
    titulo = str(bruta.get("titulo") or "").strip()[:140]
    if not cat or not titulo:
        return
    nova = {"titulo": titulo, "categoria": cat,
            "regiao": bruta.get("regiao") if bruta.get("regiao") in REGIOES else "",
            "resumo": str(bruta.get("resumo") or ""), "resumo_original": str(bruta.get("resumo_original") or ""),
            "passos": [str(p) for p in (bruta.get("passos") or [])][:15],
            "locais": [{k: str(l.get(k) or "") for k in ("nome", "onde", "detalhe")} for l in (bruta.get("locais") or [])][:10],
            "dificuldade": bruta.get("dificuldade") if bruta.get("dificuldade") in (1, 2, 3) else 1,
            "tempo": str(bruta.get("tempo") or ""), "ganho": int(bruta.get("ganho") or 0) if str(bruta.get("ganho") or "0").isdigit() else 0,
            "spoiler": bool(bruta.get("spoiler")),
            "confianca": bruta.get("confianca") if bruta.get("confianca") in ("alta", "media", "baixa") else "media",
            "alerta": str(bruta.get("alerta") or "")}
    fonte = {k: src.get(k) for k in ("tipo", "url", "autor", "titulo", "video_id")}
    fonte["imagem"] = (src.get("imagens") or [None])[0]
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    f = achar(fichas, nova)
    if f:
        if fonte["url"] not in {x["url"] for x in f["fontes"]}:
            f["fontes"].append(fonte)
        if len(nova["passos"]) > len(f["passos"]):
            f.update({k: nova[k] for k in ("resumo", "resumo_original", "passos", "locais", "tempo")})
        if not f["regiao"]:
            f["regiao"] = nova["regiao"]
        if nova["confianca"] == "alta":
            f["confianca"] = "alta"
        f["atualizado"] = agora
    else:
        f = {"id": slug(cat + " " + titulo), **nova, "fontes": [fonte], "criado": agora, "atualizado": agora}
        fichas.append(f)
    f["status"] = status_de(f)


def main():
    if not os.getenv("GEMINI_API_KEY"):
        sys.exit("Falta GEMINI_API_KEY")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    fichas = load(DATA, [])
    state = load(STATE, {"vistos": []})
    vistos = set(state["vistos"])
    fontes = []
    for fn in (fonte_youtube, fonte_reddit, fonte_wiki):
        try:
            fontes += fn()
        except Exception as e:
            log(f"{fn.__name__} falhou: {e}")
    novas = [s for s in fontes if s["id"] not in vistos][:MAX_NOVOS]
    log(f"{len(novas)} fontes novas para processar")
    antes = len(fichas)
    for s in novas:
        log("->", s["tipo"], s["titulo"][:80])
        for b in extrair(client, s):
            mesclar(fichas, b, s)
        vistos.add(s["id"])
    fichas.sort(key=lambda f: f["atualizado"], reverse=True)
    DATA.write_text(json.dumps(fichas, ensure_ascii=False, indent=1), encoding="utf-8")
    STATE.write_text(json.dumps({"vistos": sorted(vistos)[-20000:]}, ensure_ascii=False), encoding="utf-8")
    pub = sum(1 for f in fichas if f["status"] != "retido")
    log(f"Pronto: {len(fichas) - antes} fichas novas, {len(fichas)} no total, {pub} publicadas")


if __name__ == "__main__":
    main()
