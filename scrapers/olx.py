"""
scrapers/olx.py
───────────────
Scraper para OLX Imóveis.
Usa curl_cffi (impersonate=chrome120) para contornar bloqueio por
fingerprint TLS — requests puro tomava 403 do WAF da OLX.

Resolução de listings em 2 camadas:
  1. __NEXT_DATA__ (formato antigo — mantido caso o site volte a usá-lo)
  2. RSC streaming: self.__next_f.push([1, "..."]) — formato atual
     (ago/2026). OLX migrou pra Next.js App Router; os dados de cada
     anúncio vêm embutidos como um array JSON "ads":[...] dentro de um
     desses chunks, não mais em __NEXT_DATA__. Confirmado ao vivo: o
     JSON-LD que sobra na página é só um resumo agregado (schema.org
     Product/AggregateOffer), sem dado por anúncio — por isso, ao
     contrário do ZAP, não dá pra usar JSON-LD como fallback aqui.
"""

import json, re, time, logging
from curl_cffi import requests
from typing import Optional
from scrapers.base import Listing
from utils.text    import extrair_numero, extrair_area, extrair_quartos, sanitizar_placeholder as _sanitizar_placeholder

logger   = logging.getLogger(__name__)
# URL verificada contra a estrutura real do site (jun/2026).
# A antiga "/imoveis/estado-sp" não inclui o tipo de imóvel no path
# e retorna resultados misturados (casas + apartamentos + comercial).
BASE_URL = "https://www.olx.com.br/imoveis/venda/apartamentos/estado-sp/sao-paulo-e-regiao"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
}

# Palavras que indicam aluguel, não venda — descarta o anúncio
_PALAVRAS_ALUGUEL = ("aluguel", "alugar", "mensal", "por mês", "loca", "rental", "lease")


# ── API pública ───────────────────────────────────────────────

def buscar(cfg: dict) -> list[Listing]:
    """Busca listings no OLX com filtros do config.yaml (bloco 'mercado')."""
    base_url = _get_base_url(cfg)
    listings = []

    for pagina in range(1, 6):
        params = {**_montar_params(cfg), "o": pagina}
        try:
            resp = requests.get(
                base_url, params=params, headers=HEADERS,
                timeout=15, impersonate="chrome120",
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"OLX erro p.{pagina}: {e}"); break

        if pagina == 1:
            _salvar_debug(resp.text, "olx")

        novos = _parsear_pagina(resp.text)
        if not novos:
            break

        listings.extend(novos)
        logger.info(f"OLX p.{pagina}: {len(novos)} anúncios")
        time.sleep(1.5)

    return listings


def _get_base_url(cfg: dict) -> str:
    """Permite sobrescrever a região via mercado.slug_olx (multi-região)."""
    slug = cfg.get("mercado", {}).get("slug_olx")
    return f"https://www.olx.com.br/imoveis/{slug}" if slug else BASE_URL


# ── Parsing ───────────────────────────────────────────────────

def _parsear_pagina(html: str) -> list[Listing]:
    # Camada 1: __NEXT_DATA__ (formato antigo)
    script = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if script:
        try:
            data = json.loads(script.group(1))
            ads  = data.get("props", {}).get("pageProps", {}).get("ads", [])
            if ads:
                return [l for l in (_ad_para_listing(a) for a in ads) if l]
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"OLX: __NEXT_DATA__ presente mas falhou ao parsear: {e}")

    # Camada 2: RSC streaming (formato atual — App Router)
    ads_rsc = _extrair_ads_rsc(html)
    if ads_rsc:
        logger.info(f"OLX: listings via RSC streaming ({len(ads_rsc)} itens brutos)")
        return [l for l in (_ad_rsc_para_listing(a) for a in ads_rsc) if l]

    logger.warning("OLX: nenhum listing encontrado (nem __NEXT_DATA__, nem RSC)")
    return []


# ── Camada 2: RSC streaming (self.__next_f.push) ───────────────

_PADRAO_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,\s*(".*?")\]\)', re.DOTALL)


