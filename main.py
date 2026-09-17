"""
main.py
───────
Orquestrador principal do imovel-bot.

Uso:
  python main.py                → roda Tier 1 (mercado) — padrão
  python main.py --tier 1       → idem, explícito
  python main.py --tier 3       → Tier 3 (leilão) — ON HOLD
  python main.py --dry-run      → sem envio Telegram (só loga)

Tier 3 está em hold: não roda por padrão nem no cron agendado.
Só executa se chamado explicitamente com --tier 3.

Secrets: o config.yaml tem placeholders. Em produção (GitHub
Actions), as variáveis de ambiente abaixo sobrescrevem os
valores do arquivo automaticamente via _aplicar_env():
  TELEGRAM_TOKEN
  TELEGRAM_CHANNEL_MERCADO
  TELEGRAM_CHANNEL_LEILAO
"""

import argparse, copy, logging, os, sys, yaml
from typing import Optional

from scrapers  import olx, zap, quintoandar
from scorers   import mercado as scorer_mercado
from notifier  import telegram, dedup, dashboard
from utils     import historico
from utils.bairro import resolver_bairro, texto_localizacao

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

REFERENCIAS_CALIBRADAS_PATH = "referencias_calibradas.yaml"


# ── Config ────────────────────────────────────────────────────

def carregar_config(path: str = "config.yaml", dry_run: bool = False) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg = _mesclar_calibragem(cfg)
    return _aplicar_env(cfg, dry_run)


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


def _aplicar_env(cfg: dict, dry_run: bool = False) -> dict:
    """
    Sobrescreve valores do config.yaml com variáveis de ambiente.
    Permite usar GitHub Secrets sem expor tokens no repositório.
    Valores de env têm prioridade absoluta sobre o arquivo.
    """
    mapa = {
        "TELEGRAM_TOKEN":           ("telegram", "token"),
        "TELEGRAM_CHANNEL_MERCADO": ("telegram", "channels", "mercado"),
        "TELEGRAM_CHANNEL_LEILAO":  ("telegram", "channels", "leilao"),
    }
    for env_key, cfg_path in mapa.items():
        valor = os.getenv(env_key)
        if valor:
            d = cfg
            for k in cfg_path[:-1]:
                d = d.setdefault(k, {})
            d[cfg_path[-1]] = valor
            logger.debug(f"Config sobrescrito via env: {env_key}")

    _validar_config(cfg, dry_run)
    return cfg


def _validar_config(cfg: dict, dry_run: bool = False):
    """
    Falha rápido se configuração obrigatória estiver faltando.

    Em --dry-run nada é enviado pro Telegram, então token/canal não
    fazem falta — permite rodar o dashboard-only (ex: workflow do
    GitHub Actions sem enviar_telegram marcado) sem secret nenhum.
    """
    if dry_run:
        return

    erros = []
    token = cfg.get("telegram", {}).get("token", "")
    if not token or "SEU_BOT" in token:
        erros.append(
            "TELEGRAM_TOKEN não configurado (defina o secret no GitHub "
            "ou preencha config.yaml localmente)"
        )

    canais = cfg.get("telegram", {}).get("channels", {})
    if not canais.get("mercado") or "XXX" in str(canais.get("mercado", "")):
        erros.append("Canal Telegram 'mercado' não configurado")

    if erros:
        for e in erros:
            logger.error(f"Config inválido: {e}")
        sys.exit(1)


# ── Tier 1 — Mercado ─────────────────────────────────────────

def rodar_tier1(cfg: dict, dry_run: bool = False):
    logger.info("═══ TIER 1 — Mercado ═══")
    mc = cfg.get("mercado", {})
    resultados_por_regiao: dict = {}

    if mc.get("multi_regiao") and mc.get("regions"):
        total = 0
        for chave, info in mc["regions"].items():
            nome = info.get("display_name", chave)
            logger.info(f"--- Região: {nome} ---")
            cfg_regiao = _construir_config_regiao(cfg, info)
            enviados, aprovados = _coletar_pontuar_notificar(cfg_regiao, dry_run, label=nome)
            total += enviados
            resultados_por_regiao[nome] = aprovados
        logger.info(f"Tier 1 total (todas as regiões): {total} notificações")
    else:
        nome = cfg.get("mercado", {}).get("cidade", "Resultados")
        _, aprovados = _coletar_pontuar_notificar(cfg, dry_run)
        resultados_por_regiao[nome] = aprovados

    # Dashboard local (uma aba por cidade, cards por aprovado) — nunca
    # pode derrubar a rodada de produção (roda sozinha 3x/dia via
    # GitHub Actions); falha aqui é só um warning, não interrompe nada.
    try:
        caminho = dashboard.salvar_e_abrir(resultados_mercado=resultados_por_regiao)
        logger.info(f"📊 Dashboard: {caminho}")
    except Exception as e:
        logger.warning(f"Falha ao gerar dashboard (não crítico): {e}")


