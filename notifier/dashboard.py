"""
notifier/dashboard.py
──────────────────────
Dashboard HTML local — dividido em 2 tiers de nível superior (🏠
Mercado / 🏦 Leilão), cada um com uma aba por região (Mercado) ou
fonte (Leilão), cards por imóvel aprovado. Gerado toda vez que um
script produtor de listings roda (testar_scraper.py, main.py), pra
não depender de scroll no console ou de guardar a janela do terminal
aberta.

Documento único, sem dependência externa (sem Jinja2 — não é dependência
do projeto, ver requirements.txt): tudo montado via f-string, CSS e JS
inline.

IMPORTANTE — cache por tier: Tier 1 (mercado) e Tier 3 (leilão) rodam
em invocações SEPARADAS de main.py (`--tier 1` vs `--tier 3`), nunca
juntas. Se cada uma sobrescrevesse o dashboard inteiro, rodar Tier 3
sozinho apagaria a aba de Mercado (e vice-versa) até o próximo cron.
Por isso cada tier persiste seu HTML já renderizado em um cache
próprio (`_CACHE_MERCADO`/`_CACHE_LEILAO`) — quando `salvar_e_abrir()`
recebe `None` pra um tier (não rodou nessa invocação), reaproveita o
cache da última rodada em vez de mostrar vazio. Dentro do MESMO tier,
cada rodada ainda sobrescreve a anterior — sem histórico acumulado.
"""

import html
import logging
import os
import re
import shutil
import unicodedata
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from scorers.base import ScoreResult, LeilaoScoreResult

logger = logging.getLogger(__name__)

REFERENCIAS_CALIBRADAS_PATH = "referencias_calibradas.yaml"

_MESES_ABREV = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
                7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}


def _fmt_data_curta(dt: datetime) -> str:
    """'22/Set' — sem hora nem ano, pro header não ocupar espaço à toa.
    Mês abreviado em pt-BR na mão (não dá pra confiar em locale do SO)."""
    return f"{dt.day:02d}/{_MESES_ABREV[dt.month]}"
_CACHE_MERCADO = "data/_dash_cache_mercado.html"
_CACHE_LEILAO  = "data/_dash_cache_leilao.html"

# chave do breakdown → (label de exibição, pontuação máxima do critério)
# ver scorers/mercado.py — docstring do módulo + cada função _criterio_*
_CRITERIOS = {
    "preco":  ("Preço/m²",        35),
    "yield":  ("Yield",           25),
    "walk":   ("Caminhabilidade",  8),
    "carry":  ("Condomínio",      20),
    "bairro": ("Bairro-alvo",     10),
    "area":   ("Metragem",        10),
    "bonus":  ("Bônus",           20),
}

# ver scorers/leilao.py — docstring do módulo + cada função _criterio_*
_CRITERIOS_LEILAO = {
    "desconto":   ("Desconto",   70),
    "modalidade": ("Modalidade", 20),
    "area":       ("Metragem",   10),
}

_FONTE_LEILAO_LABEL = {"caixa_leilao": "Caixa", "resale": "Resale"}
_FONTE_LEILAO_EMOJI = {"caixa_leilao": "🏦", "resale": "🏷️"}


# ── API pública ───────────────────────────────────────────────

def salvar_e_abrir(
    resultados_mercado: Optional[dict] = None,
    resultados_leilao: Optional[dict] = None,
    path: str = "data/dashboard.html",
    abrir: bool = True,
) -> str:
    """
    Gera o dashboard HTML, salva em `path` e abre no navegador padrão
    (exceto em CI — GitHub Actions define a env var CI automaticamente).
    Retorna o path absoluto do arquivo escrito.

    `resultados_mercado`: {nome_da_regiao: list[ScoreResult]} — Tier 1.
    `resultados_leilao`:  {nome_da_fonte:  list[LeilaoScoreResult]} — Tier 3.
    Regiões/fontes com lista vazia são descartadas antes de montar as
    abas. Passar `None` (em vez de `{}`) pra um dos dois significa
    "esse tier não rodou agora" — reaproveita o cache da última rodada
    (ver docstring do módulo) em vez de mostrar a aba vazia.
    """
    destino = Path(path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(_montar_html(resultados_mercado, resultados_leilao), encoding="utf-8")

    caminho_absoluto = str(destino.resolve())

    if abrir and not os.getenv("CI"):
        try:
            url = destino.resolve().as_uri()
            if not _abrir_no_chrome(url):
                logger.debug("Chrome não encontrado — abrindo no navegador padrão.")
                webbrowser.open(url)
        except Exception as e:
            logger.warning(f"Não consegui abrir o navegador automaticamente: {e}")

    return caminho_absoluto


def _abrir_no_chrome(url: str) -> bool:
    """
    Abre especificamente no Chrome, não no navegador padrão do Windows.
    Procura o chrome.exe nos caminhos usuais de instalação; se não
    achar em nenhum, retorna False pra quem chamou cair no navegador
    padrão (não trava a geração do dashboard por causa disso).
    """
    candidatos = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ]
    chrome_path = next((c for c in candidatos if c and os.path.isfile(c)), None)
    if not chrome_path:
        chrome_path = shutil.which("chrome") or shutil.which("google-chrome")
    if not chrome_path:
        return False

    # Barra normal em vez de contrabarra do Windows — o webbrowser
    # module usa shlex.split() nesse comando, que trata "\" como
    # caractere de escape e quebraria o caminho.
    chrome_cmd = chrome_path.replace("\\", "/")
    webbrowser.get(f'"{chrome_cmd}" %s').open(url)
    return True


