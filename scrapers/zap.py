"""
scrapers/zap.py
───────────────
Scraper para ZAP Imóveis e VivaReal (mesmo backend).

Resolução de listings em 3 camadas:
  1. __NEXT_DATA__ via paths conhecidos (rápido)
  2. __NEXT_DATA__ via busca recursiva validada (resiliente)
  3. JSON-LD embutido (<script type="application/ld+json">) — fallback
     para quando o site mudou de mecanismo de renderização

Se Cloudflare bloquear → migrar para Apify (Opção B).
"""

import json, re, time, logging, requests
from typing import Optional, Literal
from scrapers.base import Listing
from utils.text    import (
    extrair_numero, extrair_area, extrair_quartos,
    sanitizar_placeholder as _sanitizar_placeholder,
)

logger = logging.getLogger(__name__)
Portal = Literal["ZAP", "VIVA_REAL"]

SEARCH_URL = {
    "ZAP":       "https://www.zapimoveis.com.br/venda/apartamentos/sp+sao-paulo/",
    "VIVA_REAL": "https://www.vivareal.com.br/venda/sp/sao-paulo/apartamento_residencial/",
}
HEADERS = {
    "User-Agent":                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":           "pt-BR,pt;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

# Palavras que indicam aluguel, não venda — descarta o anúncio.
# Mesma proteção que olx.py já tinha; ZAP também mistura "para locação"
# em buscas de venda (visto em teste manual: um "Apartamento para
# locação" apareceu no meio de resultados de venda).
_PALAVRAS_ALUGUEL = ("aluguel", "alugar", "para locação", "locacao", "rental", "lease")


# ── API pública ───────────────────────────────────────────────

def buscar(cfg: dict, portal: Portal = "ZAP") -> list[Listing]:
    session = _criar_sessao(portal)
    listings = []

    for pagina in range(1, 6):
        url = _montar_url(cfg, portal, pagina)
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 403 or "cf-mitigated" in resp.headers:
                logger.warning(f"{portal} bloqueado pelo Cloudflare. Considere Apify.")
                break
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"{portal} erro p.{pagina}: {e}"); break

        if pagina == 1:
            _salvar_debug(resp.text, portal.lower())

        novos = _extrair_todos_listings(resp.text, portal)
        if not novos:
            break

        listings.extend(novos)
        logger.info(f"{portal} p.{pagina}: {len(novos)} anúncios")
        time.sleep(2.5)

    return listings


def _criar_sessao(portal: Portal) -> requests.Session:
    """Visita a homepage primeiro para obter cookies de sessão."""
    session = requests.Session()
    session.headers.update(HEADERS)
    home = ("https://www.zapimoveis.com.br" if portal == "ZAP"
            else "https://www.vivareal.com.br")
    try:
        session.get(home, timeout=10)
        time.sleep(2.0)
    except requests.RequestException:
        pass
    return session


# ── Parsing — orquestrador das 3 camadas ──────────────────────

def _extrair_todos_listings(html: str, portal: Portal) -> list[Listing]:
    # Camada 1+2: __NEXT_DATA__
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            listings_raw = _extrair_listings_next_data(data, portal)
            if listings_raw:
                return [l for l in (_item_para_listing(i, portal) for i in listings_raw) if l]
        except json.JSONDecodeError as e:
            logger.debug(f"{portal}: __NEXT_DATA__ presente mas JSON inválido: {e}")

    # Camada 3: JSON-LD (fallback para quando o site mudou de mecanismo)
    listings_ld = _parsear_jsonld(html, portal)
    if listings_ld:
        logger.info(f"{portal}: listings via JSON-LD ({len(listings_ld)} itens)")
        return listings_ld

    logger.warning(f"{portal}: nenhum listing encontrado (nem __NEXT_DATA__, nem JSON-LD)")
    return []


# ── Camada 1+2: __NEXT_DATA__ ─────────────────────────────────

def _extrair_listings_next_data(data: dict, portal: Portal) -> list:
    paths = [
        "props.pageProps.initialState.results.listings",
        "props.pageProps.listingProps.listings",
        "props.pageProps.listings",
    ]
    for path in paths:
        result = _deep_get(data, path)
        if result and isinstance(result, list) and len(result) > 0:
            logger.debug(f"{portal}: listings via path '{path}'")
            return result

    result = _extrair_listings_recursivo(data)
    if result:
        logger.info(f"{portal}: listings via busca recursiva ({len(result)} itens)")
    return result