def _construir_config_regiao(cfg: dict, info: dict) -> dict:
    """
    Cria uma cópia do config com os parâmetros da região sobrepostos.
    Mantém tudo mais (telegram, geo, db_path) intacto.
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


def _coletar_pontuar_notificar(cfg: dict, dry_run: bool, label: str = "") -> tuple:
    """
    Coleta OLX+ZAP(+VivaReal), pontua, deduplica e notifica.
    Retorna (nº de notificações enviadas, lista de ScoreResult aprovados
    — inclui os que já tinham sido notificados antes, usada pelo
    dashboard local, que mostra todo aprovado da rodada independente
    de dedup).
    """
    db    = cfg["db_path"]
    canal = "mercado"
    refs  = cfg.get("bairros_referencia", {})

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

    enviados = _notificar(
        aprovados, canal, db, dry_run,
        fn_envio = lambda r: telegram.enviar_mercado(r, cfg),
        fn_label = lambda r: f"{r.listing.titulo} | score={r.score}" + (f" | {label}" if label else ""),
        refs = refs,
    )
    return enviados, aprovados


def _notificar(aprovados, canal, db, dry_run, fn_envio, fn_label, refs: Optional[dict] = None) -> int:
    """
    Loop de dedup + envio. Retorna quantidade enviada.

    `refs` (bairros_referencia) é opcional — só listings de mercado
    (Tier 1) têm bairro resolvível contra referências conhecidas.
    Quando presente, aplica uma segunda camada de dedup por
    fingerprint de conteúdo (bairro+preço+área+quartos) além do
    (id, fonte) exato — pega repost com ID novo e cross-post entre
    ZAP/VivaReal, que o dedup por ID sozinho não vê.

    O mesmo repost pode aparecer várias vezes dentro do MESMO lote
    coletado (visto real: 1 apartamento com 3 IDs diferentes no OLX
    na mesma rodada) — por isso `fingerprints_do_lote` é checado e
    atualizado incondicionalmente (inclusive em dry-run), e não só
    via dedup.marcar_visto_fingerprint (que só grava após envio real,
    então não pegaria duplicatas dentro do próprio lote).
    """
    enviados = 0
    fingerprints_do_lote: set[str] = set()

    for r in aprovados:
        listing    = r.listing
        lid, fonte = listing.id, listing.fonte

        if dedup.ja_visto(db, lid, fonte, canal):
            logger.debug(f"Já visto (id exato): {lid}"); continue

        fingerprint = None
        if refs and listing.preco and listing.area:
            bairro_key  = resolver_bairro(texto_localizacao(listing), refs) or listing.bairro
            fingerprint = dedup.calcular_fingerprint(
                bairro_key, listing.preco, listing.area, listing.quartos,
            )
            if fingerprint in fingerprints_do_lote:
                logger.debug(f"Repost dentro do mesmo lote: {lid} ({fonte})")
                continue
            if dedup.ja_visto_fingerprint(db, fingerprint, canal):
                logger.debug(
                    f"Já visto (mesmo imóvel sob outro id/fonte): {lid} ({fonte})"
                )
                continue
            fingerprints_do_lote.add(fingerprint)

        if dry_run:
            logger.info(f"[DRY-RUN] {fn_label(r)}")
            enviados += 1
        elif fn_envio(r):
            dedup.marcar_visto(db, lid, fonte, canal)
            if fingerprint:
                dedup.marcar_visto_fingerprint(db, fingerprint, canal, lid, fonte)
            enviados += 1

    logger.info(f"Notificações enviadas ({canal}): {enviados}")
    return enviados


# ── Tier 3 — Leilão (ON HOLD) ──────────────────────────────────

def rodar_tier3(cfg: dict, dry_run: bool = False):
    """
    Tier 3 está em hold. Só roda se chamado explicitamente via --tier 3.
    Usa só requests (scrapers/caixa_leilao.py, scrapers/resale.py) —
    não precisa de playwright, nenhuma das duas fontes exige JS pro
    fluxo de busca.
    """
    logger.warning(
        "Tier 3 (leilão) está em hold. Executando mesmo assim "
        "porque foi chamado explicitamente com --tier 3."
    )
    try:
        from scrapers import caixa_leilao, resale
        from scorers  import leilao as scorer_leilao
    except ImportError as e:
        logger.error(f"Tier 3 indisponível — dependência faltando: {e}")
        return

    db, canal = cfg["db_path"], "leilao"
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
    _notificar(
        aprovados, canal, db, dry_run,
        fn_envio = lambda r: telegram.enviar_leilao(r, cfg),
        fn_label = lambda r: f"{r.listing.titulo} | score={r.score} | desconto={r.listing.desconto_pct:.1f}%",
    )

    # Dashboard local (uma aba por fonte — Caixa/Resale). Mesma lógica
    # de segurança do Tier 1: nunca pode derrubar a rodada por causa
    # disso. Ver notifier/dashboard.py — Tier 1 e Tier 3 rodam em
    # invocações separadas de main.py, então cada um só atualiza a
    # própria seção (mercado/leilão) do dashboard, sem apagar a outra.
    fonte_labels = {"caixa_leilao": "Caixa", "resale": "Resale"}
    resultados_por_fonte: dict = {}
    for r in aprovados:
        label = fonte_labels.get(r.listing.fonte, r.listing.fonte.title())
        resultados_por_fonte.setdefault(label, []).append(r)

    try:
        caminho = dashboard.salvar_e_abrir(resultados_leilao=resultados_por_fonte)
        logger.info(f"📊 Dashboard: {caminho}")
    except Exception as e:
        logger.warning(f"Falha ao gerar dashboard (não crítico): {e}")


# ── Entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="imovel-bot")
    parser.add_argument("--tier",    type=int, choices=[1, 3], default=1,
                         help="Tier a rodar. Padrão: 1 (mercado). Tier 3 (leilão) está em hold.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = carregar_config(dry_run=args.dry_run)
    dedup.inicializar(cfg["db_path"])
    historico.inicializar(cfg["db_path"])

    if args.tier == 1:
        rodar_tier1(cfg, dry_run=args.dry_run)
    elif args.tier == 3:
        rodar_tier3(cfg, dry_run=args.dry_run)

    logger.info("✅ imovel-bot concluído")


if __name__ == "__main__":
    main()
