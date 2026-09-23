"""
calibragem/atlas.py
────────────────────
Refaz a consulta ao atlasdados.com/sp (preço real de fechamento via ITBI)
para os bairros de SP capital configurados. Roda mensalmente via
.github/workflows/calibragem.yml — mantém compra_m2 sempre fresco sem
precisar de intervenção manual.

Cobre APENAS SP capital — o Atlas não tem dados de Campinas/Piracicaba.
Para essas regiões, a calibragem vem de utils/historico.py (mediana
móvel dos próprios dados coletados pelo bot).

AVISO: a extração é por regex sobre o texto visível da página, não por
seletor CSS fixo — o Atlas pode reestruturar o HTML a qualquer momento.
Se retornar None para todos os bairros, é sinal de que os padrões abaixo
precisam de ajuste (o site provavelmente mudou de layout).
"""

import logging
import re
import time
from typing import Optional

import requests

from utils.bairro import slugificar

logger = logging.getLogger(__name__)

ATLAS_BASE = "https://atlasdados.com/sp/bairro"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Bairros sem página própria no Atlas (404) — o site trata como parte de
# outro bairro maior. Confirmado ao vivo (22/09/2026, pedido do usuário):
# "Paraíso" e "Vila Clementino" caem dentro de "Vila Mariana" no Atlas.
# buscar_bairro() busca pelo slug do bairro-alias, mas o resultado
# continua indexado pelo nome ORIGINAL em buscar_todos().
_ALIAS_ATLAS = {
    "Paraíso": "Vila Mariana",
    "Vila Clementino": "Vila Mariana",
}

# Padrões tentados em ordem — o primeiro que casar múltiplas vezes na
# página é usado. Procura valor em R$ associado a "área privativa"
# (não "área construída", que costuma ser um número diferente/menor).
_PADROES_PRIVATIVA = [
    r"(?:pre[çc]o|valor)[^R$]{0,40}[áa]rea\s+privativa[^R$]{0,60}R\$\s*([\d.,]+)",
    r"R\$\s*([\d.,]+)[^\n]{0,40}[áa]rea\s+privativa",
    r"[áa]rea\s+privativa[^\n]{0,60}R\$\s*([\d.,]+)",
]
# Fallback: qualquer "R$ X/m²" na página, se os padrões acima não baterem
_PADRAO_GENERICO = r"R\$\s*([\d.,]+)\s*(?:/\s*m²|por\s*m²)"


def buscar_bairro(nome_bairro: str) -> Optional[float]:
    """
    Busca o compra_m2 (ITBI, área privativa) de um bairro no Atlas.
    Retorna None se a página não abrir ou nenhum padrão bater.
    """
    slug = slugificar(_ALIAS_ATLAS.get(nome_bairro, nome_bairro))
    url = f"{ATLAS_BASE}/{slug}/"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Atlas: erro ao buscar '{nome_bairro}' ({url}): {e}")
        return None

    texto = resp.text

    for padrao in _PADROES_PRIVATIVA:
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            valor = _parse_brl(m.group(1))
            if valor:
                logger.debug(f"Atlas '{nome_bairro}': R${valor:,.0f}/m² (padrão específico)")
                return valor

    m = re.search(_PADRAO_GENERICO, texto, re.IGNORECASE)
    if m:
        valor = _parse_brl(m.group(1))
        if valor:
            logger.debug(f"Atlas '{nome_bairro}': R${valor:,.0f}/m² (padrão genérico — conferir)")
            return valor

    logger.warning(f"Atlas: nenhum padrão de preço encontrado para '{nome_bairro}'. "
                    "Layout do site pode ter mudado — revisar calibragem/atlas.py.")
    return None


def buscar_todos(bairros: list[str], delay: float = 2.0) -> dict[str, float]:
    """
    Busca compra_m2 para uma lista de bairros, com delay entre
    requisições para não sobrecarregar o site (roda 1x/mês, não
    precisa de pressa).
    """
    resultado = {}
    for i, bairro in enumerate(bairros):
        valor = buscar_bairro(bairro)
        if valor:
            resultado[bairro] = valor
        if i < len(bairros) - 1:
            time.sleep(delay)
    return resultado


def _parse_brl(texto: str) -> Optional[float]:
    """'14.349,00' ou '14.349' → 14349.0"""
    try:
        limpo = texto.replace(".", "").replace(",", ".")
        return float(limpo)
    except (ValueError, AttributeError):
        return None
