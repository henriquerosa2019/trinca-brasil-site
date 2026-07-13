# -*- coding: utf-8 -*-
"""
Função serverless (Vercel) — ciclo completo do certificado.
Rota: POST /api/processar   (chamada pelo Database Webhook do Supabase)
      GET  /api/processar   (teste de saúde)

Faz: trava status=processando -> gera PDF -> sobe no Supabase Storage
     -> envia e-mail (Resend, via HTTP) -> grava status=enviado / erro.

Variáveis de ambiente (Vercel -> Settings -> Environment Variables):
  SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_BUCKET(=certificados),
  BREVO_API_KEY, EMAIL_FROM (ex.: henrique.linux@gmail.com),
  EMAIL_FROM_NAME (ex.: "Trinca Brasil")
"""

import base64
import datetime
import io
import json
import os
import re
from http.server import BaseHTTPRequestHandler

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------- render (mesma calibração do gerador testado) ----------------
AQUI   = os.path.dirname(__file__)
MODELO = os.path.join(AQUI, "modelo_base.png")
FONTE  = os.path.join(AQUI, "EBGaramond.ttf")

CREAM = (246, 247, 250)
GOLD  = (194, 152, 98)
CENTRO_X, CENTRO_Y, PITCH, LARGURA = 823, 596, 43, 1040
TAM_BASE, TAM_MIN = 38, 28


def _font(tam):
    f = ImageFont.truetype(FONTE, tam)
    try:
        f.set_variation_by_name("Regular")
    except Exception:
        pass
    return f


def _tokens(d):
    partes = [
        ("Certificamos que ", CREAM), (d["Nome"], GOLD), (", inscrito(a) no ", CREAM),
        ("CPF nº " + d["CPF"], GOLD), (", participou com êxito da Oficina de Choro, ", CREAM),
        ("carga horária " + d["Carga"], GOLD), (", realizada em ", CREAM), (d["Endereco"], GOLD),
        (", no ano de ", CREAM), (str(d["Ano"]), GOLD), (".", CREAM),
    ]
    palavras = []
    for t, c in partes:
        for i, w in enumerate(t.split(" ")):
            if w == "" and i > 0:
                continue
            palavras.append([w, c])
    return palavras


def _wrap(draw, palavras, fonte, larg):
    linhas, cur, lw = [], [], 0
    for w, c in palavras:
        ww = draw.textlength(w + " ", font=fonte)
        if lw + ww > larg and cur:
            linhas.append(cur); cur = []; lw = 0
        cur.append((w, c)); lw += ww
    if cur:
        linhas.append(cur)
    return linhas


def _desenha(im, d):
    draw = ImageDraw.Draw(im)
    pal = _tokens(d)
    tam = TAM_BASE
    while tam >= TAM_MIN:
        f = _font(tam)
        linhas = _wrap(draw, pal, f, LARGURA)
        if len(linhas) <= 3:
            break
        tam -= 1
    f = _font(tam)
    linhas = _wrap(draw, pal, f, LARGURA)
    n = len(linhas)
    topo = CENTRO_Y - ((n - 1) / 2) * PITCH
    for li, linha in enumerate(linhas):
        larg = sum(draw.textlength(w + " ", font=f) for w, _ in linha)
        x = CENTRO_X - larg / 2
        y = topo + li * PITCH
        for w, c in linha:
            draw.text((x, y), w + " ", font=f, fill=c, anchor="lm")
            x += draw.textlength(w + " ", font=f)
    return im


def gera_pdf_bytes(d):
    im = Image.open(MODELO).convert("RGB")
    _desenha(im, d)
    buf = io.BytesIO()
    im.save(buf, "PDF", resolution=150)
    return buf.getvalue()


