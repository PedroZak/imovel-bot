"""
testar_scraper.py
──────────────────
Testa OLX, ZAP e QuintoAndar — sem Telegram, sem histórico. Não precisa
de TELEGRAM_TOKEN nem de nenhum secret configurado. Roda o scorer
(igual o bot de produção) e gera um dashboard HTML local com os aprovados.

Uso:
  python testar_scraper.py                    → testa a região padrão (sp_capital)
  python testar_scraper.py --regiao campinas   → testa outra região do multi_regiao
  python testar_scraper.py --regiao all        → testa TODAS as regiões, um dashboard só
  python testar_scraper.py --fonte olx         → só OLX
  python testar_scraper.py --fonte zap         → só ZAP
  python testar_scraper.py --fonte quintoandar → só QuintoAndar
  python testar_scraper.py --fonte todos       → OLX + ZAP + QuintoAndar (padrão)

Imprime quantos anúncios cada fonte trouxe e mostra os 5 primeiros
em detalhe, pra você conferir visualmente se os campos (preço, área,
condomínio, bairro) vieram certos. Não busca VivaReal (diferente do
main.py de produção) — VivaReal é só ZAP com portal diferente, sem
necessidade de teste isolado extra.
"""

import argparse
import logging
import yaml

from scrapers import olx, zap, quintoandar
from scorers import mercado as scorer_mercado
from notifier import dashboard
from utils.bairro import resolver_bairro

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("testar_scraper")


def carregar_config_regiao(regiao: str) -> dict:
    """
    Lê config.yaml puro (sem passar por main.py — sem validação de
    Telegram, sem merge de calibragem). Se multi_regiao estiver ativo,
    usa os parâmetros da região pedida; senão, usa o bloco 'mercado' padrão.
    """
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    mc = cfg.get("mercado", {})
    if mc.get("multi_regiao") and mc.get("regions"):
        info = mc["regions"].get(regiao)
        if not info:
            disponiveis = list(mc["regions"].keys())
            raise SystemExit(
                f"Região '{regiao}' não existe no config.yaml. "
                f"Disponíveis: {disponiveis}"
            )
        # Sobrepõe os campos da região no bloco mercado (mesma lógica
        # que main.py:_construir_config_regiao usa)
        for campo in ("preco_max", "preco_min", "score_minimo", "slug_olx", "slug_zap", "quintoandar"):
            if info.get(campo) is not None:
                mc[campo] = info[campo]
        mc["cidades_excluidas"] = info.get("cidades_excluidas", [])
        # Troca também os bairros de referência — sem isso, testar
        # --regiao campinas continuaria comparando contra os bairros
        # de SP capital (Vila Mariana etc.), e nunca daria match.
        cfg["bairros_referencia"] = info.get("bairros_referencia", {})
        print(f"🌍 Testando região: {info.get('display_name', regiao)}")
    else:
        print("🌍 Testando com config.mercado padrão (multi_regiao desligado)")

    return cfg


def mostrar_resultados(nome: str, listings: list):
    print(f"\n{'='*60}")
    print(f"{nome}: {len(listings)} anúncios coletados")
    print(f"{'='*60}")

    if not listings:
        print("⚠️  Zero resultados. Possíveis causas:")
        print("   - Site bloqueou a requisição (403/Cloudflare)")
        print("   - __NEXT_DATA__ não encontrado (site mudou estrutura)")
        print(f"   - Confira data/debug_{nome.lower()}.html pra inspecionar")
        return

    print(f"\nPrimeiros {min(5, len(listings))} anúncios:\n")
    for l in listings[:5]:
        print(f"  📍 {l.titulo}")
        print(f"     Bairro: {l.bairro} | Cidade: {l.cidade}")
        print(f"     Preço: R${l.preco:,.0f}" if l.preco else "     Preço: (não capturado)")
        print(f"     Área: {l.area}m²" if l.area else "     Área: (não capturada)")
        print(f"     Quartos: {l.quartos} | Vagas: {l.vagas}")
        print(f"     Condomínio: R${l.condominio:,.0f}" if l.condominio else "     Condomínio: (não informado)")
        print(f"     Preço/m²: R${l.preco_m2:,.0f}" if l.preco_m2 else "     Preço/m²: —")
        print(f"     URL: {l.url}")
        print()


