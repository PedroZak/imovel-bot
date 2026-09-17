# imovel-bot 🏠

Robô que varre listagens imobiliárias (OLX, ZAP/VivaReal, QuintoAndar —
mercado; Caixa e Resale — leilão) em bairros específicos de São Paulo
capital, Campinas e Piracicaba, pontua cada imóvel de 0–100 a partir de
preço/m² real (ITBI), yield de aluguel estimado, condomínio, metragem
e distância a pé do escritório, e notifica os aprovados via Telegram.
Também gera um dashboard HTML (mobile-friendly, com modo escuro e uma
calculadora de flip compra-reforma-venda) a cada rodada.

**Objetivo:** achar distorções de mercado — imóveis genuinamente abaixo
do valor real, não só "baratos" no sentido de anúncio.

## Estrutura

```
imovel-bot/
├── config.yaml                  ← filtros, regiões, referências de preço (curado à mão)
├── referencias_calibradas.yaml  ← gerado por calibragem/ — nunca editar à mão
├── main.py                      ← orquestrador (Tier 1 mercado / Tier 3 leilão)
├── requirements.txt
│
├── scrapers/
│   ├── base.py                  ← dataclasses Listing / LeilaoListing
│   ├── olx.py                   ← OLX (curl_cffi + parser de RSC streaming)
│   ├── zap.py                   ← ZAP e VivaReal (__NEXT_DATA__ + fallback JSON-LD)
│   ├── quintoandar.py           ← QuintoAndar (API interna, engenharia reversa)
│   ├── caixa_leilao.py          ← Leilões Caixa (Tier 3)
│   └── resale.py                ← Leilões multi-banco via Resale (Tier 3)
│
├── scorers/
│   ├── base.py                  ← ScoreResult / LeilaoScoreResult / FlipResult
│   ├── mercado.py               ← Score 0–100 (7 critérios isolados)
│   ├── leilao.py                ← Score 0–100 leilão, fonte-agnóstico
│   └── flip.py                  ← Calculadora de compra-reforma-venda
│
├── notifier/
│   ├── telegram.py              ← Envia para canal Mercado / Leilão
│   ├── dashboard.py             ← Dashboard HTML local (+ botão de controle remoto)
│   └── dedup.py                 ← SQLite: evita notificação repetida
│
├── utils/
│   ├── bairro.py                ← Normalização/resolução de bairro (compartilhado)
│   ├── historico.py             ← Grava preços coletados, calcula mediana móvel
│   ├── geo.py                   ← Haversine + geocoding + cache
│   └── text.py                  ← Parsing de números/texto
│
├── calibragem/
│   ├── atlas.py                 ← Rebusca Atlas (SP capital) mensalmente
│   └── calibrar.py              ← Orquestrador: Atlas + mediana móvel → yaml
│
├── pwa/                         ← manifest/service worker/ícone do dashboard instalável
├── testar_scraper.py            ← testa scrapers + gera dashboard, sem precisar de Telegram
├── testar.bat                   ← menu local (Windows) pra rodar tudo isso sem terminal
│
└── .github/workflows/
    ├── rodar-bot.yml            ← só sob demanda (workflow_dispatch) — publica o dashboard no Pages
    └── calibragem.yml           ← roda mensal, dia 1º
```

## Segurança — leia antes de tudo

`config.yaml` deste repositório **só contém placeholders**
(`SEU_BOT_TOKEN_AQUI`, `-100XXXXXXXXXX`). Nunca substitua esses valores
por dados reais neste arquivo se ele for para o GitHub ou ficar em uma
pasta compartilhada (Drive, etc.) — token e IDs de canal reais vão
**exclusivamente** em:

1. **GitHub Secrets** (produção) — ver seção abaixo
2. **Variáveis de ambiente locais** (desenvolvimento) — nunca em arquivo salvo

## Setup

### 1. Telegram

```
1. @BotFather → /newbot → anotar token
2. Criar 2 canais privados → adicionar o bot como admin em cada
3. Pegar os IDs via https://api.telegram.org/bot<TOKEN>/getUpdates
```

### 2. Rodar local (sem salvar token em arquivo)

PowerShell:
```powershell
$env:TELEGRAM_TOKEN="seu_token"
$env:TELEGRAM_CHANNEL_MERCADO="-100..."
pip install -r requirements.txt
python main.py --tier 1 --dry-run
```