def _extrair_ads_rsc(html: str) -> list[dict]:
    """
    Cada chunk é uma string JSON (precisa de json.loads pra desescapar)
    contendo um trecho do payload RSC do Next.js. O chunk que importa
    tem um array "ads":[...] embutido no meio de um objeto maior que
    NÃO é JSON válido por si só — por isso extrai o array via
    contagem de colchetes balanceada, em vez de tentar parsear o
    chunk inteiro como JSON.
    """
    marcador = '"ads":['
    for chunk_str in _PADRAO_RSC_CHUNK.findall(html):
        try:
            texto = json.loads(chunk_str)
        except json.JSONDecodeError:
            continue

        pos = texto.find(marcador)
        if pos == -1:
            continue

        inicio = pos + len(marcador) - 1  # posição do '['
        array_txt = _extrair_array_balanceado(texto, inicio)
        if not array_txt:
            continue

        try:
            ads = json.loads(array_txt)
        except json.JSONDecodeError as e:
            logger.debug(f"OLX: array 'ads' encontrado mas JSON inválido: {e}")
            continue

        if ads and isinstance(ads, list):
            return ads

    return []


def _extrair_array_balanceado(texto: str, inicio: int) -> Optional[str]:
    """Extrai um array JSON bem-formado a partir de '[' em `inicio`,
    contando profundidade de colchetes e ignorando colchetes dentro
    de strings (ex: URLs, títulos com texto livre)."""
    profundidade = 0
    dentro_string = False
    escapando = False
    for i in range(inicio, len(texto)):
        c = texto[i]
        if escapando:
            escapando = False
            continue
        if c == "\\":
            escapando = True
            continue
        if c == '"':
            dentro_string = not dentro_string
            continue
        if dentro_string:
            continue
        if c == "[":
            profundidade += 1
        elif c == "]":
            profundidade -= 1
            if profundidade == 0:
                return texto[inicio:i + 1]
    return None


def _ad_rsc_para_listing(ad: dict) -> Optional[Listing]:
    """
    Mapeia um item do array 'ads' do payload RSC pra Listing.
    Diferenças-chave em relação ao formato antigo (__NEXT_DATA__):
      - Sem campo de descrição (só 'subject'/título) — o filtro de
        aluguel disfarçado aqui só pode checar o título.
      - Bairro/cidade vêm limpos em 'locationDetails', sem precisar
        de regex no título (diferente do fallback que foi necessário
        pro ZAP).
      - Valores de 'properties' vêm formatados como texto
        ('R$ 470', '46m²'), não como número cru — precisa extrair_numero
        / extrair_area em vez de conversão direta.
      - Alguns itens do array 'ads' são slots de publicidade nativa,
        sem listId/url/properties — descartados aqui.
    """
    try:
        list_id = ad.get("listId")
        url     = ad.get("url", "") or ""
        if not list_id or not url:
            return None

        titulo = ad.get("subject", "") or ""

        # Descarta anúncios de aluguel disfarçados — sem 'body' nesse
        # formato, só dá pra checar o título.
        if any(p in titulo.lower() for p in _PALAVRAS_ALUGUEL):
            return None

        props = {p.get("name"): p.get("value") for p in ad.get("properties", [])}

        area = extrair_area(str(props.get("size", "")))
        if not area:
            area = extrair_area(titulo)

        condominio = _sanitizar_placeholder(extrair_numero(str(props.get("condominio", ""))))
        iptu       = _sanitizar_placeholder(extrair_numero(str(props.get("iptu", ""))))

        loc = ad.get("locationDetails") or {}

        return Listing(
            id         = str(list_id),
            titulo     = titulo,
            preco      = extrair_numero(str(ad.get("price") or ad.get("priceValue") or "")),
            area       = area,
            quartos    = _safe_int(props.get("rooms")) or extrair_quartos(titulo),
            banheiros  = _safe_int(props.get("bathrooms")),
            vagas      = _safe_int(props.get("garage_spaces")) or 0,
            bairro     = loc.get("neighbourhood", ""),
            cidade     = loc.get("municipality", "São Paulo"),
            url        = url,
            condominio = condominio,
            iptu       = iptu,
            fotos      = [i.get("original", "") for i in ad.get("images", [])[:3]],
            fonte      = "olx",
        )
    except Exception as e:
        logger.debug(f"OLX: erro ao parsear ad RSC: {e}")
        return None


