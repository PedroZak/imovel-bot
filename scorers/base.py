"""
scorers/base.py
───────────────
Tipos de retorno compartilhados pelos scorers.
Separados aqui para que notifier/dashboard.py importe sem circular.
"""

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scrapers.base import Listing, LeilaoListing


@dataclass
class ScoreResult:
    """Resultado do scorer de mercado."""
    listing:            "Listing"
    score:              float
    breakdown:          dict
    justificativas:     list[str]
    passou:             bool
    motivo_reprovacao:  Optional[str] = None

    # Delta vs referência do bairro (preenchido só quando passou dos
    # filtros hard — ver scorers/mercado.py:avaliar()). desconto_pct
    # positivo = preço/m² abaixo da mediana do bairro (bom negócio);
    # negativo = acima. referencia_m2 é a mediana de venda real (ITBI,
    # não preço de anúncio) usada como base da comparação.
    desconto_pct:       Optional[float] = None
    referencia_m2:      Optional[float] = None

    # Análise de flip (compra + reforma + venda) — ver scorers/flip.py.
    # None quando não dá pra estimar com segurança (falta quartos,
    # banheiros ou referência de bairro).
    flip:               Optional["FlipResult"] = None


@dataclass
class FlipResult:
    """
    Estimativa de negócio de flip (compra, reforma padrão média, venda)
    pra um ScoreResult já aprovado. Ver scorers/flip.py:calcular().

    Todo custo de reforma aqui é ESTIMATIVA — não vem de nenhuma fonte
    de dado real, é um modelo simples por cômodo configurado em
    config.yaml:reforma (curado à mão, ajuste pelos seus números reais
    de obra). valor_venda_esperado usa a MEDIANA de venda real do
    bairro (mesma referência do desconto_pct) como piso conservador —
    um imóvel reformado tende a vender ACIMA da mediana (que mistura
    unidades reformadas e não-reformadas), então isso é uma margem de
    segurança, não uma previsão otimista.

    custo_manutencao_estimado cobre condomínio+IPTU durante os meses
    de reforma + meses até vender (config.yaml:reforma.meses_reforma_
    estimado/meses_venda_estimado) — NÃO temos dado real de prazo médio
    de venda por bairro (nem Atlas nem os scrapers têm isso hoje), então
    esses meses são estimativa manual, igual o custo de reforma.

    margem_pct NÃO desconta custos de transação (ITBI, corretagem,
    imposto de renda sobre ganho de capital) — o limiar de "bom
    negócio" em config.yaml:reforma.margem_minima_pct deve ser generoso
    o bastante pra cobrir isso.
    """
    quartos:                  int
    banheiros:                int
    custo_reforma_estimado:   float
    meses_reforma:            float
    meses_venda:              float
    custo_manutencao_estimado: float  # condomínio+IPTU × (meses_reforma + meses_venda)
    valor_venda_esperado:     float
    custo_total:              float   # compra + reforma + manutenção durante o período
    margem_estimada:          float   # valor_venda_esperado - custo_total
    margem_pct:               float
    veredito:                 str     # "bom_negocio" | "margem_apertada" | "nao_compensa"


@dataclass
class LeilaoScoreResult:
    """Resultado do scorer de leilão."""
    listing:            "LeilaoListing"
    score:              float
    breakdown:          dict
    justificativas:     list[str]
    passou:             bool
    alertas:            list[str]      = field(default_factory=list)
    motivo_reprovacao:  Optional[str]  = None
