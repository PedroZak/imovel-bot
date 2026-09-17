"""
scrapers/resale.py
──────────────────
Scraper para leilões/venda direta da Resale (resale.com.br) — outlet
imobiliário que agrega imóveis retomados de vários bancos (Santander,
BTG, Banco Pan, BRB, entre outros) numa API só.

TIER 3 EM HOLD — só é importado se main.py for chamado explicitamente
com --tier 3.

A página é um SPA em React (create-react-app) sem nenhum dado no HTML
— tudo vem de uma API JSON própria (AWS API Gateway) chamada pelo
front-end: https://q3jhhgksa9.execute-api.us-east-2.amazonaws.com/prod
/property/. Achada via engenharia reversa do bundle JS público do
site (busca por "x-api-key" em main.*.chunk.js). A API key abaixo é
PÚBLICA — está embutida em texto puro no JS que qualquer navegador
baixa ao abrir resale.com.br, não é nenhum tipo de acesso privilegiado.

Dado bem mais rico que o da Caixa (JSON tipado, não HTML pra regex):
  - `desagio` (desconto %), `valores.valor_avaliado`/`valor_venda`
  - `tags` inclui "Desocupado"/"Locado" — situação de ocupação
    REAL, ao contrário da Caixa (ver scrapers/caixa_leilao.py), que
    perdeu esse dado. Por isso o scorer (scorers/leilao.py) volta a
    tratar ocupação como filtro hard quando a fonte é confiável
    (situacao != "Desconhecida").
  - `possui_dividas` / `possui_contencioso` — sinais de risco que a
    Caixa nunca expôs; viram alerta no scorer quando True.

LIMITAÇÕES CONHECIDAS (mapeadas ao vivo, set/2026):
  - Não existe filtro de cidade/estado que funcione via query string
    simples (`cidade=Piracicaba`, `estado=SP` etc. são ignorados
    silenciosamente — testado, resultado idêntico ao sem filtro). O
    endpoint /prod/city parece existir pra popular um combobox em
    cascata no front, mas não aceita nenhum dos parâmetros óbvios
    (estado, uf, sigla, parent) nem path (/city/SP) — sempre devolve
    a lista de estados. Não vale a pena insistir nisso: o volume
    total de imóveis (~500) é pequeno o bastante pra só paginar tudo
    (`tipo-venda=leilao`, sem outro filtro) e filtrar cidade/tipo no
    lado do Python.
  - Paginação é via `page` (1-indexed), NÃO `offset` — testado ao vivo
    em 04/09 com `offset` e parecia funcionar (a API aceitava o
    parâmetro sem erro), mas reteste em 11/09 mostrou que `offset` é
    ignorado (resultado idêntico pra offset=0/10/20) — o campo
    `pagination.offset` na resposta é só o tamanho fixo de página
    (20), não um parâmetro de entrada. `page` é o que de fato pagina
    (confirmado: IDs diferentes por página).
  - O parâmetro `order` (ex: `order=relevante`, usado pelo próprio
    front do site) **quebra o backend** — a API responde HTTP 200 com
    corpo `{"errorType": "Runtime.ExitError", ...}` em vez do
    `{"data": ..., "pagination": ...}` esperado sempre que `order` é
    passado, com qualquer valor testado. É bug deles, não nosso — por
    isso o scraper simplesmente nunca manda esse parâmetro.
  - A API tem um WAF sensível a rajada de requests: em teste manual,
    5 chamadas em poucos segundos (algumas com parâmetros idênticos a
    uma chamada que tinha acabado de funcionar) já tomaram 403, e o
    bloqueio durou mais de 30s. Por isso o pacing aqui é bem mais
    conservador que o da Caixa (2s entre páginas) e tem retry com
    backoff longo em vez de desistir no primeiro 403.
"""

import re, time, logging, requests
from typing import Optional
from scrapers.base import LeilaoListing

logger    = logging.getLogger(__name__)
SITE_URL  = "https://resale.com.br"
API_BASE  = "https://q3jhhgksa9.execute-api.us-east-2.amazonaws.com/prod"
API_KEY   = "TFqvYJxuhO67Bo5WOzspQ6UENhuIZFVvrhLIcCig"  # pública, ver docstring
HEADERS   = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-API-KEY":  API_KEY,
    "Accept":     "application/json",
}

_PAUSA_ENTRE_PAGINAS   = 2.0  # segundos — WAF sensível, ver docstring
_MAX_PAGINAS_PADRAO    = 20
_RETRIES_403           = 2
_ESPERA_BACKOFF_403    = 30   # segundos

_TAGS_IGNORAR = {"novidade", "locado", "desocupado"}


def buscar(cfg: dict) -> list[LeilaoListing]:
    lc = cfg.get("leilao", {})
    cidades_alvo = {_normalizar(c) for c in lc.get("cidades", [])}
    tipo_alvo    = lc.get("tipo", "apartamento")
    max_paginas  = lc.get("resale_max_paginas", _MAX_PAGINAS_PADRAO)

    listings: list[LeilaoListing] = []
    pagina = 1
    while pagina <= max_paginas:
        dados = _buscar_pagina(pagina)
        if dados is None:
            break

        itens = dados.get("data", [])
        if not itens:
            break

        for item in itens:
            cidade = (item.get("endereco") or {}).get("cidade", "")
            if cidades_alvo and _normalizar(cidade) not in cidades_alvo:
                continue
            if tipo_alvo and _normalizar(item.get("tipo_imovel", "")) != _normalizar(tipo_alvo):
                continue
            listing = _mapear(item)
            if listing:
                listings.append(listing)

        max_pages_api = dados.get("pagination", {}).get("max_pages", 1)
        if pagina >= max_pages_api:
            break
        pagina += 1
        time.sleep(_PAUSA_ENTRE_PAGINAS)

    logger.info(f"Resale: {len(listings)} imóveis coletados (cidades={cidades_alvo or 'todas'})")
    return listings