# ── Montagem do documento ────────────────────────────────────────

def _montar_html(resultados_mercado: Optional[dict], resultados_leilao: Optional[dict]) -> str:
    gerado_em = _fmt_data_curta(datetime.now())
    calibracao = _ler_data_calibragem()

    mercado_html, mercado_qtd = _preparar_tier(
        resultados_mercado, _CACHE_MERCADO, _montar_card_mercado,
        "🔍 Nenhum imóvel de mercado aprovado ainda.",
    )
    leilao_html, leilao_qtd = _preparar_tier(
        resultados_leilao, _CACHE_LEILAO, _montar_card_leilao,
        "🔍 Nenhum imóvel de leilão aprovado ainda.",
    )

    if mercado_qtd == 0 and leilao_qtd == 0:
        corpo = '<div class="vazio"><p>🔍 Nenhum imóvel aprovado ainda.</p></div>'
    else:
        tiers = [("mercado", "🏠 Mercado", mercado_qtd), ("leilao", "🏦 Leilão", leilao_qtd)]
        corpo = (
            _montar_controles()
            + _montar_tier_nav(tiers)
            + _montar_tier_panel("mercado", 0, mercado_html)
            + _montar_tier_panel("leilao", 1, leilao_html)
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>imovel-bot — dashboard</title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon.svg">
<meta name="theme-color" content="#0f1115">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="imovel-bot">
<script>{_JS_TEMA_INICIAL}</script>
<style>{_CSS}</style>
</head>
<body>
<header>
  <div class="header-top">
    <h1>🏠 imovel-bot</h1>
    <div class="header-actions">
      <button type="button" id="btn-rodar" class="btn-rodar" onclick="rodarAgora()" title="Disparar uma rodada agora via GitHub Actions">▶ Rodar agora</button>
      <button type="button" class="btn-config" onclick="abrirConfig()" title="Configurar token de acesso">⚙</button>
      <button type="button" id="theme-toggle" class="theme-toggle" onclick="alternarTema()" title="Alternar modo escuro">🌙</button>
    </div>
  </div>
  <div class="header-meta">📅 {gerado_em} &nbsp;·&nbsp; 🔧 Calibrado em {_esc(calibracao)}</div>
</header>

<div id="modal-config" class="modal-overlay" style="display:none" onclick="if(event.target===this) fecharConfig()">
  <div class="modal">
    <h2>⚙ Controle remoto</h2>
    <p class="modal-hint">
      Cole um token de acesso pessoal (fine-grained) do GitHub, com
      permissão <b>Actions: Read and write</b> só neste repositório.
      Fica guardado <b>só neste navegador</b> (localStorage) — nunca é
      enviado a lugar nenhum além da API do próprio GitHub.
    </p>
    <label>Token (PAT)
      <input type="password" id="cfg-token" placeholder="github_pat_...">
    </label>
    <label>Tier a rodar
      <select id="cfg-tier">
        <option value="1">1 — Mercado</option>
        <option value="3">3 — Leilão</option>
      </select>
    </label>
    <div class="modal-botoes">
      <button type="button" onclick="salvarConfig()">Salvar</button>
      <button type="button" onclick="fecharConfig()">Cancelar</button>
    </div>
  </div>
</div>

<main>
{corpo}
</main>
<script>{_JS}</script>
<script>
// Service worker só existe quando servido via GitHub Pages (https://...).
// No dashboard local (file://), 'serviceWorker' nem aparece em navigator
// nesse protocolo — este bloco vira no-op silencioso, não quebra nada.
if ('serviceWorker' in navigator && location.protocol !== 'file:') {{
  navigator.serviceWorker.register('sw.js').catch(() => {{}});
}}
</script>
</body>
</html>
"""


def _ler_data_calibragem(path: str = REFERENCIAS_CALIBRADAS_PATH) -> str:
    """
    Lê 'gerado_em' de referencias_calibradas.yaml (mesmo arquivo que
    main.py mescla no config — ver _mesclar_calibragem). Retorna texto
    amigável pro header; nunca levanta exceção — falha aqui não pode
    quebrar a geração do dashboard.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            dados = yaml.safe_load(f) or {}
        bruto = dados.get("gerado_em")
        if not bruto:
            return "nunca rodada"
        dt = datetime.fromisoformat(str(bruto))
        return _fmt_data_curta(dt)
    except FileNotFoundError:
        return "nunca rodada"
    except Exception as e:
        logger.debug(f"Não consegui ler data de calibragem: {e}")
        return "desconhecida"


def _preparar_tier(
    resultados: Optional[dict], cache_path: str, montar_card_fn, msg_vazio: str,
) -> tuple:
    """
    Gera o HTML (sub-abas + cards) de um tier a partir de `resultados`
    e atualiza o cache em disco — ou, se `resultados` é None (esse
    tier não rodou nessa invocação), reaproveita o cache da última
    rodada. Ver docstring do módulo pra explicação completa do porquê
    do cache. Retorna (html, quantidade_total_de_aprovados).
    """
    if resultados is not None:
        grupos = {nome: r for nome, r in resultados.items() if r}
        total = sum(len(r) for r in grupos.values())
        html_tier = (
            _montar_subtabs(grupos, montar_card_fn) if grupos
            else f'<div class="vazio"><p>{_esc(msg_vazio)}</p></div>'
        )
        try:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_text(f"{total}\n{html_tier}", encoding="utf-8")
        except Exception as e:
            logger.warning(f"Não consegui salvar cache do dashboard ({cache_path}): {e}")
        return html_tier, total

    try:
        bruto = Path(cache_path).read_text(encoding="utf-8")
        primeira_linha, _, resto = bruto.partition("\n")
        return resto, int(primeira_linha)
    except (FileNotFoundError, ValueError) as e:
        logger.debug(f"Sem cache prévio pra {cache_path} ({e}) — mostrando vazio")
        return f'<div class="vazio"><p>{_esc(msg_vazio)}</p></div>', 0


def _montar_subtabs(grupos: dict, montar_card_fn) -> str:
    nomes = list(grupos.keys())
    slugs = {nome: _slugify(nome) for nome in nomes}
    return _montar_tab_nav(nomes, slugs, grupos) + _montar_tab_paineis(nomes, slugs, grupos, montar_card_fn)


def _montar_tier_nav(tiers: list) -> str:
    """tiers: [(slug, label, quantidade), ...]"""
    botoes = []
    for i, (slug, label, qtd) in enumerate(tiers):
        ativo = " active" if i == 0 else ""
        botoes.append(
            f'<button class="tier-btn{ativo}" data-tier="{slug}" '
            f'onclick="mostrarTier(\'{slug}\')">{_esc(label)} ({qtd})</button>'
        )
    return f'<nav class="tier-nav">{"".join(botoes)}</nav>'


def _montar_tier_panel(slug: str, indice: int, conteudo: str) -> str:
    display = "block" if indice == 0 else "none"
    return f'<div class="tier-panel" data-tier="{slug}" style="display:{display}">{conteudo}</div>'


def _montar_controles() -> str:
    return """<details class="controles">
  <summary>🔍 Filtros e ordenação <span id="filtros-badge" class="filtros-badge" hidden></span></summary>
  <div class="controles-corpo">
    <label>Ordenar por
      <select id="ordenar" onchange="atualizar()">
        <option value="score-desc">Score (maior primeiro)</option>
        <option value="score-asc">Score (menor primeiro)</option>
        <option value="preco-asc">Preço (menor primeiro)</option>
        <option value="preco-desc">Preço (maior primeiro)</option>
        <option value="area-desc">Área (maior primeiro)</option>
        <option value="area-asc">Área (menor primeiro)</option>
        <option value="precom2-asc">Preço/m² (menor primeiro)</option>
        <option value="precom2-desc">Preço/m² (maior primeiro)</option>
      </select>
    </label>
    <label>Preço min. <input type="number" id="preco-min" oninput="atualizar()" placeholder="R$"></label>
    <label>Preço máx. <input type="number" id="preco-max" oninput="atualizar()" placeholder="R$"></label>
    <label>Área min. <input type="number" id="area-min" oninput="atualizar()" placeholder="m²"></label>
    <label>Área máx. <input type="number" id="area-max" oninput="atualizar()" placeholder="m²"></label>
    <button type="button" onclick="limparFiltros()">Limpar filtros</button>
  </div>
</details>"""


def _montar_tab_nav(nomes: list, slugs: dict, regioes: dict) -> str:
    botoes = []
    for i, nome in enumerate(nomes):
        ativo = " active" if i == 0 else ""
        qtd = len(regioes[nome])
        botoes.append(
            f'<button class="tab-btn{ativo}" data-tab="{slugs[nome]}" '
            f'onclick="mostrarTab(\'{slugs[nome]}\')">{_esc(nome)} ({qtd})</button>'
        )
    return f'<nav class="tab-nav">{"".join(botoes)}</nav>'


def _montar_tab_paineis(nomes: list, slugs: dict, regioes: dict, montar_card_fn) -> str:
    paineis = []
    for i, nome in enumerate(nomes):
        display = "grid" if i == 0 else "none"
        cards = "".join(montar_card_fn(r) for r in regioes[nome])
        paineis.append(
            f'<div class="tab-panel" data-tab="{slugs[nome]}" style="display:{display}">'
            f"{cards}"
            f"</div>"
        )
    return "".join(paineis)


def _montar_card_mercado(r: "ScoreResult") -> str:
    l = r.listing
    fonte_emoji = {"olx": "🟠", "zap": "🔵", "vivareal": "🟢", "quintoandar": "🟣"}.get(l.fonte, "🏠")

    if l.fotos and l.fotos[0]:
        foto_html = f'<img src="{_esc(l.fotos[0])}" alt="" loading="lazy">'
    else:
        foto_html = '<div class="sem-foto">sem foto</div>'

    just_html = "".join(f"<li>{_esc(j)}</li>" for j in r.justificativas)
    justificativas_html = f'<ul class="justificativas">{just_html}</ul>' if just_html else ""

    return f"""<article class="card" data-score="{r.score}" data-preco="{l.preco or ''}" \
data-area="{l.area or ''}" data-precom2="{l.preco_m2 or ''}">
  <div class="card-foto">{foto_html}</div>
  <div class="card-corpo">
    <div class="card-titulo">{fonte_emoji} {_esc(l.titulo)}</div>
    <div class="card-local">📍 {_esc(l.bairro)}, {_esc(l.cidade)}</div>
    <div class="card-stats">
      <span><b>{_fmt_brl(l.preco)}</b></span>
      <span>{_fmt_m2(l.area)}</span>
      <span>🛏 {l.quartos if l.quartos is not None else '?'}q</span>
      <span>🚿 {l.banheiros if l.banheiros is not None else '?'}bh</span>
      <span>🚗 {l.vagas}</span>
    </div>
    <div class="card-stats secundario">
      <span>📊 {_fmt_brl(l.preco_m2)}/m²</span>
      <span>🏢 Cond: {_fmt_brl(l.condominio)}/mês</span>
      <span>🏛 IPTU: {_fmt_brl(l.iptu)}/ano</span>
    </div>
    {_montar_delta_bairro(r)}
    <div class="card-score">
      <span class="score-badge {_tier_score(r.score)}">⭐ {r.score:.1f}/100</span>
    </div>
    {_montar_breakdown(r.breakdown)}
    {justificativas_html}
    {_montar_flip(r)}
    <a class="card-link" href="{_esc(l.url)}" target="_blank" rel="noopener noreferrer">
      Ver anúncio ({l.fonte.upper()}) →
    </a>
  </div>
</article>
"""


def _montar_delta_bairro(r: "ScoreResult") -> str:
    """
    Delta entre o preço/m² do anúncio e a mediana de venda real (ITBI)
    do bairro — refs[bairro_key]['compra_m2'], calculado no scorer
    (scorers/mercado.py:_calcular_metricas) e exposto via
    ScoreResult.desconto_pct/referencia_m2. Não é "preço médio" nem
    "preço máximo" (o Atlas não expõe isso, só a mediana) — só a
    mediana mesmo, mas é dado de venda real, não preço de anúncio.
    """
    if r.desconto_pct is None or not r.referencia_m2:
        return ""

    pct = r.desconto_pct * 100
    ref_txt = f"{_fmt_brl(r.referencia_m2)}/m²"

    if pct > 0.5:
        return f'<div class="card-delta delta-baixo">🟢 {pct:.0f}% abaixo da mediana do bairro ({ref_txt})</div>'
    if pct < -0.5:
        return f'<div class="card-delta delta-alto">🔴 {abs(pct):.0f}% acima da mediana do bairro ({ref_txt})</div>'
    return f'<div class="card-delta delta-neutro">⚖️ Na mediana do bairro ({ref_txt})</div>'


_FLIP_VEREDITO = {
    "bom_negocio":     ("🟢", "Bom negócio",     "flip-bom"),
    "margem_apertada": ("🟡", "Margem apertada", "flip-apertado"),
    "nao_compensa":    ("🔴", "Não compensa",    "flip-ruim"),
}


def _montar_flip(r: "ScoreResult") -> str:
    """
    Análise de flip (compra + reforma padrão média + venda) — ver
    scorers/flip.py. r.flip é None quando falta quartos, banheiros ou
    referência de bairro pra estimar com segurança; nesse caso não
    mostra a seção (mesma lógica de _montar_delta_bairro).
    """
    flip = r.flip
    if flip is None:
        return ""

    emoji, label, classe = _FLIP_VEREDITO.get(flip.veredito, ("⚪", flip.veredito, ""))
    pct_txt = f"{flip.margem_pct * 100:+.0f}%"
    meses_totais = flip.meses_reforma + flip.meses_venda

    return f"""<div class="flip-box {classe}">
      <div class="flip-titulo">🔨 Flip: {emoji} {label}</div>
      <div class="flip-linha">Reforma estimada ({flip.quartos}q, {flip.banheiros}bh): {_fmt_brl(flip.custo_reforma_estimado)}</div>
      <div class="flip-linha">Manutenção ({meses_totais:.0f} meses — cond+IPTU): {_fmt_brl(flip.custo_manutencao_estimado)}</div>
      <div class="flip-linha">Venda esperada (mediana do bairro): {_fmt_brl(flip.valor_venda_esperado)}</div>
      <div class="flip-linha"><b>Margem estimada: {_fmt_brl(flip.margem_estimada)} ({pct_txt})</b></div>
    </div>"""


def _montar_breakdown(breakdown: dict, criterios: dict = _CRITERIOS) -> str:
    if not breakdown:
        return ""
    linhas = []
    for chave, pts in breakdown.items():
        label, maximo = criterios.get(chave, (chave, None))
        max_txt = f"/{maximo}" if maximo is not None else ""
        linhas.append(f"<li><span>{_esc(label)}</span><span>{pts:.1f}{max_txt}</span></li>")
    return f'<ul class="breakdown">{"".join(linhas)}</ul>'


def _classe_alerta(texto: str) -> str:
    """
    scorers/leilao.py:_gerar_alertas mistura avisos de risco (🚨/⚠️,
    ex: ocupação desconhecida, dívidas) com confirmações positivas
    (🔑/ℹ️, ex: "Desocupado — confirmado pela fonte") na mesma lista —
    sem isso, o CSS pintaria a confirmação positiva de vermelho igual
    um alerta de perigo.
    """
    return "alerta-risco" if texto.startswith(("🚨", "⚠️")) else "alerta-info"


def _montar_card_leilao(r: "LeilaoScoreResult") -> str:
    l = r.listing
    fonte_label = _FONTE_LEILAO_LABEL.get(l.fonte, l.fonte.replace("_", " ").title())
    fonte_emoji = _FONTE_LEILAO_EMOJI.get(l.fonte, "🏛️")

    if l.foto:
        foto_html = f'<img src="{_esc(l.foto)}" alt="" loading="lazy">'
    else:
        foto_html = '<div class="sem-foto">sem foto</div>'

    sit_classe = {"Desocupado": "sit-ok", "Ocupado": "sit-risco"}.get(l.situacao, "sit-desconhecida")
    sit_emoji  = {"Desocupado": "✅", "Ocupado": "⚠️"}.get(l.situacao, "❓")

    just_html = "".join(f"<li>{_esc(j)}</li>" for j in r.justificativas)
    justificativas_html = f'<ul class="justificativas">{just_html}</ul>' if just_html else ""

    alertas_html = "".join(f'<li class="{_classe_alerta(a)}">{_esc(a)}</li>' for a in r.alertas)
    alertas_bloco = f'<ul class="alertas">{alertas_html}</ul>' if alertas_html else ""

    return f"""<article class="card" data-score="{r.score}" data-preco="{l.preco or ''}" \
data-area="{l.area or ''}" data-precom2="">
  <div class="card-foto">{foto_html}</div>
  <div class="card-corpo">
    <div class="card-titulo">{fonte_emoji} {_esc(l.titulo)} <span class="fonte-badge">{_esc(fonte_label)}</span></div>
    <div class="card-local">📍 {_esc(l.bairro)}, {_esc(l.cidade)}</div>
    <div class="card-stats">
      <span><b>{_fmt_brl(l.preco)}</b></span>
      <span>{_fmt_m2(l.area)}</span>
      <span>🛏 {l.quartos if l.quartos is not None else '?'}q</span>
    </div>
    <div class="card-stats secundario">
      <span>📋 Aval: {_fmt_brl(l.avaliacao)}</span>
      <span>📉 Desconto: {l.desconto_pct:.1f}%</span>
      <span>{_esc(l.modalidade)}</span>
    </div>
    <div class="card-situacao {sit_classe}">{sit_emoji} {_esc(l.situacao)}</div>
    <div class="card-score">
      <span class="score-badge {_tier_score(r.score)}">⭐ {r.score:.1f}/100</span>
    </div>
    {_montar_breakdown(r.breakdown, _CRITERIOS_LEILAO)}
    {justificativas_html}
    {alertas_bloco}
    <a class="card-link" href="{_esc(l.url_edital)}" target="_blank" rel="noopener noreferrer">
      Ver edital ({_esc(fonte_label)}) →
    </a>
  </div>
</article>
"""


# ── Helpers ───────────────────────────────────────────────────

def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _fmt_brl(v) -> str:
    return f"R${v:,.0f}" if v else "—"


def _fmt_m2(v) -> str:
    return f"{v:.0f}m²" if v else "—"


def _tier_score(score: float) -> str:
    if score >= 80:
        return "tier-alto"
    if score >= 65:
        return "tier-medio"
    return "tier-base"


def _slugify(nome: str) -> str:
    """'São Paulo - Capital' → 'sao-paulo-capital' (id ASCII pro data-tab)."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ASCII", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sem_acento.lower())).strip("-") or "regiao"


# ── CSS / JS ──────────────────────────────────────────────────

_CSS = """
:root {
  --bg: #f4f5f7; --fg: #1c1e21; --muted: #6b7280; --card-bg: #ffffff;
  --border: #e5e7eb; --accent: #2563eb; --danger: #dc2626; --sem-foto-bg: #eef0f3;
  --tier-alto: #16a34a; --tier-medio: #d97706; --tier-base: #64748b;
  --flip-bom-bg: #f0fdf4; --flip-apertado-bg: #fffbeb; --flip-ruim-bg: #fef2f2;
}
/* Modo escuro: segue a preferência do sistema por padrão; o botão no
   cabeçalho força data-theme="dark"/"light", sobrepondo o sistema. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --card-bg: #1a1d23;
    --border: #2a2e37; --accent: #3b82f6; --danger: #f87171; --sem-foto-bg: #23262e;
    --tier-alto: #22c55e; --tier-medio: #f59e0b; --tier-base: #94a3b8;
    --flip-bom-bg: rgba(34,197,94,0.12); --flip-apertado-bg: rgba(245,158,11,0.12);
    --flip-ruim-bg: rgba(248,113,113,0.14);
  }
}
:root[data-theme="dark"] {
  --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --card-bg: #1a1d23;
  --border: #2a2e37; --accent: #3b82f6; --danger: #f87171; --sem-foto-bg: #23262e;
  --tier-alto: #22c55e; --tier-medio: #f59e0b; --tier-base: #94a3b8;
  --flip-bom-bg: rgba(34,197,94,0.12); --flip-apertado-bg: rgba(245,158,11,0.12);
  --flip-ruim-bg: rgba(248,113,113,0.14);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
       background: var(--bg); color: var(--fg); }
header { padding: 0.85rem 1.5rem; background: var(--card-bg); border-bottom: 1px solid var(--border); }
.header-top { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 1rem; }
header h1 { margin: 0; font-size: 1.25rem; white-space: nowrap; }
.header-actions { display: flex; align-items: center; gap: 0.5rem; margin-left: auto; }
.header-meta { margin-top: 0.3rem; color: var(--muted); font-size: 0.76rem; line-height: 1.4; }
.theme-toggle { border: 1px solid var(--border); background: var(--card-bg);
                border-radius: 999px; width: 2.2rem; height: 2.2rem; font-size: 1.1rem;
                cursor: pointer; line-height: 1; flex-shrink: 0; }
.theme-toggle:hover { background: var(--bg); }
.btn-rodar { border: 1px solid var(--accent); background: var(--accent);
             color: #fff; border-radius: 999px; padding: 0 1rem; height: 2.2rem; font-size: 0.85rem;
             font-weight: 600; cursor: pointer; white-space: nowrap; }
.btn-rodar:hover { filter: brightness(1.08); }
.btn-rodar:disabled { opacity: 0.6; cursor: default; }
.btn-config { border: 1px solid var(--border); background: var(--card-bg);
              border-radius: 999px; width: 2.2rem; height: 2.2rem; font-size: 1.1rem;
              cursor: pointer; line-height: 1; }
.btn-config:hover { background: var(--bg); }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100;
                 display: flex; align-items: center; justify-content: center; padding: 1rem; }
.modal { background: var(--card-bg); color: var(--fg); border: 1px solid var(--border);
         border-radius: 12px; padding: 1.5rem; max-width: 420px; width: 100%;
         display: flex; flex-direction: column; gap: 0.9rem; }
.modal h2 { margin: 0; font-size: 1.1rem; }
.modal-hint { margin: 0; font-size: 0.82rem; color: var(--muted); line-height: 1.5; }
.modal label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.82rem; color: var(--muted); }
.modal input[type=password], .modal select { padding: 0.5rem 0.6rem; border: 1px solid var(--border);
              border-radius: 6px; font-size: 0.9rem; color: var(--fg); background: var(--bg); }
.modal-checkbox { flex-direction: row !important; align-items: center; gap: 0.5rem !important; }
.modal-checkbox input { width: auto; }
.modal-botoes { display: flex; gap: 0.6rem; justify-content: flex-end; }
.modal-botoes button { padding: 0.45rem 1rem; border: 1px solid var(--border); border-radius: 6px;
                        background: var(--bg); color: var(--fg); cursor: pointer; font-size: 0.85rem; }
.modal-botoes button:first-child { background: var(--accent); border-color: var(--accent); color: #fff; }
main { padding: 1.25rem 1.5rem 3rem; max-width: 1400px; margin: 0 auto; }
.vazio { text-align: center; padding: 4rem 1rem; color: var(--muted); font-size: 1.1rem; }

.controles { margin-bottom: 1rem; background: var(--card-bg); border: 1px solid var(--border);
             border-radius: 10px; }
.controles summary { list-style: none; cursor: pointer; padding: 0.65rem 1rem; font-size: 0.85rem;
                      font-weight: 600; color: var(--fg); display: flex; align-items: center; gap: 0.4rem; }
.controles summary::-webkit-details-marker { display: none; }
.controles summary::after { content: "▾"; margin-left: auto; color: var(--muted); }
.controles[open] summary::after { content: "▴"; }
.controles[open] summary { border-bottom: 1px solid var(--border); }
.filtros-badge { background: var(--accent); color: #fff; border-radius: 999px; font-size: 0.72rem;
                  font-weight: 700; padding: 0.05rem 0.45rem; }
.controles-corpo { display: flex; flex-wrap: wrap; align-items: end; gap: 1rem; padding: 0.9rem 1rem; }
.controles-corpo label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.78rem;
                    color: var(--muted); }
.controles-corpo select, .controles-corpo input { padding: 0.35rem 0.5rem; border: 1px solid var(--border);
                                       border-radius: 6px; font-size: 0.85rem; color: var(--fg);
                                       background: var(--card-bg); }
.controles-corpo input[type=number] { width: 6.5rem; }
.controles-corpo button { padding: 0.4rem 0.9rem; border: 1px solid var(--border); border-radius: 6px;
                     background: var(--card-bg); cursor: pointer; font-size: 0.82rem; color: var(--fg);
                     align-self: flex-end; }
.controles-corpo button:hover { background: var(--bg); }

.tier-nav { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 1.25rem;
            border-bottom: 2px solid var(--border); padding-bottom: 0.75rem; }
.tier-btn { padding: 0.55rem 1.3rem; border: none; border-radius: 999px;
            background: var(--card-bg); border: 1px solid var(--border); cursor: pointer;
            font-size: 1rem; font-weight: 600; color: var(--fg); }
.tier-btn.active { background: var(--fg); color: var(--bg); border-color: var(--fg); }

.tab-nav { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1.25rem; }
.tab-btn { padding: 0.5rem 1rem; border: 1px solid var(--border); border-radius: 8px;
           background: var(--card-bg); cursor: pointer; font-size: 0.9rem; color: var(--fg); }
.tab-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

.tab-panel { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
             gap: 1rem; }

.card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px;
        overflow: hidden; display: flex; flex-direction: column; }
.card-foto img { width: 100%; height: 170px; object-fit: cover; display: block; }
.card-foto .sem-foto { width: 100%; height: 170px; display: flex; align-items: center;
                        justify-content: center; background: var(--sem-foto-bg); color: var(--muted);
                        font-size: 0.85rem; }
.card-corpo { padding: 0.9rem 1rem 1.1rem; display: flex; flex-direction: column; gap: 0.5rem; }
.card-titulo { font-weight: 600; font-size: 0.98rem; line-height: 1.3; }
.card-local { color: var(--muted); font-size: 0.85rem; }
.card-stats { display: flex; flex-wrap: wrap; gap: 0.6rem; font-size: 0.9rem; }
.card-stats.secundario { color: var(--muted); font-size: 0.82rem; }
.card-delta { font-size: 0.82rem; font-weight: 600; }
.card-delta.delta-baixo  { color: var(--tier-alto); }
.card-delta.delta-alto   { color: var(--danger); }
.card-delta.delta-neutro { color: var(--muted); }
.card-score { margin-top: 0.2rem; }
.score-badge { display: inline-block; padding: 0.25rem 0.6rem; border-radius: 999px;
               color: #fff; font-weight: 600; font-size: 0.85rem; }
.score-badge.tier-alto  { background: var(--tier-alto); }
.score-badge.tier-medio { background: var(--tier-medio); }
.score-badge.tier-base  { background: var(--tier-base); }

.breakdown, .justificativas, .alertas { list-style: none; margin: 0; padding: 0.6rem 0 0;
                               border-top: 1px dashed var(--border); font-size: 0.82rem; }
.breakdown li { display: flex; justify-content: space-between; padding: 0.1rem 0; color: var(--muted); }
.justificativas { display: flex; flex-direction: column; gap: 0.2rem; }
.justificativas li { color: var(--fg); }
.alertas { display: flex; flex-direction: column; gap: 0.3rem; border-top-style: solid;
           border-top-color: var(--danger); }
.alertas li { font-weight: 500; }
.alertas li.alerta-risco { color: var(--danger); }
.alertas li.alerta-info  { color: var(--tier-alto); }

.fonte-badge { display: inline-block; margin-left: 0.4rem; padding: 0.1rem 0.5rem;
               border-radius: 999px; background: var(--bg); border: 1px solid var(--border);
               color: var(--muted); font-size: 0.72rem; font-weight: 600; vertical-align: middle; }
.card-situacao { font-size: 0.85rem; font-weight: 600; }
.card-situacao.sit-ok          { color: var(--tier-alto); }
.card-situacao.sit-risco       { color: var(--danger); }
.card-situacao.sit-desconhecida { color: var(--tier-medio); }

.flip-box { margin-top: 0.2rem; padding: 0.6rem 0.7rem; border-radius: 8px; font-size: 0.8rem;
            border: 1px solid var(--border); background: var(--card-bg); }
.flip-titulo { font-weight: 700; margin-bottom: 0.3rem; }
.flip-linha { color: var(--muted); line-height: 1.5; }
.flip-linha b { color: var(--fg); }
.flip-box.flip-bom      { border-color: var(--tier-alto); background: var(--flip-bom-bg); }
.flip-box.flip-apertado { border-color: var(--tier-medio); background: var(--flip-apertado-bg); }
.flip-box.flip-ruim     { border-color: var(--danger); background: var(--flip-ruim-bg); }

.card-link { margin-top: 0.4rem; color: var(--accent); font-weight: 600; font-size: 0.88rem;
             text-decoration: none; }
.card-link:hover { text-decoration: underline; }

/* Celular: cabeçalho e área útil mais compactos, um card por linha
   (já cai sozinho via minmax do grid, isso só ajusta respiro/fonte) */
@media (max-width: 480px) {
  header { padding: 0.75rem 1rem; }
  header h1 { font-size: 1.1rem; }
  main { padding: 0.85rem 1rem 2rem; }
  .controles-corpo { padding: 0.75rem; gap: 0.75rem 1rem; }
  .controles-corpo label, .controles-corpo button { width: 100%; }
  .controles-corpo input[type=number] { width: 100%; }
  .tab-panel { grid-template-columns: 1fr; }
}
"""

_JS = """
function mostrarTier(slug) {
  document.querySelectorAll('.tier-panel').forEach(function(p) {
    p.style.display = (p.dataset.tier === slug) ? 'block' : 'none';
  });
  document.querySelectorAll('.tier-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.tier === slug);
  });
}

