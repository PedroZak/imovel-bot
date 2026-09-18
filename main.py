"""
main.py
───────
Orquestrador principal do imovel-bot. Dashboard-only — sem Telegram
(removido por pedido do usuário): cada rodada só coleta, pontua e
atualiza data/dashboard.html, nunca envia nada por conta própria.

Uso:
  python main.py                → roda Tier 1 (mercado) — padrão
  python main.py --tier 1       → idem, explícito
  python main.py --tier 3       → Tier 3 (leilão)
"""

import argparse, copy, logging, os, yaml

from scrapers  import olx, zap, quintoandar
from scorers   import mercado as scorer_mercado
from notifier  import dashboard
from utils     import historico
from utils.bairro import normalizar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

REFERENCIAS_CALIBRADAS_PATH = "referencias_calibradas.yaml"


# ── Config ────────────────────────────────────────────────────

def carregar_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return _mesclar_calibragem(cfg)


def _mesclar_calibragem(cfg: dict, path: str = REFERENCIAS_CALIBRADAS_PATH) -> dict:
    """
    Se calibragem/calibrar.py já rodou e gerou referencias_calibradas.yaml,
    sobrescreve compra_m2 dos bairros correspondentes. config.yaml nunca é
    editado por esse processo — ele continua sendo a fonte curada à mão,
    com todos os comentários intactos. Isso só é uma camada por cima.
    """
    if not os.path.exists(path):
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as f:
            calibragem = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Falha ao ler {path}, ignorando calibragem: {e}")
        return cfg

    regions_calibradas = calibragem.get("regions", {})
    regions_cfg = cfg.get("mercado", {}).get("regions", {})
    aplicados = 0

    for regiao_key, bairros in regions_calibradas.items():
        alvo = regions_cfg.get(regiao_key, {}).get("bairros_referencia", {})
        for bairro, dados in bairros.items():
            if bairro in alvo and dados.get("compra_m2"):
                alvo[bairro]["compra_m2"] = dados["compra_m2"]
                aplicados += 1

    # Se multi_regiao estiver desligado, aplica também no bloco top-level
    # (usa sp_capital como referência, já que é o cenário padrão single-região)
    top_refs = cfg.get("bairros_referencia", {})
    sp_calibrado = regions_calibradas.get("sp_capital", {})
    for bairro, dados in sp_calibrado.items():
        if bairro in top_refs and dados.get("compra_m2"):
            top_refs[bairro]["compra_m2"] = dados["compra_m2"]
            aplicados += 1

    if aplicados:
        logger.info(f"Calibragem aplicada: {aplicados} referência(s) de compra_m2 atualizadas "
                     f"({calibragem.get('gerado_em', 'data desconhecida')})")
    return cfg


# ── Tier 1 — Mercado ─────────────────────────────────────────

def rodar_tier1(cfg: dict):
    logger.info("═══ TIER 1 — Mercado ═══")
    mc = cfg.get("mercado", {})
    resultados_por_regiao: dict = {}

    if mc.get("multi_regiao") and mc.get("regions"):
        total = 0
        for chave, info in mc["regions"].items():
            nome = info.get("display_name", chave)
            logger.info(f"--- Região: {nome} ---")
            cfg_regiao = _construir_config_regiao(cfg, info)
            aprovados = _coletar_e_pontuar(cfg_regiao, label=nome)
            total += len(aprovados)
            resultados_por_regiao[nome] = aprovados
        logger.info(f"Tier 1 total (todas as regiões): {total} aprovados")
    else:
        nome = cfg.get("mercado", {}).get("cidade", "Resultados")
        resultados_por_regiao[nome] = _coletar_e_pontuar(cfg)

    # Dashboard local (uma aba por cidade, cards por aprovado) — nunca
    # pode derrubar a rodada de produção; falha aqui é só um warning,
    # não interrompe nada.
    try:
        caminho = dashboard.salvar_e_abrir(resultados_mercado=resultados_por_regiao)
        logger.info(f"📊 Dashboard: {caminho}")
    except Exception as e:
        logger.warning(f"Falha ao gerar dashboard (não crítico): {e}")


def _construir_config_regiao(cfg: dict, info: dict) -> dict:
    """
    Cria uma cópia do config com os parâmetros da região sobrepostos.
    Mantém tudo mais (geo, db_path) intacto.
    """
    cfg_regiao = copy.deepcopy(cfg)
    mc = cfg_regiao.setdefault("mercado", {})

    for campo in ("preco_max", "preco_min", "score_minimo", "slug_olx", "slug_zap", "quintoandar"):
        if info.get(campo) is not None:
            mc[campo] = info[campo]

    mc["cidades_excluidas"] = info.get("cidades_excluidas", [])
    cfg_regiao["bairros_referencia"] = info.get("bairros_referencia", {})
    return cfg_regiao


def _viva_real_aplicavel(cfg: dict) -> bool:
    """
    scrapers/zap.py busca VivaReal numa URL fixa de São Paulo capital
    (SEARCH_URL["VIVA_REAL"]) — ao contrário do ZAP, não aceita slug
    por região. Chamar em Campinas/Piracicaba traria resultados de SP
    capital contaminando a região errada. Só ativa quando a região
    atual é SP capital (slug_zap == "sp+sao-paulo") ou quando
    multi_regiao está desligado (o bloco 'mercado' top-level deste
    config é, por convenção, também SP capital).
    """
    slug_zap = cfg.get("mercado", {}).get("slug_zap")
    return slug_zap in (None, "sp+sao-paulo")