def _extrair_listings_recursivo(estrutura) -> list:
    if isinstance(estrutura, dict):
        if ("listings" in estrutura
                and isinstance(estrutura["listings"], list)
                and len(estrutura["listings"]) > 0
                and _parece_listing(estrutura["listings"][0])):
            return estrutura["listings"]
        for valor in estrutura.values():
            result = _extrair_listings_recursivo(valor)
            if result: return result
    elif isinstance(estrutura, list):
        for item in estrutura:
            result = _extrair_listings_recursivo(item)
            if result: return result
    return []


def _parece_listing(item: dict) -> bool:
    campos = {"id", "title", "pricingInfos", "address"}
    return bool(campos.intersection(item.keys()))


def _item_para_listing(item: dict, portal: Portal) -> Optional[Listing]:
    try:
        ld    = item.get("listing", item)
        preco = (ld.get("pricingInfos") or [{}])[0]
        addr  = ld.get("address", {})
        geo   = addr.get("point", {})

        titulo    = ld.get("title", "")
        descricao = ld.get("description", "")

        # Descarta anúncios de aluguel disfarçados na busca de venda
        # (o próprio pricingInfos.businessType também sinaliza isso
        # quando presente — checa os dois pra não depender só do texto)
        business_type = str(preco.get("businessType", "")).upper()
        texto_check   = f"{titulo} {descricao}".lower()
        if business_type == "RENTAL" or any(p in texto_check for p in _PALAVRAS_ALUGUEL):
            return None

        area_list      = ld.get("usableAreas") or ld.get("totalAreas") or []
        quartos_list   = ld.get("bedrooms", [])
        banheiros_list = ld.get("bathrooms", [])
        vagas_list     = ld.get("parkingSpaces", [])
        url_base     = ("https://www.zapimoveis.com.br" if portal == "ZAP"
                        else "https://www.vivareal.com.br")

        return Listing(
            id         = str(ld.get("id", "")),
            titulo     = titulo,
            preco      = extrair_numero(str(preco.get("price", ""))),
            area       = float(area_list[0]) if area_list else None,
            quartos    = int(quartos_list[0]) if quartos_list else None,
            banheiros  = int(banheiros_list[0]) if banheiros_list else None,
            vagas      = int(vagas_list[0]) if vagas_list else 0,
            bairro     = addr.get("neighborhood", "") or addr.get("zone", ""),
            cidade     = addr.get("city", "São Paulo"),
            url        = url_base + ld.get("permalink", ""),
            condominio = _sanitizar_placeholder(extrair_numero(str(preco.get("monthlyCondoFee", "")))),
            iptu       = _sanitizar_placeholder(extrair_numero(str(preco.get("yearlyIptu", "")))),
            lat        = geo.get("lat"),
            lng        = geo.get("lon"),
            endereco   = addr.get("street", ""),
            descricao  = descricao,
            fotos      = [img.get("url", "") for img in ld.get("images", [])[:3]],
            fonte      = portal.lower().replace("_", ""),
        )
    except Exception as e:
        logger.debug(f"{portal}: erro ao parsear item __NEXT_DATA__: {e}")
        return None


# ── Camada 3: JSON-LD ──────────────────────────────────────────

def _parsear_jsonld(html: str, portal: Portal) -> list[Listing]:
    """
    Extrai anúncios de blocos <script type="application/ld+json">.
    Suporta ItemList (itemListElement) e RealEstateListing diretos.
    """
    blocos = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    )
    resultados: list[Listing] = []

    for bloco in blocos:
        try:
            obj = json.loads(bloco)
        except json.JSONDecodeError:
            continue

        if isinstance(obj, dict) and obj.get("@type") == "ItemList":
            for el in obj.get("itemListElement", []):
                item = el.get("item") if isinstance(el, dict) else None
                if item:
                    l = _jsonld_item_para_listing(item, portal)
                    if l: resultados.append(l)

        elif isinstance(obj, dict) and obj.get("@type") == "RealEstateListing":
            main = obj.get("mainEntity") or []
            if isinstance(main, dict):
                main = [main]
            for ent in main:
                l = _jsonld_item_para_listing(ent, portal)
                if l: resultados.append(l)

        elif isinstance(obj, list):
            for ent in obj:
                l = _jsonld_item_para_listing(ent, portal)
                if l: resultados.append(l)

    return resultados


