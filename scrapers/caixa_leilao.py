"""
scrapers/caixa_leilao.py
────────────────────────
Scraper para leilões Caixa Econômica Federal.

TIER 3 EM HOLD — este arquivo só é importado se main.py for chamado
explicitamente com --tier 3. Não faz parte do fluxo agendado (cron).

Busca em TODAS as cidades de leilao.cidades (config.yaml) — cada uma é
uma rodada de busca independente (a Caixa não aceita múltiplas cidades
numa query só). Adicionado 11/09/2026: antes só buscava a cidade fixa
em leilao.cidade (São Paulo). Nomes de cidade são traduzidos pro código
interno da Caixa via _CODIGO_CIDADE_SP — cidade sem código conhecido é
pulada com um warning, não quebra a busca das outras.

Reescrito em set/2026 — o portal mudou de layout por completo desde a
versão anterior (form único → fluxo de 3 chamadas AJAX encadeadas, sem
nenhuma documentação pública). Fluxo real, mapeado ao vivo:

  1. GET  busca-imovel.asp                     → abre sessão
  2. POST carregaPesquisaImoveis.asp            → devolve só os IDs dos
     imóveis, paginados 10 a 10 em inputs hidden hdnImov1..N (o valor
     de cada um é uma string de IDs separados por "_")
  3. GET  detalhe-imovel.asp?hdnimovel=<id>     → única página que
     ainda mostra "Valor de avaliação" — a listagem (passo 2) não
     inclui esse dado, só o valor mínimo de venda. Por isso é 1
     request por imóvel candidato (ver _PAUSA_ENTRE_DETALHES e
     max_detalhes no config.yaml — em sp_capital/apartamento isso
     já passou de 500 imóveis num teste ao vivo).

IMPORTANTE — ocupação: a versão anterior deste arquivo tratava
"Ocupado" como filtro hard (ver scorers/leilao.py). Confirmado ao
vivo (set/2026): esse dado sumiu do site. Na página de detalhe, o
campo aparece só como comentário morto no HTML
(`<!--span>Situação: <strong>Ocupado</strong></span><br-->`), sempre
comentado, em todo imóvel testado — não é condicional, é gap de
dado do próprio template da Caixa. O PDF do edital também não lista
ocupação por item (testado baixando um edital real de ~460 itens:
as únicas ocorrências da palavra são cláusulas jurídicas genéricas,
não status por imóvel). Não há mais como determinar ocupação via
scraping público — o filtro hard foi removido do scorer, que agora
gera um alerta obrigatório pedindo conferência manual no edital.

Variações de formato observadas no bloco de valores (mesmo site,
imóveis diferentes — ver _extrair_bloco_precos):
  a) "Valor mínimo de venda: R$ X (desconto de Y%)" — uma rodada só,
     já com desconto pré-calculado pela própria Caixa.
  b) "Valor mínimo de venda 1º Leilão: R$ X" — uma rodada, com o
     rótulo embutido na própria linha (sem desconto pré-calculado).
  c) "1º Leilão: R$ X" + "2º Leilão: R$ Y" — duas rodadas ainda não
     realizadas; usa sempre a primeira (é a que está aberta agora).
O cabeçalho da modalidade ("Licitação Aberta" / "Leilão SFI" em
<b>, 14pt) também está ausente em alguns imóveis sem padrão aparente
(confirmado com 2 imóveis idênticos em formato de preço, um com
cabeçalho e outro sem) — por isso o fallback para "Licitação Aberta"
quando não há cabeçalho nem rótulo de rodada.
"""

import re, time, logging, requests
from typing import Optional
from scrapers.base import LeilaoListing
from utils.text    import brl_para_float
from utils.bairro  import normalizar

logger    = logging.getLogger(__name__)
SITE_ROOT = "https://venda-imoveis.caixa.gov.br/"
BASE_URL  = SITE_ROOT + "sistema/"
BUSCA_URL = BASE_URL + "busca-imovel.asp"
HEADERS   = {
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":          BUSCA_URL,
    "X-Requested-With": "XMLHttpRequest",
}

# Códigos internos da Caixa extraídos do form ao vivo (set/2026) — não
# documentados publicamente, conferir se algum dia pararem de bater.
_TIPO_MAP = {"apartamento": "2", "casa": "1", "outros": "3", "indiferente": "4"}