function mostrarTab(slug) {
  document.querySelectorAll('.tab-panel').forEach(function(p) {
    p.style.display = (p.dataset.tab === slug) ? 'grid' : 'none';
  });
  document.querySelectorAll('.tab-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.tab === slug);
  });
}

var _ORDENS = {
  'score-desc':  ['score', -1], 'score-asc':   ['score', 1],
  'preco-asc':   ['preco', 1],  'preco-desc':  ['preco', -1],
  'area-desc':   ['area', -1],  'area-asc':    ['area', 1],
  'precom2-asc': ['precom2', 1], 'precom2-desc': ['precom2', -1],
};

function atualizar() {
  var precoMin = parseFloat(document.getElementById('preco-min').value);
  var precoMax = parseFloat(document.getElementById('preco-max').value);
  var areaMin  = parseFloat(document.getElementById('area-min').value);
  var areaMax  = parseFloat(document.getElementById('area-max').value);
  var ordem    = _ORDENS[document.getElementById('ordenar').value] || _ORDENS['score-desc'];

  var ativos = [precoMin, precoMax, areaMin, areaMax].filter(function(v) { return !isNaN(v); }).length;
  var badge = document.getElementById('filtros-badge');
  if (badge) {
    badge.textContent = ativos;
    badge.hidden = ativos === 0;
  }

  document.querySelectorAll('.tab-panel').forEach(function(painel) {
    var cards = Array.prototype.slice.call(painel.querySelectorAll('.card'));

    cards.forEach(function(card) {
      var preco = parseFloat(card.dataset.preco);
      var area  = parseFloat(card.dataset.area);
      var visivel = true;
      if (!isNaN(precoMin) && preco < precoMin) visivel = false;
      if (!isNaN(precoMax) && preco > precoMax) visivel = false;
      if (!isNaN(areaMin)  && area  < areaMin)  visivel = false;
      if (!isNaN(areaMax)  && area  > areaMax)  visivel = false;
      card.style.display = visivel ? '' : 'none';
    });

    cards.sort(function(a, b) {
      var va = parseFloat(a.dataset[ordem[0]]);
      var vb = parseFloat(b.dataset[ordem[0]]);
      return (va - vb) * ordem[1];
    });
    cards.forEach(function(card) { painel.appendChild(card); });
  });
}

