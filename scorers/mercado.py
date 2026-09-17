"""
scorers/mercado.py
──────────────────
Scorer de imóveis de mercado (Tier 1: OLX, ZAP, VivaReal).
Score 0–100. Notifica apenas se score >= config.mercado.score_minimo.

Cada critério é uma função isolada → fácil ajustar peso sem tocar nos outros.

Critérios:
  A. Preço/m² vs referência do bairro     → até 35 pts
  B. Yield líquido vs threshold dinâmico  → até 25 pts
  C. Walkability (bônus empilhável)       → até 8 pts
  D. Custo de condomínio                  → até 20 pts
  E. Bairro alvo                          → até 10 pts
  F. Metragem                             → até 10 pts
  G. Bônus: distresse, tempo, vaga        → até 20 pts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from scrapers.base import Listing
from scorers.base  import ScoreResult
from scorers       import flip as _flip
from utils.geo     import eh_caminhavel
from utils.text    import contem_distresse
from utils.bairro  import normalizar as _normalizar
from utils.bairro  import resolver_bairro as _resolver_bairro
from utils.bairro  import texto_localizacao as _texto_localizacao

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# TIPO INTERNO: métricas pré-calculadas
# ─────────────────────────────────────────────────────────────

@dataclass
class _Metricas:
    preco_m2:           float
    aluguel_estimado:   float
    carry_mensal:       float
    yield_liq:          float
    yield_min_dinamico: float
    desconto_pct:       float
    bairro_key:         str
    referencia_m2:      float  # mediana de venda real (ITBI) do bairro, R$/m²


# ─────────────────────────────────────────────────────────────
# 1. FILTROS HARD
# ─────────────────────────────────────────────────────────────

def _filtros_hard(listing: Listing, cfg: dict, refs: dict) -> Optional[str]:
    """
    Retorna motivo de reprovação (str) ou None se passou.
    Qualquer reprovação aqui encerra o scoring com score=0.
    """
    mc = cfg["mercado"]

    # Cidades excluídas — configurável por região (config.yaml). Sem
    # isso, banir "campinas" globalmente quebraria a própria região
    # de Campinas quando multi_regiao estiver ativo.
    #
    # IMPORTANTE: inclui listing.cidade explicitamente no texto de
    # busca, não só _texto_localizacao(listing) — essa função retorna
    # SÓ o bairro quando ele está preenchido (ver utils/bairro.py), o
    # que fazia um anúncio com bairro="Paraíso" e cidade="Santo André"
    # passar batido pelo filtro (bug real encontrado via o dashboard:
    # card aprovado mostrando "Paraíso, Santo André" — a cidade nunca
    # era checada porque o bairro sozinho não contém "santo andre").
    texto_busca = _normalizar(
        _texto_localizacao(listing) + " " + listing.cidade + " " + listing.url
    )
    cidades_excluidas = mc.get("cidades_excluidas", [])
    for cidade in cidades_excluidas:
        if _normalizar(cidade) in texto_busca:
            return f"Imóvel fora da região-alvo (match: '{cidade}')"

    if not listing.preco:
        return "Preço não informado — impossível avaliar com segurança"

    if listing.preco > mc["preco_max"]:
        return f"Preço R${listing.preco:,.0f} acima do teto R${mc['preco_max']:,.0f}"

    if mc.get("preco_min") and listing.preco < mc["preco_min"]:
        return f"Preço R${listing.preco:,.0f} abaixo do mínimo R${mc['preco_min']:,.0f}"

    if listing.condominio and listing.condominio > mc["condominio_max"]:
        return f"Condomínio R${listing.condominio:,.0f} acima de R${mc['condominio_max']:,.0f}"

    if listing.area and listing.area < mc["area_min"]:
        return f"Área {listing.area:.0f}m² abaixo do mínimo {mc['area_min']}m²"

    if not listing.area:
        return "Área não informada — impossível calcular preço/m² com segurança"

    if listing.quartos is not None and listing.quartos < mc["quartos_min"]:
        return f"Studio eliminado ({listing.quartos} quarto)"

    if not _resolver_bairro(_texto_localizacao(listing), refs):
        return f"Bairro '{listing.bairro or '(vazio)'}' fora do radar"

    return None


# ─────────────────────────────────────────────────────────────
# 2. CÁLCULO DE MÉTRICAS BASE
# ─────────────────────────────────────────────────────────────

def _estimar_parcela_mensal(preco: float, mc: dict) -> float:
    """
    Estima a parcela mensal via tabela PRICE, escalando com o preço
    do imóvel. Generaliza o Cenário C (30% entrada, ~11% a.a., 360
    meses) para qualquer faixa de preço — essencial com multi-região,
    onde Piracicaba/Campinas têm preços bem menores que SP capital.
    Uma parcela fixa (ex.: R$3.190) faria o yield mínimo exigido ficar
    irrealisticamente alto em regiões mais baratas.
    """
    taxa_anual  = mc.get("financiamento_taxa_anual", 0.11)
    prazo_meses = mc.get("financiamento_prazo_meses", 360)
    entrada_pct = mc.get("financiamento_entrada_pct", 0.30)

    financiado = preco * (1 - entrada_pct)
    if financiado <= 0 or prazo_meses <= 0:
        return 0.0

    i_mensal = (1 + taxa_anual) ** (1 / 12) - 1
    if i_mensal <= 0:
        return financiado / prazo_meses

    fator = 1 - (1 + i_mensal) ** (-prazo_meses)
    return financiado * i_mensal / fator if fator > 0 else 0.0


def _calcular_metricas(listing: Listing, cfg: dict, refs: dict) -> _Metricas:
    mc         = cfg["mercado"]
    bairro_key = _resolver_bairro(_texto_localizacao(listing), refs)
    ref        = refs[bairro_key]

    preco = listing.preco or 0
    area  = listing.area  or 1
    cond  = listing.condominio or 0
    iptu  = listing.iptu  or 0

    preco_m2 = preco / area

    fator_area  = 1.15 if area <= 45 else (1.00 if area <= 60 else 0.88)
    aluguel_est = ref["aluguel_m2"] * area * fator_area

    carry_mensal = cond + (iptu / 12)
    yield_liq    = (aluguel_est - carry_mensal) / preco if preco else 0

    parcela   = _estimar_parcela_mensal(preco, mc)
    cobertura = mc.get("cobertura_alvo", 0.40)
    yield_min = (carry_mensal + cobertura * parcela) / preco if preco else 0

    desconto_pct = (ref["compra_m2"] - preco_m2) / ref["compra_m2"]

    return _Metricas(
        preco_m2=preco_m2, aluguel_estimado=aluguel_est, carry_mensal=carry_mensal,
        yield_liq=yield_liq, yield_min_dinamico=yield_min,
        desconto_pct=desconto_pct, bairro_key=bairro_key,
        referencia_m2=ref["compra_m2"],
    )


# ─────────────────────────────────────────────────────────────
# 3. CRITÉRIOS DE SCORING
# ─────────────────────────────────────────────────────────────

def _criterio_preco(m: _Metricas) -> Tuple[float, str]:
    """A. Preço/m² vs referência do bairro (35 pts)."""
    d = m.desconto_pct
    if   d >= 0.25: return 35, "🔥 Preço/m² 25%+ abaixo da média (+35)"
    elif d >= 0.15: return 25, "💰 Preço/m² 15%+ abaixo da média (+25)"
    elif d >= 0.05: return 15, "✅ Preço/m² 5%+ abaixo da média (+15)"
    elif d >= 0:    return  5, "⚖️ Na média do bairro (+5)"
    else:           return  0, f"📉 {abs(d)*100:.0f}% acima da média (0)"


def _criterio_yield(m: _Metricas, cfg: dict) -> Tuple[float, str]:
    """B. Yield líquido vs threshold dinâmico (25 pts)."""
    y, y_min = m.yield_liq, m.yield_min_dinamico
    y_opt    = cfg["mercado"]["yield_otimo_mensal"]

    if   y >= y_opt: return 25, f"📈 Yield ótimo {y*100:.2f}%/mês (+25)"
    elif y >= y_min: return 15, f"📊 Yield aceitável {y*100:.2f}%/mês (+15)"
    else:            return  0, f"⚠️ Yield fraco {y*100:.2f}%/mês (0)"


def _criterio_walkability(listing: Listing, cfg: dict) -> Tuple[float, str]:
    """C. Bônus empilhável — caminhável ao escritório (8 pts)."""
    dist_max   = cfg.get("geo", {}).get("distancia_max_km", 3.0)
    caminhavel = eh_caminhavel(listing.__dict__, cfg["db_path"], dist_max)

    if caminhavel is True:
        return 8, "🚶 Caminhável ao escritório (+8)"
    if caminhavel is None:
        return 0, "📍 Localização não determinada (sem bônus)"
    return 0, ""


def _criterio_condominio(listing: Listing) -> Tuple[float, str]:
    """
    D. Custo de condomínio (20 pts).
    Condomínio ausente é tratado como neutro (não elimina o anúncio —
    muitos anúncios legítimos só não publicam esse dado).

    OLX/ZAP costumam exibir "R$ 1" quando o anunciante não preencheu
    o campo (placeholder do próprio site, não um valor real) — sem
    esse tratamento, o scorer pontuava isso como "condomínio
    excelente" (+20), quando deveria ser neutro. c <= 1 é tratado
    como não informado, igual a None.
    """
    if listing.condominio is None or listing.condominio <= 1:
        return 8, "🏢 Condomínio não informado (+8, neutro)"
    c = listing.condominio
    if   c <= 600: return 20, f"🏢 Condomínio excelente R${c:.0f} (+20)"
    elif c <= 800: return 12, f"🏢 Condomínio razoável R${c:.0f} (+12)"
    else:          return  5, f"⚠️ Condomínio próximo ao limite R${c:.0f} (+5)"


def _criterio_bairro(bairro_key: str) -> Tuple[float, str]:
    """E. Bairro alvo (10 pts — todos os da lista são primários)."""
    return 10, f"📍 {bairro_key} (+10)"


def _criterio_metragem(area: Optional[float]) -> Tuple[float, str]:
    """F. Metragem (10 pts)."""
    if not area:
        return 4, "📐 Metragem não informada (+4)"
    if   area >= 60: return 10, f"📐 {area:.0f}m² (+10)"
    elif area >= 50: return  7, f"📐 {area:.0f}m² (+7)"
    else:            return  4, f"📐 {area:.0f}m² (+4)"


def _criterio_bonus(listing: Listing) -> Tuple[float, list[str]]:
    """G. Bônus: distresse + tempo no mercado + vaga (até 20 pts)."""
    pts  = 0.0
    just: list[str] = []

    if contem_distresse(listing.descricao):
        pts += 8
        just.append("🚨 Sinal de distresse no anúncio (+8)")

    dias = listing.dias_publicado or 0
    if   dias > 90: pts += 7; just.append(f"⏳ {dias} dias anunciado — vendedor ansioso (+7)")
    elif dias > 45: pts += 3; just.append(f"⏳ {dias} dias anunciado (+3)")

    if listing.vagas > 0:
        pts += 5
        just.append(f"🚗 {listing.vagas} vaga(s) (+5)")

    return pts, just


# ─────────────────────────────────────────────────────────────
# 4. ORQUESTRADOR
# ─────────────────────────────────────────────────────────────

def avaliar(listing: Listing, cfg: dict) -> ScoreResult:
    """Avalia um Listing e retorna ScoreResult com score, breakdown e justificativas."""
    refs      = cfg["bairros_referencia"]
    score_min = cfg["mercado"]["score_minimo"]

    motivo = _filtros_hard(listing, cfg, refs)
    if motivo:
        return ScoreResult(
            listing=listing, score=0, breakdown={},
            justificativas=[f"❌ {motivo}"],
            passou=False, motivo_reprovacao=motivo,
        )

    m = _calcular_metricas(listing, cfg, refs)

    score = 0.0
    bd:   dict      = {}
    just: list[str] = []

    def _add(key: str, pts: float, txt: str):
        nonlocal score
        score += pts
        bd[key] = round(pts, 1)
        if txt:
            just.append(txt)

    pts, txt = _criterio_preco(m);                 _add("preco", pts, txt)
    pts, txt = _criterio_yield(m, cfg);             _add("yield", pts, txt)
    pts, txt = _criterio_walkability(listing, cfg); _add("walk", pts, txt)
    pts, txt = _criterio_condominio(listing);       _add("carry", pts, txt)
    pts, txt = _criterio_bairro(m.bairro_key);      _add("bairro", pts, txt)
    pts, txt = _criterio_metragem(listing.area);    _add("area", pts, txt)
    pts, txts = _criterio_bonus(listing);           _add("bonus", pts, ""); just.extend(txts)

    score = min(round(score, 1), 100)

    resultado = ScoreResult(
        listing=listing, score=score, breakdown=bd,
        justificativas=just, passou=score >= score_min,
        desconto_pct=m.desconto_pct, referencia_m2=m.referencia_m2,
    )
    resultado.flip = _flip.calcular(resultado, cfg)
    return resultado


def filtrar_e_ordenar(listings: list[Listing], cfg: dict) -> list[ScoreResult]:
    """Avalia todos, retorna apenas aprovados ordenados por score desc."""
    resultados = [avaliar(l, cfg) for l in listings]
    aprovados  = sorted(
        [r for r in resultados if r.passou],
        key=lambda r: r.score, reverse=True,
    )
    logger.info(
        f"Scorer mercado: {len(listings)} avaliados → "
        f"{len(aprovados)} aprovados (score ≥ {cfg['mercado']['score_minimo']})"
    )
    return aprovados