# Padrão do título do ZAP: "...em <Bairro>, <Cidade>" no final.
# O JSON-LD do ZAP não tem um campo confiável de bairro — o schema.org
# PostalAddress.addressLocality retorna a CIDADE (não o bairro), o que
# fazia todo listing cair com bairro="São Paulo"/"Campinas" e ser
# reprovado por "bairro fora do radar" mesmo tendo dado bom. O título
# sempre traz o bairro real, então extrai de lá nesta camada.
_PADRAO_BAIRRO_CIDADE = re.compile(r".*\bem\s+([^,]+?),\s*(.+?)\s*$")


def _extrair_bairro_cidade_titulo(nome: str) -> tuple[str, str]:
    if not nome:
        return "", ""
    m = _PADRAO_BAIRRO_CIDADE.match(nome)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", ""


def _jsonld_item_para_listing(item: dict, portal: Portal) -> Optional[Listing]:
    try:
        if not isinstance(item, dict):
            return None
        nome    = item.get("name", "")
        url     = item.get("url", "")
        oferta  = item.get("offers", {}) if isinstance(item.get("offers"), dict) else {}
        preco   = extrair_numero(str(oferta.get("price", "")))
        end     = item.get("address", {}) if isinstance(item.get("address"), dict) else {}
        descricao = item.get("description", "")

        if not (nome or url):
            return None

        # Mesma proteção contra aluguel disfarçado do parser __NEXT_DATA__
        texto_check = f"{nome} {descricao}".lower()
        if any(p in texto_check for p in _PALAVRAS_ALUGUEL):
            return None

        bairro_titulo, cidade_titulo = _extrair_bairro_cidade_titulo(nome)

        # area/quartos/banheiros: prefere os campos estruturados do
        # JSON-LD (floorSize/numberOfBedrooms/numberOfBathroomsTotal —
        # confirmados presentes ao vivo, set/2026) sobre regex no
        # título. Mais confiável: um título com faixa ("24 - 40 m²",
        # comum em lançamento) não tem UM valor certo pra regex tirar,
        # enquanto o campo estruturado já vem resolvido pelo ZAP. Cai
        # pro regex só se o campo estruturado vier ausente/zerado.
        floor_size = item.get("floorSize")
        area = _safe_float(floor_size.get("value")) if isinstance(floor_size, dict) else None
        if not area:
            area = extrair_area(nome)

        quartos = _safe_int(item.get("numberOfBedrooms"))
        if quartos is None:
            quartos = extrair_quartos(nome)

        banheiros = _safe_int(item.get("numberOfBathroomsTotal"))

        return Listing(
            id        = re.sub(r"\W+", "", url)[-20:] or nome[:20],
            titulo    = nome,
            preco     = preco,
            area      = area,
            quartos   = quartos,
            banheiros = banheiros,
            bairro    = bairro_titulo or end.get("addressLocality", ""),
            cidade    = cidade_titulo or end.get("addressRegion", "São Paulo"),
            url       = url,
            descricao = descricao,
            fonte     = portal.lower().replace("_", ""),
        )
    except Exception as e:
        logger.debug(f"{portal}: erro ao parsear item JSON-LD: {e}")
        return None


# ── Helpers ───────────────────────────────────────────────────

def _montar_url(cfg: dict, portal: Portal, pagina: int) -> str:
    mc   = cfg.get("mercado", {})
    slug = mc.get("slug_zap")
    base = (f"https://www.zapimoveis.com.br/venda/apartamentos/{slug}/"
            if slug and portal == "ZAP" else SEARCH_URL[portal])
    qs = f"?pagina={pagina}"
    if mc.get("preco_max"): qs += f"&precoMaximo={mc['preco_max']}"
    if mc.get("preco_min"): qs += f"&precoMinimo={mc['preco_min']}"
    return base + qs


def _deep_get(d: dict, path: str):
    for k in path.split("."):
        if not isinstance(d, dict): return None
        d = d.get(k)
    return d


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