Ou, pra testar só o scraper/score/dashboard sem token nenhum:
```powershell
python testar_scraper.py --regiao all
```

### 3. GitHub Actions + Pages (rodar e ver resultado sem o PC ligado)

No repositório: **Settings → Secrets and variables → Actions**, criar:
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHANNEL_MERCADO`
- `TELEGRAM_CHANNEL_LEILAO`

Em **Settings → Pages**, fonte = "GitHub Actions".

O workflow `rodar-bot.yml` só roda sob demanda — sem agendamento fixo,
pra economizar minutos de Actions. Disparo manual via **Actions → Run
workflow**, escolhendo tier (1 mercado / 3 leilão) e se deve enviar
notificação real pro Telegram (desligado por padrão — sem marcar essa
opção, a rodada só atualiza o dashboard). Cada rodada publica
`data/dashboard.html` automaticamente no GitHub Pages.

**Controle pelo celular:** o próprio dashboard publicado tem um botão
"▶ Rodar agora" que dispara esse mesmo workflow via API do GitHub, sem
precisar abrir o repositório. Usa um token de acesso pessoal
(fine-grained, escopo `Actions: Read and write` só neste repo) que
você mesmo gera e cola uma vez — fica salvo só no `localStorage` do seu
navegador/celular, nunca é enviado a lugar nenhum além da API do
GitHub. O dashboard também é instalável como app (PWA) — "Adicionar à
tela inicial" no navegador do celular.

## Multi-região

`config.yaml` → `mercado.multi_regiao: true` ativa a busca em
São Paulo capital, Campinas e Piracicaba na mesma execução, cada uma
com seu próprio teto de preço, score mínimo e referências de bairro.
Todas caem no mesmo canal Telegram "mercado". VivaReal só é buscado
para SP capital (a URL de busca do VivaReal não tem slug por região).

## Calibração de preços — automática (calibragem/)

O `compra_m2` de cada bairro se mantém fresco sozinho, por dois
caminhos diferentes conforme a região:

```
SP capital        → Atlas (atlasdados.com/sp) via ITBI, roda mensal
Campinas/Piracicaba → mediana móvel dos próprios dados coletados pelo bot
```

**Como funciona:**
1. Toda execução do Tier 1 grava no SQLite (`precos_historico`) TODOS os
   listings coletados — aprovados ou não — para nunca enviesar a mediana
   para baixo.
2. `.github/workflows/calibragem.yml` roda no dia 1º de cada mês:
   rebusca o Atlas para SP capital, calcula a mediana móvel (últimos 60
   dias, mínimo 15 amostras) para Campinas/Piracicaba, e escreve
   `referencias_calibradas.yaml`.
3. `main.py` funde esse arquivo com `config.yaml` na inicialização —
   **nunca edita `config.yaml` diretamente**, preservando toda a
   curadoria manual e os comentários.
4. Divergências ≥15% entre o valor curado e o recalibrado são
   sinalizadas no canal Telegram "mercado" — essas são, literalmente,
   as distorções de mercado que o bot existe para encontrar.

Rodar manualmente:
```bash
python -m calibragem.calibrar
```

Campinas/Piracicaba só são recalibrados depois que o bot já tiver
coletado amostra suficiente (mín. 15 anúncios/bairro em 60 dias) —
antes disso, mantém a estimativa manual do `config.yaml` como está.

## Leilão (Tier 3)

Roda com `python main.py --tier 3`. Duas fontes — Caixa (busca por
código de cidade) e Resale (agrega vários bancos, dado mais rico:
ocupação real, dívidas, contencioso judicial). Filtros hard incluem
ocupação (quando a fonte informa) e ação judicial em andamento — risco
jurídico é exatamente o tipo de coisa que esse tier existe pra evitar.

## Ajustar os scorers

Pesos em `scorers/mercado.py` — cada critério é uma função isolada
(`_criterio_preco`, `_criterio_yield`, etc.). Mudar peso de um não
afeta os outros. `scorers/flip.py` usa os parâmetros de `config.yaml`
→ `reforma:` (custo por cômodo, meses de reforma/venda estimados) —
são estimativas iniciais, ajustar conforme sua experiência real.