def _buscar_pagina(pagina: int) -> Optional[dict]:
    # NUNCA mandar "order" — quebra o backend deles, ver docstring do módulo.
    params = {"tipo-venda": "leilao", "page": pagina}
    for tentativa in range(_RETRIES_403 + 1):
        try:
            resp = requests.get(API_BASE + "/property/", params=params, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            logger.error(f"Resale: {e}")
            return None

        if resp.status_code == 200:
            resp.encoding = "utf-8"  # API não manda charset no header, requests às vezes chuta errado
            corpo = resp.json()
            if "data" in corpo and "pagination" in corpo:
                return corpo
            # Backend já quebrou (Runtime.ExitError) e devolveu HTTP 200
            # mesmo assim (visto ao vivo com o parâmetro "order" — ver
            # docstring do módulo). Não confundir com "zero resultados".
            logger.warning(f"Resale: resposta 200 sem 'data' na página={pagina}: {corpo}")
            return None

        if resp.status_code == 403 and tentativa < _RETRIES_403:
            logger.warning(
                f"Resale: 403 na página={pagina} (WAF/rate-limit) — "
                f"aguardando {_ESPERA_BACKOFF_403}s antes de tentar de novo "
                f"({tentativa + 1}/{_RETRIES_403})"
            )
            time.sleep(_ESPERA_BACKOFF_403)
            continue

        logger.error(f"Resale: HTTP {resp.status_code} inesperado na página={pagina}")
        return None
    return None


# ── Mapeamento JSON → LeilaoListing ─────────────────────────────

def _mapear(item: dict) -> Optional[LeilaoListing]:
    valores = item.get("valores") or {}
    preco     = valores.get("valor_venda")
    avaliacao = valores.get("valor_avaliado")
    if not preco or not avaliacao:
        return None

    endereco_info = item.get("endereco") or {}
    caract        = item.get("caracteristicas") or {}
    tags          = item.get("tags") or []
    area          = (item.get("areas_total") or {}).get("value")

    return LeilaoListing(
        id                 = item["id"],
        preco              = float(preco),
        avaliacao          = float(avaliacao),
        modalidade         = _extrair_modalidade(tags),
        situacao           = _extrair_situacao(tags),
        estado             = endereco_info.get("estado", ""),
        url_edital         = f"{SITE_URL}/imovel/{item['id']}",
        titulo             = item.get("nome_imovel") or "Imóvel",
        area               = float(area) if area else None,
        quartos            = _extrair_quartos(caract.get("dormitorios")),
        endereco           = endereco_info.get("endereco_completo", ""),
        bairro             = _extrair_bairro(item.get("nome_imovel", "")),
        cidade             = endereco_info.get("cidade", ""),
        foto               = item.get("foto_capa"),
        possui_dividas     = _sim_nao_para_bool(item.get("possui_dividas")),
        possui_contencioso = _sim_nao_para_bool(item.get("possui_contencioso")),
        fonte              = "resale",
    )


def _extrair_modalidade(tags: list[str]) -> str:
    for t in tags:
        tl = t.strip().lower()
        if tl in _TAGS_IGNORAR or tl.startswith("idr"):
            continue
        return t.strip()
    return "Leilão"


def _extrair_situacao(tags: list[str]) -> str:
    tags_lower = [t.strip().lower() for t in tags]
    if "desocupado" in tags_lower:
        return "Desocupado"
    if "locado" in tags_lower:
        return "Ocupado"
    return "Desconhecida"


def _extrair_quartos(dormitorios) -> Optional[int]:
    try:
        n = int(dormitorios)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _extrair_bairro(nome_imovel: str) -> str:
    """
    nome_imovel vem como "Tipo, Categoria, Bairro[, extra...]" (ex:
    "Apartamento, Residencial, Jardim Aeroporto" ou "Apartamento,
    Residencial, Moema, 1 dormitório(s)") — o bairro é sempre o
    TERCEIRO segmento (índice 2), não o último: alguns imóveis têm
    "1 dormitório(s)"/"1 vaga(s) de garagem" etc. anexado depois do
    bairro (visto ao vivo, 11/09/2026) — pegar o último quebraria
    esses casos.
    """
    partes = [p.strip() for p in nome_imovel.split(",") if p.strip()]
    if len(partes) >= 3:
        return partes[2]
    return partes[-1] if partes else ""


def _sim_nao_para_bool(valor) -> Optional[bool]:
    if valor is None:
        return None
    return str(valor).strip().lower() == "sim"


def _normalizar(texto: str) -> str:
    texto = texto.strip().lower()
    for de, para in (("á", "a"), ("à", "a"), ("â", "a"), ("ã", "a"),
                      ("é", "e"), ("ê", "e"), ("í", "i"),
                      ("ó", "o"), ("ô", "o"), ("õ", "o"), ("ú", "u"), ("ç", "c")):
        texto = texto.replace(de, para)
    return re.sub(r"\s+", " ", texto)
