"""
scrapers/base.py
────────────────
Tipos base compartilhados por todos os scrapers.
Alterar aqui reflete automaticamente em OLX, ZAP e Caixa.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    """Imóvel de mercado (OLX, ZAP, VivaReal)."""
    # Obrigatórios
    id:     str
    titulo: str
    bairro: str
    cidade: str
    url:    str
    fonte:  str

    # Preço e área
    preco:     Optional[float] = None
    area:      Optional[float] = None
    quartos:   Optional[int]   = None
    banheiros: Optional[int]   = None
    vagas:     int              = 0

    # Custos de carregamento
    condominio: Optional[float] = None
    iptu:       Optional[float] = None   # valor anual

    # Localização geográfica (para walkability)
    lat:      Optional[float] = None
    lng:      Optional[float] = None
    endereco: Optional[str]   = None

    # Extras
    descricao:      str            = ""
    fotos:          list           = field(default_factory=list)
    dias_publicado: Optional[int]  = None

    # Computado
    preco_m2: Optional[float] = field(init=False, default=None)

    def __post_init__(self):
        if self.preco and self.area and self.area > 0:
            self.preco_m2 = round(self.preco / self.area, 2)


@dataclass
class LeilaoListing:
    """Imóvel em leilão (Caixa Econômica Federal, Resale, ...)."""
    # Obrigatórios
    id:         str
    preco:      float    # lance mínimo
    avaliacao:  float    # avaliação da Caixa
    modalidade: str      # "1º Leilão" | "2º Leilão" | "Licitação Aberta"
    situacao:   str      # "Ocupado" | "Desocupado"
    estado:     str
    url_edital: str

    # Localização
    endereco: str = ""
    bairro:   str = ""
    cidade:   str = ""

    # Imóvel
    titulo:   str            = ""
    area:     Optional[float] = None
    quartos:  Optional[int]   = None
    foto:     Optional[str]   = None

    # Financeiro
    debitos_condominio:   Optional[float] = None
    possui_dividas:       Optional[bool]  = None  # débitos de IPTU/condomínio não quantificados (ex: Resale)
    possui_contencioso:   Optional[bool]  = None  # ação judicial em andamento sobre o imóvel (ex: Resale)

    # Meta
    fonte: str = "caixa_leilao"

    # Computado
    desconto_pct: float = field(init=False, default=0.0)

    def __post_init__(self):
        if self.avaliacao > 0:
            self.desconto_pct = round(
                (self.avaliacao - self.preco) / self.avaliacao * 100, 1
            )