function limparFiltros() {
  document.getElementById('preco-min').value = '';
  document.getElementById('preco-max').value = '';
  document.getElementById('area-min').value = '';
  document.getElementById('area-max').value = '';
  document.getElementById('ordenar').value = 'score-desc';
  atualizar();
}

function alternarTema() {
  var atual = document.documentElement.getAttribute('data-theme');
  var escuroAgora = atual === 'dark'
    || (!atual && window.matchMedia('(prefers-color-scheme: dark)').matches);
  var novo = escuroAgora ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', novo);
  try { localStorage.setItem('imovel-bot-tema', novo); } catch (e) {}
  atualizarIconeTema();
}

function atualizarIconeTema() {
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  var atual = document.documentElement.getAttribute('data-theme');
  var escuro = atual === 'dark'
    || (!atual && window.matchMedia('(prefers-color-scheme: dark)').matches);
  btn.textContent = escuro ? '☀️' : '🌙';
}

atualizarIconeTema();

// ── Controle remoto (dispara o workflow do GitHub Actions do celular) ──
// Repositório é público — o nome não é segredo. O que protege o disparo
// é o token (PAT) do próprio usuário, colado só uma vez e guardado só
// no localStorage deste navegador (nunca embutido nesta página).
var GITHUB_REPO     = 'PedroZak/imovel-bot';
var GITHUB_WORKFLOW = 'rodar-bot.yml';

