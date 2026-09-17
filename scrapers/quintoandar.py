"""
scrapers/quintoandar.py
─────────────────────────
Scraper para QuintoAndar via API interna (house-listing-search).

Achada por engenharia reversa: QuintoAndar é um SPA React (Next.js)
que não embute nenhum anúncio no HTML inicial — tudo carrega depois,
via chamada de API do navegador (mesmo motivo do OLX antes do fix de
RSC, mas aqui não tem __NEXT_DATA__/RSC nenhum pra aproveitar, é 100%
client-side). Usuário capturou um HAR real do navegador (15-16/09/2026)
e a chamada que traz os dados completos é:

    POST https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list

Sem autenticação, sem cookie, sem chave de API — só precisa de headers
de navegador comuns (origin/referer/user-agent). Testado ao vivo direto
via curl_cffi antes de escrever este arquivo, nas 3 regiões do bot.

Lançamentos (empreendimentos ainda em construção) aparecem misturados
com anúncios normais quando a busca cobre a cidade inteira — preço/
área/quartos desses vêm como faixa do EMPREENDIMENTO inteiro (campo
`isPrimaryMarket`), não de uma unidade real específica. Descartados
aqui, mesma filosofia dos filtros hard do scorer: não avalia dado que
não é confiável.
"""

import logging
import time
from typing import Optional

import requests

from scrapers.base import Listing

logger = logging.getLogger(__name__)

API_URL = "https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list"
PAGE_SIZE = 24

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://www.quintoandar.com.br",
    "referer": "https://www.quintoandar.com.br/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

FIELDS = [
    "id", "salePrice", "area", "bedrooms", "bathrooms", "parkingSpaces",
    "iptuPlusCondominium", "address", "regionName", "neighbourhood", "city",
    "imageList", "type", "forSale", "isPrimaryMarket",
]


# ── API pública ───────────────────────────────────────────────

def buscar(cfg: dict) -> list[Listing]:
    """
    Busca a região configurada em cfg['mercado']['quintoandar']
    (slug/coordenada/viewport — ver config.yaml). Região sem esse bloco
    configurado é pulada silenciosamente (log debug), não é erro —
    permite ativar QuintoAndar região por região conforme for validando
    cobertura, sem quebrar as que ainda não têm coordenada definida.
    """
    mc = cfg.get("mercado", {})
    qa = mc.get("quintoandar")
    if not qa or not qa.get("slug") or not qa.get("coordenada") or not qa.get("viewport"):
        logger.debug("QuintoAndar: região sem slug/coordenada/viewport configurados, pulando")
        return []

    listings: list[Listing] = []
    offset = 0

    for pagina in range(1, 6):
        corpo = _montar_corpo(cfg, qa, offset)
        try:
            resp = requests.post(API_URL, json=corpo, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"QuintoAndar erro p.{pagina}: {e}")
            break

        try:
            data = resp.json()
        except ValueError as e:
            logger.error(f"QuintoAndar: resposta não é JSON válido: {e}")
            break

        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        novos = [l for l in (_item_para_listing(h) for h in hits) if l]
        listings.extend(novos)
        logger.info(f"QuintoAndar p.{pagina}: {len(novos)} anúncios")

        offset += PAGE_SIZE
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        if offset >= total:
            break
        time.sleep(1.5)

    return listings


# ── Montagem da requisição ──────────────────────────────────────

def _montar_corpo(cfg: dict, qa: dict, offset: int) -> dict:
    mc = cfg.get("mercado", {})
    return {
        "slug": qa["slug"],
        "topics": [],
        "fields": FIELDS,
        "sorting": {"criteria": "RELEVANCE", "order": "DESC"},
        "pagination": {"pageSize": PAGE_SIZE, "offset": offset},
        "context": {"listShowing": True, "mapShowing": True, "numPhotos": 3, "isSSR": False},
        "filters": {
            "enableFlexibleSearch": True,
            "businessContext": "SALE",
            "location": {
                "coordinate": qa["coordenada"],
                "viewport": qa["viewport"],
                "neighborhoods": [],
                "countryCode": "BR",
            },
            "priceRange": [{
                "costType": "SALE_PRICE",
                "range": {
                    "min": mc.get("preco_min") or 50000,
                    "max": mc["preco_max"],
                },
            }],
            "availability": "ANY",
            "occupancy": "ANY",
            "houseSpecs": {
                "area": {"range": {}},
                "houseTypes": ["Apartamento"],
                "amenities": [], "installations": [],
                "bathrooms": {"range": {}},
                "bedrooms": {"range": {}},
                "parkingSpace": {"range": {}},
                "suites": {"range": {}},
            },
            "origin": "HYBRID",
        },
        "locationDescriptions": [{"description": qa["slug"]}],
    }


# ── Parsing ──────────────────────────────────────────────────

def _item_para_listing(hit: dict) -> Optional[Listing]:
    try:
        s = hit.get("_source", {})

        # Lançamento — preço/área/quartos são faixa do empreendimento
        # inteiro, não de uma unidade real. Ver docstring do módulo.
        if s.get("isPrimaryMarket"):
            return None

        # businessContext=SALE já filtra no servidor, mas alguns itens
        # vêm com forSale=False mesmo assim (ex: só aluguel com opção
        # de compra futura) — checagem defensiva.
        if s.get("forSale") is False:
            return None

        preco = s.get("salePrice")
        area = s.get("area")
        if not preco or not area:
            return None

        list_id = str(s.get("id") or hit.get("_id") or "")
        if not list_id:
            return None

        tipo = s.get("type") or "Apartamento"
        bairro = s.get("neighbourhood") or s.get("regionName") or ""
        cidade = s.get("city") or ""

        # iptuPlusCondominium vem somado, sem separar — jogado inteiro
        # em condominio. O scorer soma condomínio + iptu/12 de qualquer
        # forma pro carry mensal (scorers/mercado.py:_calcular_metricas),
        # então o total da conta bate igual; só o breakdown individual
        # "condomínio vs IPTU" não fica exato pra essa fonte.
        condominio = s.get("iptuPlusCondominium")

        fotos = [
            f"https://quintoandar.com.br/img/v2/med/{img}"
            for img in (s.get("imageList") or [])[:3]
        ]

        return Listing(
            id=list_id,
            titulo=f"{tipo} à venda em {bairro or cidade}",
            preco=float(preco),
            area=float(area),
            quartos=s.get("bedrooms"),
            banheiros=s.get("bathrooms"),
            vagas=s.get("parkingSpaces") or 0,
            bairro=bairro,
            cidade=cidade,
            url=f"https://www.quintoandar.com.br/imovel/{list_id}/comprar",
            condominio=condominio,
            endereco=s.get("address"),
            fotos=fotos,
            fonte="quintoandar",
        )
    except Exception as e:
        logger.debug(f"QuintoAndar: erro ao parsear item: {e}")
        return None