def mostrar_match_bairro(nome: str, listings: list, refs: dict):
    """
    O scraper busca a região inteira, não bairro por bairro — o filtro
    de bairro-alvo só acontece no scorer. Isso mostra a diferença entre
    "quantos o scraper trouxe" e "quantos são de fato dos seus bairros
    configurados", pra não confundir os dois números.
    """
    if not listings or not refs:
        return

    matches = []
    for l in listings:
        bairro_resolvido = resolver_bairro(l.bairro or l.titulo, refs)
        if bairro_resolvido:
            matches.append((l, bairro_resolvido))

    print(f"\n{'-'*60}")
    print(f"{nome}: {len(matches)}/{len(listings)} batem com seus bairros configurados "
          f"({', '.join(refs.keys())})")
    print(f"{'-'*60}")
    for l, bairro in matches[:10]:
        preco_fmt = f"R${l.preco:,.0f}" if l.preco else "—"
        print(f"  ✅ [{bairro}] {preco_fmt} — {l.titulo[:60]}")


def rodar_regiao(regiao: str, fonte: str) -> tuple[str, list]:
    """
    Busca (OLX/ZAP/QuintoAndar conforme `fonte`), mostra o bruto no
    console (igual sempre fez) e pontua com o mesmo scorer do bot de
    produção. Retorna (nome de exibição da região, lista de ScoreResult
    aprovados) — a lista alimenta o dashboard.
    """
    cfg = carregar_config_regiao(regiao)
    listings_totais = []

    if fonte in ("olx", "todos", "ambos"):
        try:
            listings_olx = olx.buscar(cfg)
            mostrar_resultados("OLX", listings_olx)
            mostrar_match_bairro("OLX", listings_olx, cfg.get("bairros_referencia", {}))
            listings_totais.extend(listings_olx)
        except Exception as e:
            logger.error(f"OLX falhou com exceção: {e}")

    if fonte in ("zap", "todos", "ambos"):
        try:
            listings_zap = zap.buscar(cfg, portal="ZAP")
            mostrar_resultados("ZAP", listings_zap)
            mostrar_match_bairro("ZAP", listings_zap, cfg.get("bairros_referencia", {}))
            listings_totais.extend(listings_zap)
        except Exception as e:
            logger.error(f"ZAP falhou com exceção: {e}")

    if fonte in ("quintoandar", "todos"):
        try:
            listings_qa = quintoandar.buscar(cfg)
            mostrar_resultados("QuintoAndar", listings_qa)
            mostrar_match_bairro("QuintoAndar", listings_qa, cfg.get("bairros_referencia", {}))
            listings_totais.extend(listings_qa)
        except Exception as e:
            logger.error(f"QuintoAndar falhou com exceção: {e}")

    aprovados = scorer_mercado.filtrar_e_ordenar(listings_totais, cfg)
    print(f"\n⭐ {len(aprovados)} aprovados (score ≥ {cfg['mercado']['score_minimo']})")

    mc = cfg.get("mercado", {})
    if mc.get("multi_regiao") and mc.get("regions"):
        nome = mc["regions"].get(regiao, {}).get("display_name", regiao)
    else:
        nome = mc.get("cidade", "Resultados")

    return nome, aprovados


def _regioes_disponiveis() -> list[str]:
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return list(cfg.get("mercado", {}).get("regions", {}).keys())


def main():
    parser = argparse.ArgumentParser(description="Testa scrapers isoladamente (sem Telegram)")
    parser.add_argument("--regiao", default="sp_capital",
                         help="Região do multi_regiao a testar (padrão: sp_capital). "
                              "'all' testa todas de uma vez, um dashboard só.")
    parser.add_argument("--fonte", choices=["olx", "zap", "quintoandar", "todos", "ambos"], default="todos")
    args = parser.parse_args()

    resultados_por_regiao = {}

    if args.regiao == "all":
        regioes = _regioes_disponiveis()
        for i, regiao in enumerate(regioes):
            if i > 0:
                print(f"\n{'-'*60}\n")
            nome, aprovados = rodar_regiao(regiao, args.fonte)
            resultados_por_regiao[nome] = aprovados
    else:
        nome, aprovados = rodar_regiao(args.regiao, args.fonte)
        resultados_por_regiao[nome] = aprovados

    print("\n✅ Teste de scraper concluído (nenhuma mensagem enviada, nenhum secret necessário)")

    caminho = dashboard.salvar_e_abrir(resultados_por_regiao)
    print(f"\n📊 Dashboard: {caminho}")


if __name__ == "__main__":
    main()