def _ad_para_listing(ad: dict) -> Optional[Listing]:
    try:
        props     = {p["name"]: p.get("value") for p in ad.get("properties", [])}
        titulo    = ad.get("subject", "") or ""
        descricao = ad.get("body", "") or ""
        url       = ad.get("url", "") or ""

        # Descarta anúncios de aluguel disfarçados na busca de venda
        texto_check = f"{titulo} {descricao}".lower()
        if any(p in texto_check for p in _PALAVRAS_ALUGUEL):
            return None

        area = _safe_float(props.get("size"))
        if not area:
            area = extrair_area(descricao)

        condominio = None
        for k, v in props.items():
            if "condom" in k.lower():
                condominio = extrair_numero(str(v))
                break
        if not condominio:
            condominio = _extrair_condominio_texto(descricao)
        condominio = _sanitizar_placeholder(condominio)

        bairro = (ad.get("neighbourhood") or ad.get("neighborhood")
                  or props.get("neighbourhood") or props.get("neighborhood") or "")

        return Listing(
            id         = str(ad.get("listId", "")),
            titulo     = titulo,
            preco      = extrair_numero(str(ad.get("price", ""))),
            area       = area,
            quartos    = _safe_int(props.get("rooms")) or extrair_quartos(descricao),
            banheiros  = _safe_int(props.get("bathrooms")),
            vagas      = _safe_int(props.get("garage_spaces")) or 0,
            bairro     = bairro,
            cidade     = ad.get("municipality", "São Paulo"),
            url        = url,
            condominio = condominio,
            lat        = _safe_float(ad.get("coordinates", {}).get("latitude")),
            lng        = _safe_float(ad.get("coordinates", {}).get("longitude")),
            descricao  = descricao,
            fotos      = [i.get("original", "") for i in ad.get("images", [])[:3]],
            fonte      = "olx",
        )
    except Exception as e:
        logger.debug(f"OLX: erro ao parsear ad: {e}")
        return None


# ── Helpers ───────────────────────────────────────────────────

def _montar_params(cfg: dict) -> dict:
    """
    Apenas filtros de preço — o tipo (apartamento) já está no path
    da URL (BASE_URL / slug_olx), então 'q=apartamento' seria
    redundante e poderia conflitar com a busca por texto livre.
    """
    mc = cfg.get("mercado", {})
    return {k: v for k, v in {
        "pe": mc.get("preco_max"),
        "ps": mc.get("preco_min"),
    }.items() if v is not None}


def _extrair_condominio_texto(descricao: str) -> Optional[float]:
    """Fallback: procura 'condomínio R$ XXX' direto no texto do anúncio."""
    m = re.search(
        r"(?i)(?:condom[ií]nio|cond\.?)\s*[:\-]?\s*(?:R\$)?\s*(\d{1,3}(?:\.\d{3})*)",
        descricao,
    )
    return float(m.group(1).replace(".", "")) if m else None


def _salvar_debug(html: str, fonte: str):
    """Salva HTML em data/ (gitignored) para inspeção manual se necessário."""
    try:
        import os
        os.makedirs("data", exist_ok=True)
        with open(f"data/debug_{fonte}.html", "w", encoding="utf-8") as f:
            f.write(html)
        logger.debug(f"[DEBUG] HTML salvo em data/debug_{fonte}.html")
    except Exception as e:
        logger.debug(f"Falha ao salvar debug HTML: {e}")


def _safe_float(v) -> Optional[float]:
    try:   return float(v) if v is not None else None
    except (TypeError, ValueError): return None

def _safe_int(v) -> Optional[int]:
    try:   return int(v) if v is not None else None
    except (TypeError, ValueError): return None