def limpa_nome(s):
    s = re.sub(r"[^\w\s.-]", "", str(s), flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", s) or "sem-nome"


def _ano(data):
    m = re.search(r"(\d{4})", data or "")
    return m.group(1) if m else ""


# ---------------- Supabase ----------------
SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = os.environ.get("SUPABASE_BUCKET", "certificados")
TABELA = "solicitacoes_certificado"


def _h(extra=None):
    h = {"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY}
    if extra:
        h.update(extra)
    return h


def sb_lock(rid, tentativas):
    url = f"{SB_URL}/rest/v1/{TABELA}?id=eq.{rid}&status=in.(novo,erro)"
    r = requests.patch(url, headers=_h({"Content-Type": "application/json", "Prefer": "return=representation"}),
                       json={"status": "processando", "tentativas": tentativas}, timeout=15)
    r.raise_for_status()
    return r.json()


def sb_patch(rid, campos):
    url = f"{SB_URL}/rest/v1/{TABELA}?id=eq.{rid}"
    r = requests.patch(url, headers=_h({"Content-Type": "application/json", "Prefer": "return=minimal"}),
                       json=campos, timeout=15)
    r.raise_for_status()


def sb_ensure_bucket():
    try:
        requests.post(f"{SB_URL}/storage/v1/bucket", headers=_h({"Content-Type": "application/json"}),
                      json={"id": BUCKET, "name": BUCKET, "public": True}, timeout=15)
    except Exception:
        pass


def sb_upload(path, data):
    url = f"{SB_URL}/storage/v1/object/{BUCKET}/{path}"
    r = requests.post(url, headers=_h({"Content-Type": "application/pdf", "x-upsert": "true"}), data=data, timeout=45)
    r.raise_for_status()
    return f"{SB_URL}/storage/v1/object/public/{BUCKET}/{path}"


# ---------------- e-mail via Brevo (HTTP) ----------------
EMAIL_ASSUNTO = "Seu certificado — Oficina de Choro | Trinca Brasil"


def corpo_email(nome):
    return (
        f"Olá, {nome}!\n\n"
        "Segue em anexo o seu certificado de participação da Oficina de Choro. "
        "Obrigado por ter participado e por manter viva a história do choro com a gente.\n\n"
        "Já ficamos no aguardo da sua presença numa próxima oficina.\n\n"
        "Um abraço,\nTrinca Brasil\n"
        "Toninho Carrasqueira · Edmilson Capelupi · Guilherme Sparrapan\n\n"
        "Instagram @trincabrasil · WhatsApp +55 (11) 95425-5066"
    )


def enviar_email(destino, nome, pdf_bytes, nome_arq):
    key = os.environ.get("BREVO_API_KEY")
    from_email = os.environ.get("EMAIL_FROM", "henrique.linux@gmail.com")
    from_nome = os.environ.get("EMAIL_FROM_NAME", "Trinca Brasil")
    if not key:
        raise RuntimeError("BREVO_API_KEY não configurada")
    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": key, "Content-Type": "application/json", "accept": "application/json"},
        json={
            "sender": {"name": from_nome, "email": from_email},
            "to": [{"email": destino}],
            "subject": EMAIL_ASSUNTO,
            "textContent": corpo_email(nome),
            "attachment": [{"name": nome_arq, "content": base64.b64encode(pdf_bytes).decode()}],
        },
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Brevo {r.status_code}: {r.text[:300]}")


# ---------------- ciclo completo ----------------
def processar(body):
    rec = body.get("record") if isinstance(body, dict) and "record" in body else body
    if not isinstance(rec, dict) or not rec.get("id"):
        return {"erro": "payload sem 'record.id'"}, 400
    if not (SB_URL and SB_KEY):
        return {"erro": "SUPABASE_URL/SUPABASE_SERVICE_KEY não configurados"}, 500

    rid = rec["id"]
    tentativas = int(rec.get("tentativas") or 0) + 1
    try:
        if not sb_lock(rid, tentativas):
            return {"ok": True, "status": "ignorado", "motivo": "já processado/em processamento"}, 200

        dados = {
            "Nome": rec.get("nome", ""), "CPF": rec.get("cpf", "") or "",
            "Carga": rec.get("carga_horaria") or "8 horas", "Endereco": rec.get("endereco", "") or "",
            "Ano": rec.get("ano") or _ano(rec.get("data_oficina", "")),
        }
        pdf = gera_pdf_bytes(dados)

        sb_ensure_bucket()
        ano = dados["Ano"] or datetime.date.today().strftime("%Y")
        path = f"{ano}/{rid}.pdf"
        pdf_url = sb_upload(path, pdf)

        # marca ENVIADO já aqui (o e-mail, que é a parte demorada, vem depois)
        sb_patch(rid, {
            "status": "enviado", "pdf_url": pdf_url, "storage_path": path,
            "enviado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(), "erro": None,
        })

        # envia o e-mail; se falhar, mantém 'enviado' e anota o aviso em 'erro'
        nome_arq = "Certificado - " + limpa_nome(rec.get("nome", "")) + ".pdf"
        try:
            enviar_email(rec.get("email"), rec.get("nome", ""), pdf, nome_arq)
        except Exception as e_mail:
            try:
                sb_patch(rid, {"erro": "aviso e-mail: " + str(e_mail)[:400]})
            except Exception:
                pass

        return {"ok": True, "status": "enviado", "pdf_url": pdf_url}, 200
    except Exception as e:
        try:
            sb_patch(rid, {"status": "erro", "erro": str(e)[:500]})
        except Exception:
            pass
        return {"ok": False, "erro": str(e)}, 500


# ---------------- handler Vercel ----------------
class handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        self._json({"status": "ok", "servico": "certificados-trinca-brasil"})

    def do_POST(self):
        try:
            n = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"erro": "JSON inválido"}, 400)
        resultado, status = processar(body)
        self._json(resultado, status)
