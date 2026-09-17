# imovel-bot 🏠

Robô que varre listagens imobiliárias (OLX, ZAP/VivaReal, QuintoAndar —
mercado; Caixa e Resale — leilão) em bairros específicos de São Paulo
capital, Campinas e Piracicaba e pontua cada imóvel de 0–100 a partir
de preço/m² real (ITBI), yield de aluguel estimado, condomínio,
metragem e distância a pé do escritório. Dashboard-only: nunca envia
nada por conta própria — cada rodada gera/atualiza um dashboard HTML
(mobile-friendly, com modo escuro e uma calculadora de flip
compra-reforma-venda), disponível localmente e, opcionalmente, via
GitHub Pages.

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
│   └── dashboard.py             ← Dashboard HTML local (+ botão de controle remoto)
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
├── testar_scraper.py            ← testa scrapers + gera dashboard
├── testar.bat                   ← menu local (Windows) pra rodar tudo isso sem terminal
│
└── .github/workflows/
    ├── rodar-bot.yml            ← só sob demanda (workflow_dispatch) — publica o dashboard no Pages
    └── calibragem.yml           ← roda mensal, dia 1º
```

## Setup

### Rodar local

PowerShell:
```powershell
pip install -r requirements.txt
python main.py --tier 1
```

Ou, pra testar só o scraper/score/dashboard região por região:
```powershell
python testar_scraper.py --regiao all
```

Ou, no Windows, sem terminal nenhum: `testar.bat` abre um menu.

### GitHub Actions + Pages (rodar e ver resultado sem o PC ligado)

Em **Settings → Pages**, fonte = "GitHub Actions" (já habilitado, se
você seguiu o setup inicial deste repositório).

O workflow `rodar-bot.yml` só roda sob demanda — sem agendamento fixo,
pra economizar minutos de Actions. Disparo manual via **Actions → Run
workflow**, escolhendo o tier (1 mercado / 3 leilão). Cada rodada
publica `data/dashboard.html` automaticamente no GitHub Pages.

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
com seu próprio teto de preço, score mínimo e referências de bairro —
cada região vira sua própria aba no dashboard. VivaReal só é buscado
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
   sinalizadas no log da calibragem — essas são, literalmente, as
   distorções de mercado que o bot existe para encontrar.

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
