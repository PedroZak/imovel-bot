"""
utils/bairro.py
────────────────
Resolução e normalização de nomes de bairro.
Compartilhado entre scorers/mercado.py (scoring) e utils/historico.py
(tagueamento do histórico de preços) — extraído para módulo único porque
os dois precisam resolver bairro exatamente da mesma forma, ou a mediana
móvel do histórico ficaria dessincronizada das chaves usadas no score.
"""

import re
import unicodedata
from typing import Optional

# Prefixos que em São Paulo SEMPRE formam um bairro composto DIFERENTE
# do nome base — "Jardim Paraíso" não é "Paraíso" com um apelido, é um
# bairro à parte (visto ao vivo: dezenas de "JARDIM X"/"VILA X"/
# "PARQUE X" no cadastro de bairros da Caixa, scrapers/caixa_leilao.py).
# Sem essa lista, resolver_bairro("Jardim Paraíso", {"Paraíso": ...})
# retornava "Paraíso" por simples substring match — bug real reportado
# pelo usuário (12/09/2026): notificações de "Jardim Paraíso" chegando
# como se fossem do bairro-alvo "Paraíso".
_PREFIXOS_BAIRRO_COMPOSTO = {
    "jardim", "vila", "parque", "cidade", "conjunto", "nucleo", "núcleo",
    "recanto", "chacara", "chácara", "colonia", "colônia", "parada",
    "residencial", "condominio", "condomínio", "loteamento",
}

# Bairros reais DIFERENTES cujo nome contém o de um bairro-alvo como
# prefixo/sufixo — o filtro de prefixo acima não pega (o nome-alvo vem
# no INÍCIO, ou depois de uma preposição). Lista explícita em vez de uma
# regra genérica "alvo + do/da + palavra": essa rejeitaria texto livre
# legítimo tipo "no Centro da cidade". Cada entrada é removida do texto
# antes do match (já normalizada: sem acento, minúscula).
#  - "paraiso do morumbi": bairro à parte (zona sul, perfil bem
#    diferente do Paraíso da Av. Paulista) — reportado pelo usuário
#    (25/09/2026).
#  - "alto de pinheiros": bairro mais caro que Pinheiros; era limitação
#    conhecida do fix #13, agora coberta.
_BAIRROS_HOMONIMOS = ("paraiso do morumbi", "alto de pinheiros")


def normalizar(txt: str) -> str:
    """Remove acentos, baixa caixa. Usado em toda comparação de texto de bairro."""
    if not txt:
        return ""
    return (unicodedata.normalize("NFKD", str(txt))
                       .encode("ASCII", "ignore").decode("utf-8")
                       .lower().strip())


def slugificar(txt: str) -> str:
    """'Jardim Paulista' → 'jardim-paulista'. Usado para montar URLs (ex: Atlas)."""
    return normalizar(txt).replace(" ", "-")


def resolver_bairro(bairro_texto: str, refs: dict) -> Optional[str]:
    """
    Encontra a chave de referência (bairros_referencia) correspondente
    a um texto de bairro livre. Retorna a chave original (com acentos)
    tal como está em `refs`, ou None se não encontrar match.
    """
    alvo = normalizar(bairro_texto)
    for homonimo in _BAIRROS_HOMONIMOS:
        alvo = re.sub(rf"(?<!\w){re.escape(homonimo)}(?!\w)", " ", alvo)
    alvo = alvo.strip()
    if not alvo:
        return None
    for chave_ref in refs:
        ref_norm = normalizar(chave_ref)
        if _contem_bairro(ref_norm, alvo) or _contem_bairro(alvo, ref_norm):
            return chave_ref
    return None


def _contem_bairro(ref_norm: str, texto: str) -> bool:
    """
    True se `ref_norm` aparece em `texto` como o nome completo do
    bairro — não como sufixo de um bairro composto diferente. Exige
    fronteira de palavra dos dois lados (não casa "paraiso" dentro de
    "aeroparaiso") E rejeita quando a palavra imediatamente anterior
    é um prefixo que forma bairro composto em SP (ver
    _PREFIXOS_BAIRRO_COMPOSTO) — "jardim paraiso" não deve casar com
    "paraiso", são bairros diferentes mesmo "paraiso" aparecendo como
    palavra inteira ali dentro.
    """
    m = re.search(rf"(?<!\w){re.escape(ref_norm)}(?!\w)", texto)
    if not m:
        return False
    palavras_antes = texto[:m.start()].split()
    if palavras_antes and palavras_antes[-1] in _PREFIXOS_BAIRRO_COMPOSTO:
        return False
    return True


def texto_localizacao(listing) -> str:
    """
    Texto usado para resolver o bairro de um Listing. Se o campo `bairro`
    veio vazio do scraper (comum em fallbacks como JSON-LD), cai para
    título + descrição + URL como sinal best-effort.
    """
    if listing.bairro:
        return listing.bairro
    return f"{listing.titulo} {listing.descricao} {listing.url}"
