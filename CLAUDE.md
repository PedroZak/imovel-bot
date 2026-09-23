# imovel-bot — Handoff para Claude Code

Este arquivo dá contexto de continuidade. Leia antes de mexer no código.

## O que é

Bot que varre OLX, ZAP/VivaReal e QuintoAndar (mercado) + Caixa e Resale
(leilão) procurando apartamentos à venda em bairros específicos de São
Paulo (capital), Campinas e Piracicaba, pontua cada um de 0–100 com
base em preço/m² real (ITBI), yield de aluguel estimado, condomínio,
metragem e distância a pé do escritório do usuário. **Dashboard-only —
sem Telegram** (removido por completo em 16/09/2026, pedido explícito
do usuário — ver "Bugs corrigidos" #17): cada rodada só gera/atualiza
`data/dashboard.html`, nunca envia nada por conta própria.

**Objetivo declarado do usuário: achar distorções de mercado** —
imóveis genuinamente abaixo do valor real, não só "baratos" no sentido
de anúncio. Isso molda várias decisões de design (ex: usar preço de
fechamento ITBI em vez de preço de anúncio como referência).

Usuário: Pedro, trabalha na Goldman Sachs (Itaim Bibi), plano é morar
2–4 anos e depois alugar. Budget ~R$500k, financiamento hipotético
30% entrada / ~11% a.a. / 360 meses ("Cenário C").

## Onde tudo mora

Projeto vive em `G:\Meu Drive\06. Dev & Automação\imovel-bot`
(sincronizado via Google Drive Desktop no Windows do usuário). Ambiente
virtual Python foi movido pra **fora** dessa pasta de propósito
(`$env:USERPROFILE\venvs\imovel-bot`), pra não sobrecarregar a
sincronização do Drive com milhares de arquivos de pacote.

**GitHub existe desde 16/09/2026**: [github.com/PedroZak/imovel-bot](https://github.com/PedroZak/imovel-bot)
(público — decisão explícita do usuário, necessário pro GitHub Pages
gratuito). Dashboard publicado em https://pedrozak.github.io/imovel-bot/
a cada rodada do workflow `.github/workflows/rodar-bot.yml`, que só
roda sob demanda (sem cron — decisão explícita do usuário, economia de
minutos de Actions). Sem Telegram, não há secret nenhum a configurar —
o único jeito de disparar uma rodada é manual (**Actions → Run
workflow**) ou pelo botão "▶ Rodar agora" dentro do próprio dashboard
(ver "Bugs corrigidos" #16), que chama a API do GitHub com um PAT
fine-grained que o usuário mesmo gera e guarda no `localStorage` do
navegador — nunca passa pelo assistente nem fica embutido no código.

## Arquitetura

```
main.py                    orquestrador. Tier 1 (mercado) é o padrão.
                            Tier 3 (leilão) só roda com --tier 3 explícito.
                            Sem Telegram/dedup — dashboard-only, ver
                            "Bugs corrigidos" #17.
config.yaml                curado à mão, comentado. NUNCA sobrescrever
                            programaticamente. Não tem mais bloco
                            telegram: (removido).
referencias_calibradas.yaml gerado por calibragem/, mesclado em main.py
                            na carga do config (nunca edita config.yaml)

scrapers/
  base.py                  dataclasses Listing, LeilaoListing
  olx.py                   usa curl_cffi (impersonate=chrome120) — requests
                            puro toma 403. Parser em 2 camadas: __NEXT_DATA__
                            (formato antigo, mantido como fallback) → RSC
                            streaming (self.__next_f.push, formato atual —
                            OLX migrou pra Next.js App Router, ago/2026).
                            Ver "Bugs corrigidos" #7.
  zap.py                   3 camadas: __NEXT_DATA__ (paths conhecidos) →
                            __NEXT_DATA__ (busca recursiva validada) →
                            JSON-LD (fallback). Hoje __NEXT_DATA__ não
                            aparece no site ao vivo, mas o fallback JSON-LD
                            funciona e traz ~430-440 anúncios por rodada
                            de sp_capital. Suporta portal="VIVA_REAL" — só
                            é chamado pelo main.py quando a região é SP
                            capital (SEARCH_URL do VivaReal é fixo em SP,
                            sem slug por região).
  quintoandar.py           Adicionado 15/09/2026 — API interna
                            (`apigw.prod.quintoandar.com.br`), achada via
                            captura HAR (site é SPA puro, sem SSR nem
                            JSON-LD). Sem autenticação. Descarta listings
                            `isPrimaryMarket` (lançamento, preço em faixa,
                            não pontual) e `forSale=false`. `condominio`
                            é `iptuPlusCondominium` combinado (limitação
                            conhecida — API não separa os dois). Testado
                            ao vivo nas 3 regiões (slug/coordenada/viewport
                            calibrados em config.yaml → mercado.regions.*.
                            quintoandar).
  caixa_leilao.py          Tier 3. Reescrito 04/09/2026 — fluxo de 3
                            chamadas AJAX + 1 GET de detalhe por imóvel
                            (site mudou de layout por completo). Ver
                            "Bugs corrigidos" #9.
  resale.py                Tier 3. Adicionado 05/09/2026 — agrega
                            imóveis de vários bancos (não só
                            Caixa) via API JSON própria (achada por
                            engenharia reversa do bundle JS do site,
                            não documentada). É a única fonte com
                            ocupação (Ocupado/Desocupado) REAL — volta
                            a ser filtro hard só pra essa fonte, ver
                            scorers/leilao.py. Ver "Bugs corrigidos" #10
                            pro mapeamento completo e as limitações
                            (sem filtro de cidade via API, WAF sensível
                            a rajada).

scorers/
  mercado.py               7 critérios isolados, cada um uma função.
                            Ver docstring do arquivo pros pesos.
  leilao.py                Fonte-agnóstico — roda em cima de Caixa e
                            Resale igual.
  flip.py                  Calculadora compra-reforma-venda (FlipResult
                            em scorers/base.py) — custo de reforma por
                            cômodo, custo de manutenção durante reforma+
                            venda (condomínio+IPTU), margem estimada.
                            Parâmetros em config.yaml → reforma: (todos
                            marcados "ESTIMATIVA INICIAL", pra o usuário
                            calibrar com experiência real).

notifier/
  dashboard.py             HTML local (data/dashboard.html), 2 abas de
                            nível superior — 🏠 Mercado / 🏦 Leilão
                            (adicionado 11/09/2026) — cada uma com
                            sub-abas (região pro Mercado, fonte
                            Caixa/Resale pro Leilão). Tier 1 e Tier 3
                            rodam em invocações separadas de main.py e
                            cada uma só atualiza sua própria seção via
                            cache em disco (`_dash_cache_mercado.html`/
                            `_dash_cache_leilao.html`) — ver docstring
                            do módulo e "Bugs corrigidos" #11. Desde
                            16/09/2026 também tem botão "▶ Rodar agora" +
                            modal de configuração (PAT do GitHub salvo em
                            localStorage) pra disparar o workflow direto
                            do celular — ver "Bugs corrigidos" #16. Sem
                            módulo telegram.py/dedup.py — removidos por
                            completo, ver "Bugs corrigidos" #17.

utils/
  bairro.py                normalizar/resolver bairro — compartilhado entre
                            scorer e histórico, pra não dessincronizar.
  historico.py             grava TODO listing coletado (aprovado ou não) no
                            SQLite, base da mediana móvel de calibragem.
  geo.py                   haversine + geocoding (Nominatim) + cache,
                            3 níveis de fallback pra walkability.
  text.py                  parsing de número/área/quartos + sanitização de
                            placeholder "R$1" do OLX/ZAP.

calibragem/
  atlas.py                 rebusca atlasdados.com/sp (ITBI) pra SP capital.
                            TESTADO AO VIVO (30/08/2026): 5/6 bairros
                            configurados retornaram valor de primeira, regex
                            bateu sem ajuste. "Paraíso" dá 404 no Atlas —
                            o site não trata como bairro autônomo (já
                            marcado "interpolado" no config.yaml); não é
                            bug, é gap de cobertura da fonte externa.
  calibrar.py               orquestra Atlas (SP) + mediana móvel
                            (Campinas/Piracicaba) → referencias_calibradas.yaml.
                            TESTADO AO VIVO — roda sem erro fim a fim.
                            Resumo (bairros atualizados + divergências)
                            só vai pro log agora, não manda mais Telegram
                            (ver "Bugs corrigidos" #17).

testar_scraper.py          script standalone, testa OLX/ZAP/QuintoAndar e
                            gera dashboard. Mostra também quantos
                            resultados batem com os bairros-alvo (vs total
                            bruto da região). Não grava histórico de preços
                            (main.py grava, esse não — usado só pra teste
                            manual).

pwa/                        manifest.webmanifest + sw.js + icon.svg —
                            assets do dashboard instalável (PWA), copiados
                            pro site publicado no Pages junto com
                            data/dashboard.html. Ver "Bugs corrigidos" #16.

.github/workflows/
  rodar-bot.yml             SÓ workflow_dispatch, sem cron (renomeado de
                            scheduler.yml em 16/09/2026 — ver "Bugs
                            corrigidos" #16). Roda main.py --tier N e
                            publica data/dashboard.html + pwa/ no GitHub
                            Pages via actions/deploy-pages.
  calibragem.yml             roda calibragem mensal (dia 1º), commita
                            referencias_calibradas.yaml de volta
```

## Estado atual (o que funciona, o que não)

Última validação ao vivo: 16/09/2026.

| Fonte | Status | Detalhe |
|---|---|---|
| ZAP | ✅ Funciona | Via fallback JSON-LD. `__NEXT_DATA__` sumiu do site ao vivo, causa desconhecida, mas não importa — o fallback cobre. ~430-440 anúncios/rodada em sp_capital. |
| OLX | ✅ Funciona | Site migrou pra Next.js App Router (RSC streaming) — `__NEXT_DATA__` sumiu de vez, parser reescrito pra ler o novo formato. 247 anúncios coletados em sp_capital no teste ao vivo. Ver "Bugs corrigidos" #7. |
| VivaReal | ✅ Ativado (só SP capital) | `main.py:_viva_real_aplicavel()` só chama pra sp_capital — `scrapers/zap.py`'s SEARCH_URL do VivaReal é fixo em SP, sem slug por região; chamar em Campinas/Piracicaba traria resultado errado. 438 anúncios coletados no teste ao vivo. |
| QuintoAndar | ✅ Testado ao vivo (15/09/2026) | API interna via engenharia reversa (captura HAR) — site é SPA puro, sem SSR. 97 listings em sp_capital no teste isolado; testado também combinado com OLX+ZAP (`--fonte todos`). |
| Calibragem (Atlas + mediana móvel) | ✅ Testada ao vivo | 5/6 bairros de sp_capital retornaram valor do Atlas de primeira. `referencias_calibradas.yaml` gerado e validado. |
| Caixa (Tier 3, leilão) | ✅ Reescrito e testado ao vivo (04/09/2026), agora cobre 3 cidades (11/09/2026) | Site mudou de layout por completo desde a versão anterior. Reescrito do zero pro fluxo real (3 chamadas AJAX + 1 GET de detalhe por imóvel). Pipeline completo (scraper→scorer→dashboard) sem erro. Ver "Bugs corrigidos" #9 — inclui uma mudança de design, não só bug: filtro hard de ocupação foi removido (dado sumiu do site). Desde 11/09 busca São Paulo (395 imóveis no teste), Campinas (42) e Piracicaba (37), não só SP capital — ver "Bugs corrigidos" #12. |
| Resale (Tier 3, leilão) | ✅ Testado ao vivo fim a fim (11/09/2026) | Segunda fonte de leilão, via API JSON própria (não documentada, achada por engenharia reversa). Agrega vários bancos, não só Caixa. Dado mais rico que a Caixa: ocupação real, dívidas e contencioso judicial. Pipeline completo (scraper→scorer→dashboard) validado: 7 imóveis achados em SP/Campinas, 2 aprovados com filtro afrouxado. Ver "Bugs corrigidos" #10 pras limitações conhecidas (sem filtro de cidade via API, paginação real é `page` não `offset`, `order` quebra o backend deles, WAF sensível). |
| Dashboard unificado (Mercado + Leilão) | ✅ Testado ao vivo (11/09/2026) | `notifier/dashboard.py` agora tem 2 abas de nível superior. Testado combinando uma rodada real de Tier 1 (OLX, sp_capital) com uma rodada real de Tier 3 (Resale) em invocações separadas — confirmado visualmente que as duas seções coexistem sem se apagar. Ver "Bugs corrigidos" #11. |
| GitHub Actions + Pages + PWA | ✅ No ar (16/09/2026) | Repo público, Pages via Actions habilitado. `rodar-bot.yml` só roda sob demanda (sem cron), publica dashboard em https://pedrozak.github.io/imovel-bot/. Botão "Rodar agora" no dashboard dispara o workflow via API do GitHub usando PAT do próprio usuário salvo em localStorage. Ver "Bugs corrigidos" #16. |
| Telegram / dedup | ❌ Removido por completo (16/09/2026) | Pedido explícito do usuário — bot é dashboard-only agora. Ver "Bugs corrigidos" #17. |

## Bugs reais já encontrados e corrigidos (não redescobrir)

Todos encontrados testando com dados **reais** coletados pelo usuário,
todos com teste unitário confirmando o fix antes de subir. Em ordem
cronológica:

1. **Placeholder "R$1" do OLX/ZAP** — o site mostra "R$ 1" quando
   condomínio/IPTU não foi preenchido pelo anunciante. Sem tratamento,
   o scorer pontuava isso como "condomínio excelente" (+20) em vez de
   neutro. Fix: `utils/text.py:sanitizar_placeholder()`, valores ≤1
   viram `None`.

2. **ZAP sem filtro de aluguel disfarçado** — `olx.py` já tinha, `zap.py`
   não. Fix: checa `pricingInfos.businessType == "RENTAL"` **e**
   palavras-chave no texto, nas duas camadas de parsing do ZAP.

3. **Bairro errado no fallback JSON-LD do ZAP** — `address.addressLocality`
   do schema.org retorna a CIDADE, não o bairro (todo listing saía com
   bairro="São Paulo"/"Campinas", reprovado por "fora do radar" mesmo
   tendo dado bom). Fix: extrai bairro/cidade do **título** via regex
   (`"...em <Bairro>, <Cidade>"`), que é consistente nos anúncios reais.
   Área e quartos também estavam hardcoded como `None` nesse parser —
   corrigido pra usar `extrair_area()`/`extrair_quartos()` (que já
   existiam, só não eram chamados ali).

4. **Faixa de metragem pegava o limite errado** — títulos de lançamento
   tipo "46 - 101 m²" pegavam o 101 (maior), mas o preço mostrado é do
   menor. Isso gerava preço/m² artificialmente baixo — uma distorção
   FALSA, o oposto do que o bot deveria fazer. Fix: `extrair_area()`
   detecta faixa e usa sempre o limite menor.

5. **Área ausente virava `1m²` silenciosamente** — `area = listing.area
   or 1` no cálculo de métricas. Fix: filtro hard explícito rejeitando
   listing sem área, com motivo claro no log.

6. **CRÍTICO: preço ausente não era filtrado** — `if listing.preco and
   listing.preco > preco_max` só disparava se `preco` fosse truthy;
   com `preco=None` (acontece de verdade — o ZAP às vezes tem 2 blocos
   JSON-LD pro mesmo anúncio, um com preço e outro sem), o filtro era
   pulado inteiro, e `_calcular_metricas` fazia `preco = listing.preco
   or 0` → preço/m² = 0 → ~100% de desconto → score máximo (35 pts).
   Um anúncio sem preço nenhum podia virar "a melhor oportunidade do
   dia". Fix: filtro hard explícito `if not listing.preco: return
   "Preço não informado"` antes de qualquer comparação.

7. **OLX zerado — site migrou pra RSC streaming (30/08/2026)** — a OLX
   trocou `__NEXT_DATA__` por Next.js App Router; os anúncios agora
   vêm num array `"ads":[...]` embutido dentro de chunks
   `self.__next_f.push([1, "..."])`, que não é JSON válido por conta
   própria (é um objeto React Server Component maior). O JSON-LD que
   sobrou na página é só um resumo agregado (schema.org
   Product/AggregateOffer com `offerCount`), sem dado por anúncio — ao
   contrário do ZAP, não dá pra usar JSON-LD como fallback aqui. Fix:
   `scrapers/olx.py:_extrair_ads_rsc()` acha o chunk certo, localiza
   `"ads":[` e extrai o array via contagem de colchetes balanceada
   (ignorando colchetes dentro de strings), não parseando o chunk
   inteiro como JSON. Campos mudaram de formato: `properties[].value`
   agora vem como texto formatado (`"R$ 470"`, `"46m²"`), não número
   cru — precisa `extrair_numero`/`extrair_area` em vez de conversão
   direta. Bairro/cidade vêm limpos em `locationDetails`, sem precisar
   de regex no título (ao contrário do fix #3 pro ZAP). Alguns itens
   do array `ads` são slots de publicidade nativa sem `listId`/`url` —
   descartados checando esses dois campos.

8. **Dedup de conteúdo não pegava repost dentro do MESMO lote** — a
   primeira versão do fingerprint dedup só gravava em SQLite
   (`marcar_visto_fingerprint`) depois de um envio real bem-sucedido;
   em `--dry-run` isso nunca acontece, e mesmo fora de dry-run,
   3 reposts do mesmo apto dentro do mesmo lote coletado passavam
   batidos porque nenhum deles tinha sido marcado ainda quando os
   outros dois eram checados synchronously — na prática funcionava
   pra reposts entre rodadas diferentes, mas não dentro da mesma
   rodada. Confirmado com dado real: 1 apto no OLX (Piracicaba) com
   3 IDs diferentes (1525746658/1525744474/1525743921), mesmo preço/
   área/bairro, passou 3x no dry-run antes do fix. Fix: `main.py:
   _notificar` agora mantém um `set()` em memória
   (`fingerprints_do_lote`) checado e atualizado incondicionalmente
   (inclusive em dry-run), separado da marcação em SQLite (que
   continua só acontecendo após envio real, pra dedup entre rodadas).

9. **Caixa (Tier 3) inteiro zerado — site mudou de layout por completo
   (04/09/2026)** — `scrapers/caixa_leilao.py` fazia um POST único pra
   `busca-imovel.asp` esperando HTML pronto de volta; o site atual
   devolve sempre a mesma página do formulário vazio (POST não tinha
   efeito nenhum). Reverse-engineered ao vivo o fluxo real, que agora
   é 3 chamadas encadeadas, sem nenhuma documentação pública:
     1. GET `busca-imovel.asp` — abre sessão
     2. POST `carregaPesquisaImoveis.asp` — devolve só os IDs dos
        imóveis, paginados 10 a 10 em inputs hidden `hdnImov1..N`
        (formato: string de IDs separados por `_`)
     3. GET `detalhe-imovel.asp?hdnimovel=<id>` — única página que
        ainda mostra "Valor de avaliação"; a listagem não tem mais
        esse dado (só o valor mínimo de venda), por isso é 1 request
        por imóvel candidato (`leilao.max_detalhes` no config.yaml
        limita isso — sp_capital/apartamento sem filtro de bairro já
        trouxe 564 imóveis num teste ao vivo).
   Códigos internos também mudaram: tipo "Apartamento" agora é `"2"`
   (era `"AP"`); código de cidade (`9859` = São Paulo, `9205` =
   Campinas, `9682` = Piracicaba — mapa completo em
   `scrapers/caixa_leilao.py:_CODIGO_CIDADE_SP`) continua igual.

   **Mudança de design, não só bug — ocupação sumiu do site.** O
   filtro hard "nunca notificar imóvel ocupado" (risco jurídico de
   reintegração de posse) dependia de um campo que a Caixa parou de
   publicar. Confirmado ao vivo: na página de detalhe o campo aparece
   só como comentário morto no HTML
   (`<!--span>Situação: <strong>Ocupado</strong></span><br-->`),
   sempre comentado, em todo imóvel testado — não é condicional por
   imóvel, é ausência de dado no template inteiro. Também baixei um
   edital em PDF real (~460 itens, ~740KB) e extraí o texto inteiro
   procurando por "ocupado": as únicas 3 ocorrências no documento
   inteiro são cláusulas jurídicas genéricas ("caso o imóvel esteja
   ocupado por terceiros"), não status por item — o edital também não
   tem essa informação. Não há mais fonte pública pra esse dado. Fix
   (decisão validada com o usuário antes de implementar): removido o
   filtro hard de `scorers/leilao.py`; `situacao` agora vem sempre
   `"Desconhecida"` do scraper, e todo aprovado leva um alerta fixo
   (`🚨 Situação de ocupação NÃO disponível...`) pedindo conferência
   manual no edital antes de dar lance.

   Formato dos valores no detalhe também tem 3 variações (mesmo site,
   imóveis diferentes, sem padrão aparente de quando cada uma
   aparece): (a) "Valor mínimo de venda: R$ X (desconto de Y%)" — uma
   rodada, desconto pré-calculado; (b) "Valor mínimo de venda 1º
   Leilão: R$ X" — uma rodada, rótulo embutido, sem desconto
   calculado; (c) duas rodadas ainda não realizadas ("1º Leilão" +
   "2º Leilão") — usa sempre a primeira, é a única biddable agora.
   `desconto_pct` é sempre recalculado a partir de avaliação/preço via
   `LeilaoListing.__post_init__`, nunca lido do texto "(desconto de
   Y%)" (só aparece no formato *a*). Isso também explica por que
   imóveis em "Leilão SFI" (ainda na 1ª rodada, preço perto/acima da
   avaliação) aparecem com desconto negativo — é o comportamento
   correto, o filtro `desconto_minimo` já corta esses.

10. **Resale adicionado como 2ª fonte de leilão (05/09/2026)** — pedido
    do usuário: identificar mais sites de leilão além da Caixa.
    Investigação ao vivo mostrou que bancos grandes (Santander,
    Bradesco, Banco do Brasil) não têm engine de busca própria
    scrapeável — só redirecionam pra leiloeiros/agregadores terceiros.
    Testei viabilidade de vários (Portal Zuk, Mega Leilões, Frazão, VIP
    Leilões — todos acessíveis mas não investigados a fundo; Sodré
    Santoro e domínio próprio do Santander — bloqueados/não existem).
    `leilaoimovel.com.br` (agregador multi-banco, incluindo a própria
    Caixa) é promissor mas fica pra depois — tem filtro de cidade em
    cascata via AJAX não resolvido ainda, mesmo tipo de trabalho que o
    fix #9 exigiu.

    **Resale (resale.com.br) foi implementado primeiro** por ter dado
    bem mais rico: é um SPA React sem nada no HTML — achei a API real
    (`https://q3jhhgksa9.execute-api.us-east-2.amazonaws.com/prod`)
    inspecionando as chamadas de rede no browser, e a API key
    (`X-API-KEY`) abrindo o bundle JS público do site e procurando por
    "x-api-key" — é uma chave client-side, pública, que qualquer
    navegador usa ao visitar o site, não é bypass de nada. Schema JSON
    limpo (`scrapers/resale.py:_mapear`): `desagio` (desconto %),
    `valores.valor_avaliado`/`valor_venda`, e o que mais importa —
    `tags` inclui **"Desocupado"/"Locado" de verdade**, mais
    `possui_dividas`/`possui_contencioso` (débitos e ação judicial em
    andamento) que a Caixa nunca expôs. Isso permitiu reverter parte
    do fix #9: `scorers/leilao.py` volta a tratar ocupação como filtro
    hard, mas só quando `situacao != "Desconhecida"` — a Caixa
    continua sem esse dado e continua só com alerta.

    Limitações mapeadas ao vivo:
    - **Sem filtro de cidade/estado que funcione via query string** —
      testei `cidade=Piracicaba`, `estado=SP`, `uf=SP` etc., todos
      ignorados silenciosamente (resultado idêntico ao sem filtro).
      O endpoint `/prod/city` existe (populava um combobox em cascata
      no front) mas não aceitei nenhum parâmetro óbvio nem path
      (`/city/SP`) fazendo ele devolver as cidades de um estado — só
      a lista de estados, sempre. Não vale a pena insistir: o volume
      total é pequeno (~500 imóveis, Brasil inteiro), então
      `scrapers/resale.py:buscar()` pagina tudo (`tipo-venda=leilao`,
      sem outro filtro) e filtra cidade/tipo no Python.
    - **Paginação real é via `page` (1-indexed), não `offset`** — na
      sessão de 04/09 a API aceitava `offset` sem erro e pareceu
      funcionar; reteste ao vivo em 11/09 mostrou que `offset` é
      ignorado (offset=0/10/20 devolviam os 3 mesmos itens) — o campo
      `pagination.offset` da resposta é só o tamanho fixo de página
      (hoje 20), não um parâmetro de entrada. `page` é o que de fato
      pagina (IDs diferentes por página, confirmado).
    - **O parâmetro `order` quebra o backend** — descoberto no reteste
      de 11/09: `order=relevante` (usado pelo próprio front do site,
      é de onde copiei o parâmetro originalmente) faz a API responder
      HTTP 200 com corpo `{"errorType": "Runtime.ExitError", ...}` em
      vez do `{"data": ..., "pagination": ...}` esperado — bug deles,
      não WAF nem cache. (Isolado testando combinações de parâmetro:
      `tipo-venda=leilao` sozinho ou com `page` funciona; qualquer
      coisa com `order` quebra.) `scrapers/resale.py` nunca manda
      esse parâmetro. `_buscar_pagina()` continua validando que
      `data`/`pagination` existem no corpo mesmo com HTTP 200, como
      rede de segurança pra qualquer erro futuro do tipo.
    - **WAF sensível a rajada de requests** — 5 chamadas em poucos
      segundos (algumas com parâmetros IDÊNTICOS a uma que tinha
      acabado de funcionar) já tomaram 403, bloqueio durou mais de
      30s. Pacing bem mais conservador que a Caixa (2s entre páginas,
      retry com backoff de 30s em vez de desistir no primeiro 403).

    **Testado fim a fim ao vivo em 11/09/2026** (scraper → scorer →
    telegram, via `notifier/telegram.py:_formatar_leilao`): 7 imóveis
    achados em SP capital/Campinas (26 páginas, ~500 imóveis
    nacionais varridos), 2 aprovados baixando `desconto_minimo` pra
    10% (amostra do dia era pequena com o filtro padrão de 25%).
    Achei e corrigi 2 bugs nesse teste:
    - `_extrair_bairro()` assumia que o bairro era sempre o ÚLTIMO
      segmento de `nome_imovel`, mas alguns imóveis têm extra tipo
      "1 dormitório(s)"/"1 vaga(s) de garagem" anexado depois do
      bairro (ex: "Apartamento, Residencial, Moema, 1 dormitório(s)").
      Fix: bairro é sempre o TERCEIRO segmento (índice 2), não o
      último.
    - `notifier/telegram.py:_formatar_leilao` tinha "🏦 LEILÃO CAIXA"
      **hardcoded** no cabeçalho da mensagem, mesmo pra imóveis da
      Resale — confundiria o usuário sobre em qual site dar o lance.
      Fix: label agora vem de `listing.fonte` (`"LEILÃO CAIXA"` /
      `"LEILÃO RESALE"`).

11. **Dashboard unificado — abas Mercado/Leilão (11/09/2026)** — pedido
    do usuário: hoje `notifier/dashboard.py` só existia pro Tier 1 (uma
    aba por região), e o Tier 3 não gerava dashboard nenhum, só
    Telegram. Adicionei um nível de aba acima das já existentes: 🏠
    Mercado / 🏦 Leilão, cada uma com suas próprias sub-abas (região
    pro Mercado, fonte Caixa/Resale pro Leilão) e seu próprio card
    (`_montar_card_mercado` vs `_montar_card_leilao` — campos e
    critérios de breakdown diferentes, `_CRITERIOS` vs
    `_CRITERIOS_LEILAO`).

    **Problema de design que não é óbvio, cuidado se mexer**: Tier 1 e
    Tier 3 rodam em invocações SEPARADAS de `main.py` (`--tier 1` vs
    `--tier 3`), nunca juntas. Se `salvar_e_abrir()` sobrescrevesse o
    dashboard inteiro toda vez, rodar Tier 3 sozinho apagaria a aba de
    Mercado até o próximo cron (e vice-versa). Resolvido com um cache
    em disco por tier (`data/_dash_cache_mercado.html` e
    `data/_dash_cache_leilao.html`, HTML já renderizado, não os objetos
    `ScoreResult`/`LeilaoScoreResult` — mais simples que serializar
    dataclass e não precisa reconstruir nada, dashboard só lê pra
    montar string mesmo). `salvar_e_abrir(resultados_mercado=None, ...)`
    agora aceita `None` pra "esse tier não rodou agora" — só nesse caso
    ele lê do cache; se receber `{}` ou uma lista vazia, entende que
    rodou e deu zero aprovados (atualiza o cache pra vazio mesmo).
    `main.py:rodar_tier1` só passa `resultados_mercado`;
    `rodar_tier3` (que antes não chamava dashboard nenhum) agora
    agrupa `aprovados` por `listing.fonte` e passa só
    `resultados_leilao`.

    Testado ao vivo combinando uma rodada real de OLX (Tier 1,
    sp_capital) com uma rodada real de Resale (Tier 3) em duas
    invocações Python separadas — abri no browser, confirmei que as
    duas seções aparecem juntas e a troca de aba funciona. Achei 1 bug
    nesse teste: `scorers/leilao.py:_gerar_alertas` mistura avisos de
    risco (🚨/⚠️) com confirmações positivas (🔑 "Desocupado —
    confirmado pela fonte") na mesma lista `alertas`, e o CSS pintava
    TUDO de vermelho — a confirmação positiva saía com cara de alerta
    de perigo. Fix: `dashboard.py:_classe_alerta()` decide a cor pelo
    emoji inicial do texto (🚨/⚠️ → vermelho, resto → verde), não pela
    lista em si.

12. **Caixa (Tier 3) passou a buscar Campinas/Piracicaba, não só São
    Paulo (11/09/2026)** — pedido do usuário. Antes `caixa_leilao.py`
    usava um único código de cidade fixo (`leilao.cidade`); a Resale já
    cobria as 3 cidades desde a implementação inicial (filtra por texto
    no Python, não por código — ver "Bugs corrigidos" #10), então só a
    Caixa precisava do fix. Solução: `leilao.cidades` (a MESMA lista de
    nomes que a Resale já usa) agora também alimenta a Caixa —
    `_resolver_codigos_cidade()` traduz cada nome pro código interno
    via `_CODIGO_CIDADE_SP` (`9859`=SP, `9205`=Campinas,
    `9682`=Piracicaba) e `buscar()` roda uma busca por cidade. Cidade
    sem código no mapa é pulada com warning, não quebra as outras.
    `leilao.cidade` (código único, formato antigo) continua funcionando
    como fallback se `leilao.cidades` não estiver setado.

    Testado ao vivo: 395 imóveis em SP capital, 42 em Campinas, 37 em
    Piracicaba (query separada por cidade — a Caixa não aceita múltiplas
    cidades numa busca só). Pipeline completo (scraper→scorer) rodado
    com as 3 cidades: 60 coletados (20 de cada, com `max_detalhes: 20`
    de teste), 24 aprovados, distribuídos entre as 3 — sem erro.

    **Atenção pra quem for calibrar isso depois**: `leilao.max_detalhes`
    é POR CIDADE agora, não total — com 3 cidades e o padrão de 150, o
    pior caso é 450 requests de detalhe numa rodada (~3min a 0.4s/req).

13. **`resolver_bairro` casava bairro composto errado (12/09/2026)** —
    bug reportado pelo usuário: notificações de "Jardim Paraíso"
    chegando como se fossem do bairro-alvo "Paraíso". Causa:
    `utils/bairro.py:resolver_bairro` fazia substring match puro
    (`ref_norm in alvo`) — "paraiso" É literalmente um substring de
    "jardim paraiso", mesmo sendo dois bairros completamente diferentes
    (Jardim Paraíso fica em outra região da cidade, nada a ver com o
    Paraíso perto da Av. Paulista). Reproduzido ao vivo antes do fix:
    `resolver_bairro('Jardim Paraíso', refs)` retornava `'Paraíso'`.

    Fix: `_contem_bairro()` agora exige fronteira de palavra (regex
    `(?<!\w)...(?!\w)`) E rejeita o match se a palavra imediatamente
    anterior for um prefixo que em São Paulo sempre forma bairro
    composto diferente — `jardim`, `vila`, `parque`, `cidade`,
    `conjunto`, `nucleo`, `recanto`, `chacara`, `colonia`, `parada`,
    `residencial`, `condominio`, `loteamento` (lista baseada nos
    padrões reais vistos no cadastro de bairros da Caixa — dezenas de
    "JARDIM X"/"VILA X"/"PARQUE X" catalogados, ver scrapers/
    caixa_leilao.py). Testado com uma bateria de casos reais e
    hipotéticos: bug original (Jardim Paraíso → None agora, correto),
    match exato (Paraíso → Paraíso), texto livre com preposição
    ("Apartamento em Paraíso, SP" → Paraíso, continua funcionando),
    bairro composto que É o próprio alvo ("Jardim Paulista" → Jardim
    Paulista, não quebrou), e mais alguns prefixos (Vila Moema, Itaim
    Paulista → None, corretamente rejeitados).

    **Limitação conhecida, não corrigida**: "Alto de Pinheiros" ainda
    casa com "Pinheiros" — a palavra antes de "pinheiros" ali é "de",
    não "alto" (a lista de prefixos olha só a palavra imediatamente
    anterior). Não impede uso: "Alto de Pinheiros" nunca apareceu como
    falso-positivo reportado de verdade, e sua faixa de preço é
    parecida com Pinheiros (ao contrário do caso Jardim Paraíso/
    Paraíso, que são bairros de perfil bem diferente). Documentado
    aqui pra não ser redescoberto como "bug" — é uma limitação aceita,
    não um descuido.

14. **Revisão de filtros do Leilão (12–13/09/2026, pedido do usuário)** —
    mudanças em `scorers/leilao.py`:
    - `possui_contencioso` (Resale) virou filtro hard — antes só
      gerava alerta (`_gerar_alertas`), mas ação judicial em andamento
      é exatamente o tipo de risco jurídico que o Tier 3 existe pra
      evitar, mesmo raciocínio do filtro de ocupação. Alerta
      correspondente removido (quem chega no card já passou pela
      checagem, não faz sentido alertar sobre algo que já reprovou).
    - `preco_max` (novo, padrão 500000 — mesmo orçamento do Tier 1) —
      antes não existia teto nenhum; um imóvel de R$2M com bom desconto
      passava normalmente, mesmo fora do orçamento do usuário.
    - `area_min` foi implementado no dia 12/09 e **revertido no dia
      seguinte** — usuário decidiu que qualquer metragem deve passar
      em leilão, sem piso. `_criterio_metragem` (pontua menos área
      pequena, mas não elimina) continua sendo o único tratamento de
      metragem. Não readicionar sem pedido explícito.
    `preco_max` configurável em `config.yaml:leilao`, com fallback no
    código se não especificado. Testado ao vivo: dados reais da Resale
    confirmaram o corte de `preco_max` (6 imóveis de R$550k rejeitados);
    `possui_contencioso` testado com listing sintético (não achei
    exemplo real com esse valor no ar no momento do teste).

15. **QuintoAndar adicionado como 3ª fonte de mercado (15/09/2026)** —
    pedido do usuário: entender por que QuintoAndar não era scrapeado,
    e resolver. Causa: é um SPA React puro, sem SSR nem JSON-LD — nada
    útil no HTML inicial, ao contrário de OLX/ZAP. Resolvido via
    captura de HAR real feita pelo usuário no Chrome DevTools (duas
    tentativas — a primeira capturou a home em vez da busca, e veio
    truncada; a segunda, completa, achou a API real). Endpoint:
    `POST apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list`,
    sem autenticação nenhuma. `scrapers/quintoandar.py:_item_para_listing`
    descarta `isPrimaryMarket=true` (lançamento — preço vem em faixa
    min/max, não um valor pontual comparável ao resto do scorer) e
    `forSale=false`. Limitação aceita: `condominio` é
    `iptuPlusCondominium` já somado pela API — não dá pra separar os
    dois componentes. Testado ao vivo nas 3 regiões (97 listings em
    sp_capital isolado); Piracicaba inicialmente deu 0 resultados por
    causa de um viewport (bounding box) mal calibrado — corrigido pra
    um viewport mais largo, mesmo padrão das outras regiões.

16. **GitHub Actions + Pages + controle remoto via PWA (16/09/2026)** —
    pedido do usuário: rodar o bot e ver resultado mesmo com o PC
    desligado, incluindo do celular. Decisões (confirmadas com o
    usuário antes de implementar):
    - Repositório **público** — GitHub Pages grátis exige isso pra
      repo privado seria pago.
    - Workflow **só sob demanda** (`workflow_dispatch`), sem
      `schedule:` — usuário decidiu que rodar 3x/dia (padrão anterior,
      `scheduler.yml`) era gasto desnecessário de minutos de Actions.
      Arquivo renomeado pra `rodar-bot.yml` (refletir que não é mais
      "scheduler" nenhum).
    - Controle remoto do celular **sem** embutir secret nenhum na
      página pública: o dashboard (`notifier/dashboard.py`) ganhou um
      botão "▶ Rodar agora" + modal "⚙" que pede um PAT fine-grained
      do GitHub (escopo `Actions: Read and write` só nesse repo) —
      salvo só em `localStorage` do navegador do usuário, nunca
      passa pelo código publicado nem pelo assistente. O botão chama
      `POST /repos/{owner}/repo/actions/workflows/rodar-bot.yml/dispatches`
      direto da página.
    - Dashboard também é PWA instalável (`pwa/manifest.webmanifest`,
      `pwa/sw.js` — network-first pra HTML, cache-first fallback só
      offline; `pwa/icon.svg`). Service worker só registra quando
      `location.protocol !== 'file:'` — no uso local (arquivo aberto
      direto), o bloco vira no-op silencioso.
    - Workflow monta um diretório `site/` (dashboard.html renomeado
      pra index.html + os assets de `pwa/`) e publica só isso no Pages
      via `actions/upload-pages-artifact` — nunca publica `data/`
      inteiro (teria `vistos.db` e HTMLs de debug).
    Habilitado via `gh api repos/PedroZak/imovel-bot/pages -X POST -f
    build_type=workflow` (com autorização explícita do usuário antes
    de mudar a configuração do repositório). Testado visualmente no
    browser: botão abre o modal quando não há PAT salvo, dark mode
    funciona nos elementos novos.

17. **Telegram removido por completo (16/09/2026)** — pedido explícito
    do usuário ("n quero telegram"), depois de confirmar que era pra
    remover de verdade (não só deixar de configurar). Motivo prático:
    o workflow sob demanda já tinha o Telegram desligado por padrão
    (`enviar_telegram: false`) desde o item #16 — manter o módulo
    inteiro parado, sem nenhum consumidor real, seria código morto.
    Removido:
    - `notifier/telegram.py` (inteiro).
    - `notifier/dedup.py` (inteiro) — sua ÚNICA função era evitar
      reenvio duplicado pro Telegram; o dashboard sempre mostrou TODO
      aprovado da rodada independente de dedup (nunca dependeu desse
      módulo). `geo_cache` (tabela que `dedup.inicializar()` também
      criava) já tinha init próprio e idempotente em
      `utils/geo.py:_init_cache()` — nenhuma outra parte do sistema
      dependia de `dedup.py` sobrevivendo.
    - Bloco `telegram:` de `config.yaml`, inputs/secrets
      `TELEGRAM_*` de `rodar-bot.yml` e `calibragem.yml`, flag
      `--dry-run`/`_validar_config`/`_aplicar_env` de `main.py`
      (não tinham mais razão de existir sem secret pra validar).
    - Checkbox "Enviar pro Telegram" do modal de controle remoto do
      dashboard (`notifier/dashboard.py`) — `rodarAgora()` não manda
      mais o input `enviar_telegram` no dispatch.
    - `calibragem/calibrar.py:notificar_resumo()` virou `logar_resumo()`
      — resumo da calibragem (bairros atualizados + divergências) vai
      só pro log agora, não tenta mais mandar Telegram.
    - Opção 6 do `testar.bat` (que injetava credenciais fake só pra
      passar da validação de config) virou simplesmente "rodar bot
      completo, Tier 1" — sem workaround nenhum, porque não tem mais
      validação de Telegram pra contornar.
    `main.py` ficou bem mais simples: `_coletar_pontuar_notificar` (que
    também deduplicava e enviava) virou `_coletar_e_pontuar` (só
    coleta, grava histórico, pontua — sem loop de envio/dedup).
    **Efeito colateral**: os itens de backlog "Fingerprint dedup
    aproximado" e "Tier 3 sem dedup entre fontes" (histórico, mais
    abaixo) ficam obsoletos — não existe mais dedup nenhum no projeto,
    não é uma limitação, é o comportamento pretendido agora (dashboard
    sempre mostra tudo que passou no score na rodada atual).

18. **Cache do dashboard não sobrevivia entre rodadas do GitHub Actions
    (17/09/2026)** — usuário rodou Tier 1 real (111 aprovados, aba
    Mercado populada), depois perguntou como rodar o Leilão e notou que
    a aba Leilão aparecia zerada. Causa: o mecanismo de cache em disco
    que faz Tier 1 e Tier 3 conviverem sem se apagar (`_dash_cache_mercado.html`/
    `_dash_cache_leilao.html`, ver "Bugs corrigidos" #11) foi desenhado
    pra máquina local, onde o filesystem persiste entre execuções. No
    GitHub Actions cada rodada começa de um **checkout limpo** — só o
    que está explicitamente em `actions/cache` sobrevive, e o
    `rodar-bot.yml` original só cacheava `data/vistos.db`, não os 2
    HTMLs de cache do dashboard. Resultado real: rodar só Tier 3
    apagaria a aba Mercado do site publicado (e vice-versa) — não era
    "leilão zerado por bug no scraper", era o cache nunca ter chance de
    sobreviver de uma rodada de Actions pra outra. Fix: `path:` dos
    passos "Restore cache"/"Save cache" em `rodar-bot.yml` passou a
    listar os 3 arquivos (`vistos.db` + os 2 `_dash_cache_*.html`), não
    só o banco. **Atenção**: como o fix só entrou depois da primeira
    rodada de Tier 1 em produção, o cache daquela rodada nunca foi
    salvo (workflow antigo não sabia que devia salvar os HTMLs) — a
    primeira rodada de Tier 1 DEPOIS deste fix é que começa a alimentar
    o cache pra valer; até lá, rodar só Tier 3 ainda mostraria Mercado
    vazio uma última vez.

19. **Histórico de preços contava o mesmo anúncio a cada rodada
    (18/09/2026)** — ao validar a calibragem na nuvem, Piracicaba
    atingiu o mínimo de 15 amostras com só 3–4 rodadas e a mediana
    móvel já deu saltos de ±30% (Centro −33%, São Dimas +34%), que
    entram direto no score. Causa: `utils/historico.py:registrar_listings`
    inseria TODO listing coletado em TODA rodada, sem deduplicar — a
    mesma foto do mercado repetida N vezes, não N amostras
    independentes. Fix: coluna `listing_id` + índice único
    `(fonte, listing_id)` + `INSERT OR IGNORE` — cada anúncio conta uma
    vez (na primeira aparição). `_migrar_listing_id()` adiciona a coluna
    em bancos antigos e **descarta as linhas antigas sem id** (sem id
    não dá pra deduplicar; eram dado enviesado). Consequência esperada:
    Campinas/Piracicaba voltam a "amostras insuficientes" (mantêm o
    valor manual do config) até o histórico se reconstruir com anúncios
    distintos — não é regressão.
    Limitação conhecida: mediana é de preço de ANÚNCIO, não de ITBI
    (fechamento) — sempre acima do Atlas; por isso não é usada como
    fallback pra SP capital (ver Paraíso abaixo).
    **Paraíso**: `atlasdados.com/sp/bairro/paraiso/` dá 404 e nenhuma
    variante de slug existe (Atlas não trata como bairro autônomo).
    Sem fonte ITBI pública; `compra_m2` fica com a estimativa manual do
    config.yaml (10500, "interpolado").

20. **Aba Leilão agora é dividida por cidade, não por fonte
    (18/09/2026)** — pedido do usuário, mesmo filtro do Mercado.
    `main.py:_agrupar_leilao_por_cidade()` agrupa por `listing.cidade`
    na ordem de `leilao.cidades`; normaliza acento/caixa porque a Caixa
    devolve "SAO PAULO" e a Resale "São Paulo". Fonte (Caixa/Resale)
    continua visível no próprio card.

21. **Calibragem passou a rodar junto de toda rodada do "Rodar agora"
    (22/09/2026)** — pedido do usuário: ele roda tudo pelo GitHub
    Actions e não queria esperar o cron mensal (`calibragem.yml`, dia
    1º) pra ver preço de referência atualizado. `rodar-bot.yml` ganhou
    um passo `python -m calibragem.calibrar` antes de `python main.py`,
    no MESMO job (reaproveita o `data/vistos.db` já restaurado do
    cache). Não commita `referencias_calibradas.yaml` de volta — quem
    persiste isso no repo continua sendo `calibragem.yml`; aqui é só
    pra essa rodada, escrito no checkout efêmero e lido na sequência
    por `main.py:_mesclar_calibragem`, no mesmo job. `continue-on-error:
    true` — falha do Atlas (site fora do ar, por ex.) não pode travar a
    busca de imóveis. Consequência esperada de "Bugs corrigidos" #19
    (histórico deduplicado): recalibrar toda hora não distorce mais a
    mediana, mas Campinas/Piracicaba só mudam de verdade quando surgem
    anúncios NOVOS desde a última vez — não fica variando a cada clique
    sem motivo.

22. **Cabeçalho e filtros redesenhados pra ocupar menos espaço no celular
    (22/09/2026)** — pedido do usuário com print do dashboard no
    celular: cabeçalho (título + "Gerado em" + "Última calibração" em
    linhas separadas) e o bloco de filtros (5 campos empilhados full-
    width) empurravam as abas Mercado/Leilão pra fora da tela.
    - Cabeçalho: título + botões (Rodar agora/⚙/🌙) numa linha só
      (`.header-top`); "Gerado em"/"Calibrado em" viraram uma única
      linha pequena e muted (`.header-meta`) embaixo, em vez de 2
      `<span>` competindo por espaço com os botões.
    - Filtros: `_montar_controles()` virou um `<details>`/`<summary>`
      nativo (`🔍 Filtros e ordenação`), fechado por padrão — sem JS
      extra pra abrir/fechar. Adicionado `#filtros-badge` (atualizado
      em `atualizar()`) mostrando quantos filtros de preço/área estão
      ativos MESMO com o painel fechado, pra não esconder que um filtro
      está aplicado.
    Testado visualmente (mobile 375px, desktop, claro/escuro) com dado
    sintético local — abas ficam visíveis quase imediatamente após o
    cabeçalho agora.

## Backlog conhecido (não resolvido, com contexto)

- **Campinas/Piracicaba com calibragem fraca** — sem fonte tipo Atlas
  pra essas cidades. A mediana móvel (`utils/historico.py`) só assume
  quando tiver ≥15 amostras em 60 dias por bairro — até lá, usa
  estimativa manual (parcialmente pesquisada, ver comentários no
  `config.yaml`).
- **VivaReal só cobre SP capital** — `scrapers/zap.py:SEARCH_URL["VIVA_REAL"]`
  é uma URL fixa de São Paulo, sem suporte a slug por região (diferente
  do ZAP). Pra ativar em Campinas/Piracicaba precisaria descobrir a URL
  de busca do VivaReal por região primeiro — não tentado ainda.
- ~~Fingerprint dedup / dedup entre fontes~~ — **obsoleto desde
  16/09/2026**: não existe mais dedup nenhum no projeto (Telegram e
  `notifier/dedup.py` foram removidos por completo, ver "Bugs
  corrigidos" #17). Dashboard sempre mostra todo aprovado da rodada
  atual, sem tentar lembrar de rodadas anteriores.
- **`leilaoimovel.com.br` não implementado** — agregador multi-banco
  (Santander, Bradesco, BB e a própria Caixa juntos), promissor, mas
  fica pra depois: tem filtro de cidade/bairro em cascata via AJAX
  (mesmo tipo de reverse-engineering do fix #9), ainda não resolvido.
  Ver "Bugs corrigidos" #10 pro que já foi investigado nele.

## Coisas específicas do ambiente do usuário (Windows)

- PowerShell precisou de `Set-ExecutionPolicy -ExecutionPolicy
  RemoteSigned -Scope CurrentUser` pra rodar o `.venv\Scripts\activate`.
- Comandos encadeados usam `;`, não `,` (erro comum já aconteceu).
- O Google Drive não aceita pasta iniciada por ponto — `.github` teve
  que ser criado manualmente depois (`New-Item -ItemType Directory
  ".github"` + mover `workflows` pra dentro).
- Ativar o venv (que fica fora da pasta do Drive):
  ```powershell
  & "$env:USERPROFILE\venvs\imovel-bot\Scripts\Activate.ps1"
  ```

## Segurança

- Histórico: um token antigo do Telegram vazou uma vez (achado num
  arquivo real numa pasta do Drive) — motivou parte da disciplina de
  nunca commitar secret nenhum neste projeto. Desde 16/09/2026 isso
  deixou de ser uma categoria de risco aqui: Telegram foi removido por
  completo (ver "Bugs corrigidos" #17), `config.yaml` não tem mais
  bloco `telegram:`/token nenhum, e o repositório não guarda secret
  nenhum no GitHub (nenhum `gh secret set` foi necessário).
- O único "segredo" do sistema hoje é o PAT fine-grained que cada
  usuário gera pra si mesmo pra usar o botão "Rodar agora" do
  dashboard — nunca passa pelo código do repositório nem pelo
  assistente, fica só no `localStorage` do navegador de quem gerou.
  Ver "Bugs corrigidos" #16.
