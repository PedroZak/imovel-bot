"""
utils/text.py
─────────────
Funções utilitárias de parsing de texto/números.
Centralizadas aqui para evitar duplicação entre scrapers.
"""

import re
from typing import Optional


def extrair_numero(texto: str) -> Optional[float]:
    """'R$ 1.250.000,00' → 1250000.0"""
    if not texto:
        return None
    limpo = re.sub(r"[^\d,]", "", str(texto)).replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def brl_para_float(texto: str) -> Optional[float]:
    """'1.250.000,00' → 1250000.0  (formato BRL com ponto como milhar)"""
    if not texto:
        return None
    try:
        return float(str(texto).replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


_PADRAO_INTERVALO_AREA = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*-\s*\d+(?:[.,]\d+)?\s*m²", re.IGNORECASE
)


def extrair_area(texto: str) -> Optional[float]:
    """
    'Área: 52 m²' → 52.0

    Lançamentos costumam anunciar uma faixa de metragem ('46 - 101 m²')
    com um único preço de entrada. Esse preço quase sempre se refere à
    unidade MENOR da faixa — pareá-lo com a maior geraria um preço/m²
    artificialmente baixo (uma distorção falsa, não real). Por isso,
    quando há faixa, usa sempre o limite menor.
    """
    m_intervalo = _PADRAO_INTERVALO_AREA.search(str(texto))
    if m_intervalo:
        return brl_para_float(m_intervalo.group(1))

    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", str(texto), re.IGNORECASE)
    if m:
        return brl_para_float(m.group(1))
    return None


def extrair_quartos(texto: str) -> Optional[int]:
    """'3 quartos' ou '3 dorm.' → 3"""
    m = re.search(r"(\d+)\s*(?:quarto|dorm)", str(texto).lower())
    return int(m.group(1)) if m else None


def extrair_vagas(texto: str) -> int:
    """'2 vagas' → 2"""
    m = re.search(r"(\d+)\s*vaga", str(texto).lower())
    return int(m.group(1)) if m else 0


def contem_distresse(texto: str) -> bool:
    """Detecta palavras que sinalizam vendedor motivado."""
    palavras = [
        "urgente", "mudança", "abaixo da avaliação",
        "oportunidade", "torro", "preciso vender",
    ]
    t = texto.lower()
    return any(p in t for p in palavras)


def sanitizar_placeholder(valor: Optional[float]) -> Optional[float]:
    """
    OLX/ZAP mostram 'R$ 1' quando o anunciante não preenche condomínio
    ou IPTU — é o placeholder de "não informado" do próprio site, não
    um valor real. Sem esse tratamento, o scorer trataria R$1 como
    condomínio excelente ao invés de dado ausente. Normaliza para
    None (mesmo tratamento de "campo vazio" usado no resto do pipeline).
    Compartilhado entre olx.py e zap.py — os dois têm o mesmo problema.
    """
    if valor is not None and valor <= 1:
        return None
    return valor
