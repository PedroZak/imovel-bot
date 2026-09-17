"""
scorers/leilao.py
─────────────────
Scorer de leilões (Tier 3 — ON HOLD). Score 0–100. Fonte-agnóstico —
roda em cima de qualquer LeilaoListing, venha da Caixa
(scrapers/caixa_leilao.py) ou da Resale (scrapers/resale.py).

Situação de ocupação (Ocupado/Desocupado) só é filtro hard quando a
fonte de fato informa isso. A Caixa parou de publicar esse dado
(confirmado ao vivo em set/2026 — ver docstring de
scrapers/caixa_leilao.py) e sempre retorna situacao="Desconhecida";
pra esses casos o risco vira alerta obrigatório em vez de filtro (quem
recebe a notificação PRECISA abrir o edital antes de dar lance). Já a
Resale expõe ocupação real via tags do próprio anúncio — quando
situacao é "Ocupado"/"Desocupado" (não "Desconhecida"), volta a valer
como catraca eliminatória, igual o design original antes da Caixa
perder esse dado.

Revisão de filtros (12/09/2026, pedido do usuário):
  - `possui_contencioso` (só Resale informa) virou filtro hard — antes
    só gerava alerta, mas ação judicial em andamento é exatamente o
    tipo de risco jurídico que o Tier 3 existe pra evitar, mesmo tipo
    de raciocínio do filtro de ocupação.
  - `preco_max` — sem teto, um imóvel de R$2M com bom desconto passava
    mesmo fora do orçamento do usuário (~R$500k, ver CLAUDE.md).
  - `area_min` foi cogitado (mercado tem, leilão não) mas descartado —
    usuário decidiu manter qualquer metragem em leilão (13/09/2026). O
    critério de pontuação `_criterio_metragem` (não elimina, só pontua
    menos) continua sendo suficiente pra isso.

Critérios:
  A. Desconto vs avaliação   → até 70 pts  (critério quase único)
  B. Modalidade              → até 20 pts
  C. Metragem                → até 10 pts
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from scrapers.base import LeilaoListing
from scorers.base  import LeilaoScoreResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 1. FILTROS HARD
# ─────────────────────────────────────────────────────────────

_PRECO_MAX_PADRAO = 500_000   # mesmo orçamento do usuário no Tier 1, ver CLAUDE.md


def _filtros_hard(listing: LeilaoListing, lc: dict) -> Optional[str]:
    """
    Retorna motivo de reprovação ou None se passou.
    Ocupação só filtra quando a fonte de fato sabe informar isso
    (situacao != "Desconhecida") — ver docstring do módulo.
    """
    if listing.situacao == "Ocupado":
        return (
            "Imóvel OCUPADO — risco jurídico de reintegração de posse "
            "e capital travado durante processo"
        )

    if listing.possui_contencioso:
        return (
            "Imóvel com AÇÃO JUDICIAL em andamento — risco jurídico "
            "adicional além do processo de leilão em si"
        )

    preco_max = lc.get("preco_max", _PRECO_MAX_PADRAO)
    if listing.preco > preco_max:
        return f"Preço R${listing.preco:,.0f} acima do teto R${preco_max:,.0f}"

    if listing.desconto_pct < lc.get("desconto_minimo", 25):
        return (
            f"Desconto {listing.desconto_pct:.1f}% abaixo do mínimo "
            f"configurado ({lc.get('desconto_minimo', 25)}%)"
        )

    return None


# ─────────────────────────────────────────────────────────────
# 2. CRITÉRIOS DE SCORING
# ─────────────────────────────────────────────────────────────

def _criterio_desconto(desconto_pct: float) -> Tuple[float, str]:
    """
    A. Desconto vs avaliação Caixa (70 pts).
    Aumentado de 50 para 70 após ocupação virar filtro hard.
    """
    d = desconto_pct
    if   d >= 50: return 70, f"🔥 Margem excelente: {d:.1f}% de desconto (+70)"
    elif d >= 40: return 50, f"💰 Margem ótima: {d:.1f}% de desconto (+50)"
    elif d >= 30: return 35, f"✅ Margem boa: {d:.1f}% de desconto (+35)"
    elif d >= 20: return 20, f"⚖️ Margem aceitável: {d:.1f}% de desconto (+20)"
    else:         return  0, f"❌ Desconto {d:.1f}% insuficiente (0)"


def _criterio_modalidade(modalidade: str) -> Tuple[float, str]:
    """B. Modalidade do leilão (20 pts)."""
    m = modalidade.lower()
    if   "venda direta" in m or "2º leilão" in m:
        return 20, f"📜 {modalidade} — maior desconto garantido (+20)"
    elif "licitação" in m:
        return 12, f"📜 {modalidade} — disputa livre (+12)"
    else:
        return  5, f"📜 {modalidade} (+5)"


def _criterio_metragem(area: Optional[float]) -> Tuple[float, str]:
    """C. Metragem (10 pts)."""
    if not area:
        return 3, "📐 Metragem não informada (+3)"
    if   area >= 80: return 10, f"📐 {area:.0f}m² — amplo (+10)"
    elif area >= 60: return  7, f"📐 {area:.0f}m² (+7)"
    elif area >= 40: return  4, f"📐 {area:.0f}m² — compacto (+4)"
    else:            return  1, f"📐 {area:.0f}m² — muito pequeno (+1)"


def _gerar_alertas(listing: LeilaoListing) -> list[str]:
    """Alertas relevantes para o arrematante."""
    alertas = []

    if listing.situacao == "Desconhecida":
        alertas.append(
            "🚨 Situação de ocupação NÃO disponível via scraping — abra "
            "o edital e confirme se o imóvel está desocupado ANTES de "
            "dar lance (imóvel ocupado = risco jurídico de reintegração "
            "de posse e capital travado)."
        )
    elif listing.situacao == "Desocupado":
        alertas.append("🔑 Desocupado — confirmado pela fonte")

    if listing.modalidade == "1º Leilão":
        alertas.append("ℹ️ 1º Leilão — lance mínimo = valor de avaliação")

    if listing.debitos_condominio and listing.debitos_condominio > 0:
        alertas.append(
            f"⚠️ Débito de condomínio: R${listing.debitos_condominio:,.0f} "
            "(pode ser herdado pelo arrematante — verificar edital)"
        )

    if listing.possui_dividas:
        alertas.append(
            "⚠️ Possui dívidas (IPTU/condomínio) — verificar valor e "
            "responsabilidade antes de dar lance"
        )

    # possui_contencioso NÃO é mais alerta — virou filtro hard em
    # _filtros_hard (ver docstring do módulo), então quem chega até
    # aqui já passou por essa checagem.

    return alertas


# ─────────────────────────────────────────────────────────────
# 3. ORQUESTRADOR
# ─────────────────────────────────────────────────────────────

def avaliar(listing: LeilaoListing, cfg: dict) -> LeilaoScoreResult:
    lc        = cfg.get("leilao", {})
    score_min = lc.get("score_minimo", 50)

    motivo = _filtros_hard(listing, lc)
    if motivo:
        return LeilaoScoreResult(
            listing=listing, score=0, breakdown={},
            justificativas=[f"❌ {motivo}"],
            passou=False, motivo_reprovacao=motivo,
        )

    score = 0.0
    bd:    dict      = {}
    just:  list[str] = []

    def _add(key: str, pts: float, txt: str):
        nonlocal score
        score  += pts
        bd[key] = round(pts, 1)
        just.append(txt)

    pts, txt = _criterio_desconto(listing.desconto_pct)
    _add("desconto", pts, txt)

    pts, txt = _criterio_modalidade(listing.modalidade)
    _add("modalidade", pts, txt)

    pts, txt = _criterio_metragem(listing.area)
    _add("area", pts, txt)

    score   = min(round(score, 1), 100)
    alertas = _gerar_alertas(listing)
    passou  = score >= score_min

    return LeilaoScoreResult(
        listing=listing, score=score, breakdown=bd,
        justificativas=just, passou=passou, alertas=alertas,
    )


def filtrar_e_ordenar(
    listings: list[LeilaoListing], cfg: dict
) -> list[LeilaoScoreResult]:
    resultados = [avaliar(l, cfg) for l in listings]
    aprovados  = sorted(
        [r for r in resultados if r.passou],
        key=lambda r: r.score, reverse=True,
    )
    logger.info(
        f"Scorer leilão: {len(listings)} avaliados → "
        f"{len(aprovados)} aprovados (score ≥ {cfg.get('leilao', {}).get('score_minimo', 50)})"
    )
    return aprovados
