"""
scorers/flip.py
────────────────
Estimativa de negócio de flip: compra, reforma padrão média, venda.

Modelo de custo de reforma por cômodo — quarto e banheiro são
cobrados por unidade (custam mais por serem itens específicos:
revestimento, louças/metais no banheiro; piso/elétrica/pintura no
quarto), cozinha e sala são fixos (todo apartamento tem exatamente 1
de cada, no modelo "padrão médio" — não tenta diferenciar cozinha
americana de cozinha fechada, por exemplo), e um custo geral por m²
cobre acabamento comum ao apartamento inteiro (piso de área comum,
pintura geral, parte elétrica/hidráulica que não é específica de
banheiro/cozinha).

Todos os valores em config.yaml:reforma são estimativa inicial —
curados à mão, precisam ser ajustados com dado real de obra do
usuário ao longo do tempo (mesmo espírito de bairros_referencia pra
Campinas/Piracicaba: começa com estimativa, refina depois).

Só calcula quando há dado suficiente pra confiar no resultado — sem
quartos, banheiros ou referência de bairro, retorna None (mesma
filosofia dos filtros hard do scorer de mercado: não adivinha dado
que não tem).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from scorers.base import FlipResult

if TYPE_CHECKING:
    from scorers.base import ScoreResult

logger = logging.getLogger(__name__)


def calcular(r: "ScoreResult", cfg: dict) -> Optional[FlipResult]:
    """
    Estima o negócio de flip pra um ScoreResult já aprovado no scorer
    de mercado. Retorna None se faltar quartos, banheiros ou a
    referência de mediana do bairro (r.referencia_m2) — sem esses três
    dados a conta não é confiável o suficiente pra mostrar.
    """
    l = r.listing
    rf = cfg.get("reforma", {})

    if l.quartos is None or l.banheiros is None or not r.referencia_m2 or not l.area or not l.preco:
        return None

    custo_reforma = (
        l.quartos * rf.get("custo_quarto", 8000)
        + l.banheiros * rf.get("custo_banheiro", 12000)
        + rf.get("custo_cozinha", 15000)
        + rf.get("custo_sala", 10000)
        + l.area * rf.get("custo_area_comum_m2", 150)
    )

    # Custo de manter o imóvel (condomínio + IPTU) durante a reforma e
    # até vender. Sem dado real de prazo de venda por bairro (nem Atlas
    # nem os scrapers têm isso), os meses são estimativa manual — ver
    # docstring do módulo e comentário em config.yaml:reforma.
    meses_reforma = rf.get("meses_reforma_estimado", 3)
    meses_venda   = rf.get("meses_venda_estimado", 6)
    carry_mensal  = (l.condominio or 0) + (l.iptu or 0) / 12
    custo_manutencao = (meses_reforma + meses_venda) * carry_mensal

    valor_venda_esperado = r.referencia_m2 * l.area
    custo_total          = l.preco + custo_reforma + custo_manutencao
    margem_estimada       = valor_venda_esperado - custo_total
    margem_pct            = margem_estimada / custo_total if custo_total else 0.0

    margem_minima = rf.get("margem_minima_pct", 0.20)
    if margem_pct >= margem_minima:
        veredito = "bom_negocio"
    elif margem_pct >= 0:
        veredito = "margem_apertada"
    else:
        veredito = "nao_compensa"

    return FlipResult(
        quartos=l.quartos, banheiros=l.banheiros,
        custo_reforma_estimado=round(custo_reforma, 2),
        meses_reforma=meses_reforma, meses_venda=meses_venda,
        custo_manutencao_estimado=round(custo_manutencao, 2),
        valor_venda_esperado=round(valor_venda_esperado, 2),
        custo_total=round(custo_total, 2),
        margem_estimada=round(margem_estimada, 2),
        margem_pct=round(margem_pct, 4),
        veredito=veredito,
    )