# Código de cidade da Caixa por nome — achado ao vivo via
# carregaListaCidades.asp (busca-imovel.asp, campo cmb_estado=SP).
# Chave em nomes normalizados (utils.bairro.normalizar) pra casar com
# leilao.cidades do config.yaml (a MESMA lista de nomes usada por
# scrapers/resale.py) — evita manter 2 listas de cidade divergentes.
_CODIGO_CIDADE_SP = {
    "sao paulo":  "9859",
    "campinas":   "9205",
    "piracicaba": "9682",
}

_PAUSA_ENTRE_DETALHES  = 0.4   # segundos — não martelar o site
_MAX_DETALHES_PADRAO   = 150   # teto de segurança se config não especificar (POR CIDADE, não total)


# ── API pública ───────────────────────────────────────────────

def buscar(cfg: dict) -> list[LeilaoListing]:
    lc      = cfg.get("leilao", {})
    estados = lc.get("estados", ["SP"])
    tipo    = _TIPO_MAP.get(lc.get("tipo", "apartamento"), "2")
    max_detalhes = lc.get("max_detalhes", _MAX_DETALHES_PADRAO)
    cidades = _resolver_codigos_cidade(lc)

    listings = []
    for estado in estados:
        for cidade in cidades:
            listings.extend(_buscar_estado(estado, cidade, tipo, max_detalhes))
    return listings


def _resolver_codigos_cidade(lc: dict) -> list[str]:
    """
    leilao.cidades (nomes livres, compartilhado com resale.py) tem
    prioridade; leilao.cidade (código Caixa único, formato antigo)
    fica como fallback pra quem ainda não migrou o config.yaml.
    """
    nomes = lc.get("cidades")
    if nomes:
        codigos = []
        for nome in nomes:
            codigo = _CODIGO_CIDADE_SP.get(normalizar(nome))
            if codigo:
                codigos.append(codigo)
            else:
                logger.warning(f"Caixa: sem código conhecido pra cidade '{nome}' — pulando")
        if codigos:
            return codigos

    cidade_unica = lc.get("cidade")
    return [cidade_unica] if cidade_unica else []


# ── Coleta por estado ─────────────────────────────────────────

def _buscar_estado(estado: str, cidade: str, tipo: str, max_detalhes: int) -> list[LeilaoListing]:
    s = requests.Session()
    s.headers.update(HEADERS)

    rotulo = f"{estado}/{cidade}"

    try:
        s.get(BUSCA_URL, timeout=20)
        ids = _listar_ids(s, estado, cidade, tipo)
    except requests.RequestException as e:
        logger.error(f"Caixa ({rotulo}): {e}")
        return []

    if not ids:
        logger.warning(f"Caixa ({rotulo}): 0 imóveis na busca — site mudou de novo?")
        return []

    logger.info(f"Caixa ({rotulo}): {len(ids)} imóveis encontrados na busca")
    if len(ids) > max_detalhes:
        logger.warning(
            f"Caixa ({rotulo}): limitando a {max_detalhes} de {len(ids)} imóveis "
            f"(leilao.max_detalhes no config.yaml) — evita martelar o site"
        )
        ids = ids[:max_detalhes]

    listings = []
    for imovel_id in ids:
        try:
            detalhe = _buscar_detalhe(s, imovel_id, estado)
            if detalhe:
                listings.append(detalhe)
        except requests.RequestException as e:
            logger.debug(f"Caixa: falha ao buscar detalhe de {imovel_id}: {e}")
        time.sleep(_PAUSA_ENTRE_DETALHES)

    logger.info(f"Caixa ({rotulo}): {len(listings)} imóveis com detalhe coletado")
    return listings


