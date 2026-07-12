# Trinca Brasil — Site + API de certificados (Vercel + Supabase)

Um projeto Vercel só, com duas partes:
- `index.html` — o site (servido em `/`). Já grava as solicitações no Supabase.
- `api/processar.py` — função serverless que gera o certificado, sobe no
  Supabase Storage, envia o e-mail (via Resend) e atualiza a linha no banco.

## Estrutura
```
index.html            -> site (estático)
api/processar.py      -> função serverless  (POST /api/processar)
api/modelo_base.png   -> modelo do certificado (em branco)
api/EBGaramond.ttf    -> fonte
requirements.txt      -> Pillow, requests
vercel.json           -> tempo máximo da função
```

## Passo 1 — Subir para o GitHub
Coloque estes arquivos no seu repositório (pode ser o `trinca-brasil-site`,
substituindo o conteúdo). O `index.html` é o mesmo já conectado ao Supabase.

## Passo 2 — Importar na Vercel
1. vercel.com → **Add New… → Project** → importe o repositório do GitHub.
2. **Framework Preset: Other** (é site estático + função Python).
3. **Deploy**. Você recebe uma URL tipo `https://trinca-brasil.vercel.app`.

## Passo 3 — Variáveis de ambiente (Vercel → Settings → Environment Variables)
```
SUPABASE_URL          = https://pyydnicvltkioovtvzfk.supabase.co
SUPABASE_SERVICE_KEY  = <chave service_role do Supabase (SECRETA)>
SUPABASE_BUCKET       = certificados
RESEND_API_KEY        = <chave da sua conta Resend>
EMAIL_FROM            = Trinca Brasil <contato@seudominio.com>
```
Depois de adicionar, faça **Redeploy** para valerem.

- A `service_role` fica em Supabase → Settings → API. Ela ignora o RLS para
  poder atualizar as linhas e subir arquivos. Vai **só aqui**, nunca no site.
- **Por que Resend (e não SMTP)?** A Vercel bloqueia SMTP; por isso o e-mail
  sai por uma API HTTP. O Resend é grátis para começar.

## Passo 4 — Conta no Resend
1. resend.com → crie a conta → **API Keys** → gere uma chave (vai em `RESEND_API_KEY`).
2. Para enviar aos participantes (qualquer e-mail), verifique um **domínio**
   em Resend → Domains (adiciona uns registros DNS) e use um `EMAIL_FROM` desse
   domínio (ex.: `contato@trincabrasil.com.br`).
   - Para só testar: sem domínio, o Resend deixa enviar de `onboarding@resend.dev`
     **para o e-mail da sua própria conta Resend**.

## Passo 5 — Bucket de Storage
A função cria o bucket `certificados` (público) sozinha. Se preferir manual:
Supabase → Storage → New bucket → `certificados` → marque **Public**.

## Passo 6 — Database Webhook no Supabase
Supabase → **Database → Webhooks → Create a new hook**:
- Tabela: `solicitacoes_certificado`  •  Evento: **Insert**
- Tipo: **HTTP Request**, método **POST**
- URL: `https://SEU-APP.vercel.app/api/processar`
- Header: `Content-Type: application/json`

## Passo 7 — Testar
1. Teste a função sozinha (deve responder ok):
   `https://SEU-APP.vercel.app/api/processar`  (abrir no navegador = GET)
2. Preencha uma solicitação no site. Em segundos, a linha no Supabase deve virar
   `status = enviado`, com `pdf_url` preenchido, e o e-mail chega com o PDF.
3. Se der `status = erro`, a coluna `erro` mostra o motivo (ex.: Resend/domínio,
   service_role incorreta, bucket).

Obs.: com essa arquitetura, **o n8n não é mais necessário** — o Supabase chama a
função da Vercel diretamente.