def _coletar_e_pontuar(cfg: dict, label: str = "") -> list:
    """Coleta OLX+ZAP+QuintoAndar(+VivaReal), pontua e retorna os aprovados."""
    db = cfg["db_path"]

    fontes = [
        ("OLX", olx.buscar, {"cfg": cfg}),
        ("ZAP", zap.buscar, {"cfg": cfg, "portal": "ZAP"}),
        ("QuintoAndar", quintoandar.buscar, {"cfg": cfg}),
    ]
    if _viva_real_aplicavel(cfg):
        fontes.append(("VivaReal", zap.buscar, {"cfg": cfg, "portal": "VIVA_REAL"}))

    listings = []
    for nome, fn, kwargs in fontes:
        try:
            novos = fn(**kwargs)
            listings.extend(novos)
            logger.info(f"{nome}{f' [{label}]' if label else ''}: {len(novos)} listings coletados")
        except Exception as e:
            logger.error(f"{nome} falhou{f' [{label}]' if label else ''}: {e}")

    # Grava TODO listing coletado (não só os aprovados) no histórico de
    # preços — é a base da mediana móvel usada por calibragem/calibrar.py
    # para Campinas/Piracicaba. Gravar só os aprovados enviesaria a
    # mediana para baixo.
    try:
        historico.registrar_listings(
            db, listings, cfg.get("bairros_referencia", {}), regiao=label or None,
        )
    except Exception as e:
        logger.warning(f"Falha ao gravar histórico de preços (não crítico): {e}")

    aprovados = scorer_mercado.filtrar_e_ordenar(listings, cfg)
    logger.info(f"⭐ {len(aprovados)} aprovados" + (f" [{label}]" if label else ""))
    return aprovados


# ── Tier 3 — Leilão ─────────────────────────────────────────────

def rodar_tier3(cfg: dict):
    """Usa só requests (scrapers/caixa_leilao.py, scrapers/resale.py) — não precisa de playwright."""
    logger.info("═══ TIER 3 — Leilão ═══")
    try:
        from scrapers import caixa_leilao, resale
        from scorers  import leilao as scorer_leilao
    except ImportError as e:
        logger.error(f"Tier 3 indisponível — dependência faltando: {e}")
        return

    fontes = [("Caixa", caixa_leilao.buscar), ("Resale", resale.buscar)]

    listings = []
    for nome, fn in fontes:
        try:
            novos = fn(cfg)
            listings.extend(novos)
            logger.info(f"{nome}: {len(novos)} leilões coletados")
        except Exception as e:
            logger.error(f"{nome} falhou: {e}")

    if not listings:
        logger.warning("Tier 3: nenhum leilão coletado de nenhuma fonte")
        return

    aprovados = scorer_leilao.filtrar_e_ordenar(listings, cfg)
    logger.info(f"⭐ {len(aprovados)} aprovados")

    # Dashboard local (uma aba por cidade, igual ao Mercado; a fonte —
    # Caixa/Resale — aparece no próprio card). Mesma lógica de segurança
    # do Tier 1: nunca pode derrubar a rodada por causa disso. Ver
    # notifier/dashboard.py — Tier 1 e Tier 3 rodam em invocações
    # separadas de main.py, então cada um só atualiza a própria seção
    # (mercado/leilão) do dashboard, sem apagar a outra.
    resultados_por_cidade = _agrupar_leilao_por_cidade(
        aprovados, cfg.get("leilao", {}).get("cidades", []),
    )

    try:
        caminho = dashboard.salvar_e_abrir(resultados_leilao=resultados_por_cidade)
        logger.info(f"📊 Dashboard: {caminho}")
    except Exception as e:
        logger.warning(f"Falha ao gerar dashboard (não crítico): {e}")


def _agrupar_leilao_por_cidade(aprovados: list, cidades_cfg: list) -> dict:
    """
    Agrupa aprovados de leilão por cidade, na ordem de leilao.cidades.
    Caixa devolve a cidade em caixa alta/sem acento ("SAO PAULO") e a
    Resale com acento — normaliza pra casar com o nome canônico do
    config; cidade fora da lista mantém o texto original.
    """
    canonico = {normalizar(c): c for c in cidades_cfg}
    grupos: dict = {}
    for r in aprovados:
        bruto = r.listing.cidade or "Cidade não informada"
        nome = canonico.get(normalizar(bruto), bruto)
        grupos.setdefault(nome, []).append(r)

    ordem = {c: i for i, c in enumerate(cidades_cfg)}
    return dict(sorted(grupos.items(), key=lambda kv: ordem.get(kv[0], len(ordem))))


# ── Entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="imovel-bot")
    parser.add_argument("--tier", type=int, choices=[1, 3], default=1,
                         help="Tier a rodar. Padrão: 1 (mercado).")
    args = parser.parse_args()

    cfg = carregar_config()
    historico.inicializar(cfg["db_path"])

    if args.tier == 1:
        rodar_tier1(cfg)
    elif args.tier == 3:
        rodar_tier3(cfg)

    logger.info("✅ imovel-bot concluído")


if __name__ == "__main__":
    main()