def _listar_ids(s: requests.Session, estado: str, cidade: str, tipo: str) -> list[str]:
    resp = s.post(BASE_URL + "carregaPesquisaImoveis.asp", data={
        "hdn_estado": estado, "hdn_cidade": cidade, "hdn_bairro": "",
        "hdn_tp_venda": "", "hdn_tp_imovel": tipo, "hdn_area_util": "",
        "hdn_faixa_vlr": "", "hdn_quartos": "", "hdn_vg_garagem": "",
        "strValorSimulador": "", "strAceitaFGTS": "", "strAceitaFinanciamento": "",
    }, timeout=20)
    resp.encoding = "utf-8"

    if "Nenhum" in resp.text or "Ocorreu" in resp.text:
        return []

    paginas = re.findall(r"id='hdnImov\d+' value=([^>]*)>", resp.text)
    ids = [i for pagina in paginas for i in pagina.split("_") if i]

    if not ids:
        with open("data/debug_caixa.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        logger.debug("Caixa: resposta sem hdnImov salva em data/debug_caixa.html")

    return ids


# ── Detalhe por imóvel ─────────────────────────────────────────

def _buscar_detalhe(s: requests.Session, imovel_id: str, estado: str) -> Optional[LeilaoListing]:
    resp = s.get(BASE_URL + "detalhe-imovel.asp", params={"hdnimovel": imovel_id}, timeout=20)
    resp.encoding = "utf-8"
    html = resp.text

    precos = _extrair_bloco_precos(html)
    if not precos:
        return None
    preco, avaliacao, rotulo_rodada = precos
    if not preco or not avaliacao:
        return None

    return LeilaoListing(
        id          = imovel_id,
        preco       = preco,
        avaliacao   = avaliacao,
        modalidade  = _extrair_modalidade(html, rotulo_rodada),
        situacao    = "Desconhecida",
        estado      = estado,
        url_edital  = f"{BASE_URL}detalhe-imovel.asp?hdnimovel={imovel_id}",
        titulo      = _extrair_titulo(html),
        area        = _extrair_area(html),
        quartos     = _extrair_quartos(html),
        endereco    = _extrair_endereco(html),
        cidade      = _extrair_cidade(html),
        foto        = _extrair_foto(html),
    )


def _extrair_bloco_precos(html: str) -> Optional[tuple]:
    """
    Retorna (preco, avaliacao, rotulo_rodada). Cobre as 3 variações de
    formato documentadas no docstring do módulo. rotulo_rodada é
    "1º Leilão"/"2º Leilão" quando embutido na linha de preço, ou
    None quando o formato já vem com desconto pré-calculado.
    """
    m_aval = re.search(r"Valor de avalia..o:\s*R\$\s*([\d.,]+)", html)
    if not m_aval:
        return None
    avaliacao = brl_para_float(m_aval.group(1))

    rodadas = re.findall(
        r"Valor m.nimo de venda\s*(1.\s*Leil.o|2.\s*Leil.o)?\s*:\s*R\$\s*([\d.,]+)", html,
    )
    if not rodadas:
        return None

    rotulo, valor_txt = rodadas[0]  # primeira rodada listada = a aberta agora
    preco = brl_para_float(valor_txt)
    return preco, avaliacao, (rotulo.strip() or None)


def _extrair_modalidade(html: str, rotulo_rodada: Optional[str]) -> str:
    m = re.search(r"font-size:\s*14pt;'>\s*<b>([^<]+)</b>", html)
    if m:
        return m.group(1).strip()
    if rotulo_rodada:
        return rotulo_rodada.replace("°", "º")
    return "Licitação Aberta"


def _extrair_area(html: str) -> Optional[float]:
    m = re.search(r"rea total\s*=\s*<strong>([\d.,]+)m2</strong>", html)
    if not m:
        m = re.search(r"rea privativa\s*=\s*<strong>([\d.,]+)m2</strong>", html)
    return brl_para_float(m.group(1)) if m else None


def _extrair_quartos(html: str) -> Optional[int]:
    m = re.search(r"Quartos:\s*<strong>(\d+)</strong>", html)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*Quartos?", _extrair_descricao(html) or "")
    return int(m.group(1)) if m else None


def _extrair_descricao(html: str) -> Optional[str]:
    m = re.search(r"Descri..o:</strong><br>([^<]*)</p>", html)
    return m.group(1) if m else None


def _extrair_endereco(html: str) -> str:
    m = re.search(r"<strong>Endere.o:</strong><br>([^<]+)</p>", html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _extrair_cidade(html: str) -> str:
    m = re.search(r"Comarca:\s*<strong>([^<]+)</strong>", html)
    if not m:
        return ""
    comarca = m.group(1).strip()
    cidade, _, _uf = comarca.rpartition("-")
    return cidade.strip().title() if cidade else comarca.title()


def _extrair_titulo(html: str) -> str:
    m = re.search(r"Tipo de im.vel:\s*<strong>([^<]+)</strong>", html)
    tipo = m.group(1).strip() if m else "Imóvel"
    cidade = _extrair_cidade(html)
    return f"{tipo} — {cidade}" if cidade else tipo


def _extrair_foto(html: str) -> Optional[str]:
    m = re.search(r"preview\.src=[\"']([^\"']+)[\"']", html)
    if not m:
        m = re.search(r"class='fotoimovel'[^>]*src='([^']+)'", html)
    return (SITE_ROOT + m.group(1).lstrip("/")) if m else None