function abrirConfig() {
  try {
    document.getElementById('cfg-token').value = localStorage.getItem('imovel-bot-pat') || '';
    document.getElementById('cfg-tier').value = localStorage.getItem('imovel-bot-tier') || '1';
  } catch (e) {}
  document.getElementById('modal-config').style.display = 'flex';
}

function fecharConfig() {
  document.getElementById('modal-config').style.display = 'none';
}

function salvarConfig() {
  var token = document.getElementById('cfg-token').value.trim();
  var tier  = document.getElementById('cfg-tier').value;
  try {
    if (token) localStorage.setItem('imovel-bot-pat', token);
    localStorage.setItem('imovel-bot-tier', tier);
  } catch (e) {}
  fecharConfig();
}

function rodarAgora() {
  var token = null;
  try { token = localStorage.getItem('imovel-bot-pat'); } catch (e) {}
  if (!token) { abrirConfig(); return; }

  var tier = '1';
  try { tier = localStorage.getItem('imovel-bot-tier') || '1'; } catch (e) {}

  var btn = document.getElementById('btn-rodar');
  btn.disabled = true;
  var textoOriginal = btn.textContent;
  btn.textContent = '⏳ Disparando...';

  fetch('https://api.github.com/repos/' + GITHUB_REPO + '/actions/workflows/' + GITHUB_WORKFLOW + '/dispatches', {
    method: 'POST',
    headers: {
      'Authorization': 'Bearer ' + token,
      'Accept': 'application/vnd.github+json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      ref: 'main',
      inputs: { tier: tier },
    }),
  }).then(function(res) {
    if (res.status === 204) {
      btn.textContent = '✅ Disparado!';
      setTimeout(function() { window.open('https://github.com/' + GITHUB_REPO + '/actions', '_blank'); }, 500);
    } else {
      return res.text().then(function(txt) { throw new Error(res.status + ': ' + txt); });
    }
  }).catch(function(e) {
    btn.textContent = '❌ Erro';
    alert('Falha ao disparar: ' + e.message + '\\n\\nConfira se o token ainda é válido e tem permissão \"Actions: Read and write\" neste repositório.');
  }).finally(function() {
    setTimeout(function() { btn.disabled = false; btn.textContent = textoOriginal; }, 4000);
  });
}
"""

# Roda no <head>, antes do <style> — aplica o tema salvo (se houver)
# no <html> ANTES da primeira pintura, pra não piscar claro->escuro.
_JS_TEMA_INICIAL = """
(function() {
  try {
    var salvo = localStorage.getItem('imovel-bot-tema');
    if (salvo) document.documentElement.setAttribute('data-theme', salvo);
  } catch (e) {}
})();
"""
