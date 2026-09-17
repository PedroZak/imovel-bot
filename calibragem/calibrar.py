"""
calibragem/calibrar.py
────────────────────────
Orquestrador da autocalibragem. Roda mensalmente via
.github/workflows/calibragem.yml (ou manualmente: python -m calibragem.calibrar).

O que faz:
  1. SP capital  → rebusca compra_m2 no Atlas (fonte primária, sempre
     sobrescreve — é literalmente a mesma fonte que já usamos, só mais
     fresca).
  2. Campinas/Piracicaba → calcula mediana móvel dos últimos 60 dias
     a partir do que o próprio bot já coletou (utils/historico.py). Só
     sobrescreve se houver amostras suficientes (min_amostras); do
     contrário mantém a estimativa manual do config.yaml como está.
  3. Compara tudo contra os valores atuais e sinaliza divergências
     grandes (> tolerancia_alerta) — essas são justamente as
     "distorções" que interessam ao objetivo do bot.
  4. Escreve referencias_calibradas.yaml (nunca edita config.yaml
     diretamente — preserva os comentários/curadoria manual).
  5. Loga um resumo (bairros atualizados + divergências) no console.

Não precisa ficar bonito — é um script administrativo, roda sozinho.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibragem import atlas
from utils import historico

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("calibrar")

REFERENCIAS_PATH = "referencias_calibradas.yaml"
TOLERANCIA_ALERTA = 0.15   # divergência > 15% vira alerta no relatório


def carregar_config_base(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def calibrar_sp_capital(cfg: dict) -> dict:
    """Atlas é fonte primária para SP capital — sempre tenta atualizar."""
    regions = cfg.get("mercado", {}).get("regions", {})
    sp = regions.get("sp_capital", {})
    bairros = list(sp.get("bairros_referencia", {}).keys())

    if not bairros:
        logger.info("SP capital: nenhum bairro configurado, pulando Atlas")
        return {}

    logger.info(f"Atlas: buscando {len(bairros)} bairros de SP capital...")
    resultado_atlas = atlas.buscar_todos(bairros)
    logger.info(f"Atlas: {len(resultado_atlas)}/{len(bairros)} bairros retornaram valor")

    return {
        bairro: {"compra_m2": valor, "fonte": "atlas"}
        for bairro, valor in resultado_atlas.items()
    }


def calibrar_regiao_por_historico(
    cfg: dict, regiao_key: str, db_path: str, min_amostras: int = 15, dias: int = 60,
) -> dict:
    """Campinas/Piracicaba (ou qualquer região sem fonte externa) — mediana móvel."""
    regions = cfg.get("mercado", {}).get("regions", {})
    info = regions.get(regiao_key, {})
    bairros = list(info.get("bairros_referencia", {}).keys())

    if not bairros:
        return {}

    medianas = historico.mediana_movel_todos_bairros(
        db_path, bairros, dias=dias, min_amostras=min_amostras,
    )
    logger.info(
        f"{regiao_key}: mediana móvel disponível para "
        f"{len(medianas)}/{len(bairros)} bairros (mín. {min_amostras} amostras)"
    )

    return {
        bairro: {
            "compra_m2": m.mediana,
            "fonte": "mediana_movel",
            "amostras": m.amostras,
            "faixa": [m.minimo, m.maximo],
        }
        for bairro, m in medianas.items()
    }


def comparar_e_sinalizar(cfg: dict, calibrado: dict) -> list[dict]:
    """
    Compara os novos valores contra o config.yaml atual e retorna
    a lista de divergências acima da tolerância — é o que vira o
    relatório de "distorções" enviado no Telegram.
    """
    divergencias = []
    regions = cfg.get("mercado", {}).get("regions", {})

    for regiao_key, bairros_novos in calibrado.items():
        atual = regions.get(regiao_key, {}).get("bairros_referencia", {})
        for bairro, dados in bairros_novos.items():
            valor_atual = atual.get(bairro, {}).get("compra_m2")
            valor_novo  = dados["compra_m2"]
            if not valor_atual:
                continue
            diff_pct = (valor_novo - valor_atual) / valor_atual
            if abs(diff_pct) >= TOLERANCIA_ALERTA:
                divergencias.append({
                    "regiao": regiao_key,
                    "bairro": bairro,
                    "valor_config": valor_atual,
                    "valor_novo": valor_novo,
                    "diff_pct": round(diff_pct * 100, 1),
                    "fonte": dados["fonte"],
                })

    return divergencias


def escrever_referencias(calibrado: dict, divergencias: list[dict], path: str = REFERENCIAS_PATH):
    saida = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "regions": calibrado,
        "divergencias": divergencias,
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Gerado automaticamente por calibragem/calibrar.py — não editar à mão.\n")
        f.write("# main.py mescla este arquivo com config.yaml na inicialização.\n\n")
        yaml.safe_dump(saida, f, allow_unicode=True, sort_keys=False)
    logger.info(f"Escrito: {path}")


def logar_resumo(calibrado: dict, divergencias: list[dict]):
    total_bairros = sum(len(b) for b in calibrado.values())
    if total_bairros == 0:
        logger.info("Nenhum bairro calibrado nesta rodada")
        return

    logger.info(f"🔧 Calibragem — {total_bairros} bairro(s) atualizado(s)")

    if divergencias:
        logger.info(f"⚠️ {len(divergencias)} divergência(s) ≥{int(TOLERANCIA_ALERTA*100)}%:")
        for d in divergencias:
            sinal = "📈" if d["diff_pct"] > 0 else "📉"
            logger.info(
                f"  {sinal} {d['bairro']} ({d['regiao']}): "
                f"R${d['valor_config']:,.0f} → R${d['valor_novo']:,.0f} "
                f"({d['diff_pct']:+.1f}%, fonte: {d['fonte']})"
            )
    else:
        logger.info("Sem divergências relevantes — referências seguem estáveis.")


def main():
    cfg = carregar_config_base()
    db_path = cfg.get("db_path", "data/vistos.db")
    historico.inicializar(db_path)

    calibrado: dict = {}

    # 1. SP capital via Atlas (fonte primária, sempre tenta)
    sp_calibrado = calibrar_sp_capital(cfg)
    if sp_calibrado:
        calibrado["sp_capital"] = sp_calibrado

    # 2. Campinas/Piracicaba via mediana móvel do histórico próprio
    for regiao_key in ("campinas", "piracicaba"):
        if regiao_key in cfg.get("mercado", {}).get("regions", {}):
            r = calibrar_regiao_por_historico(cfg, regiao_key, db_path)
            if r:
                calibrado[regiao_key] = r

    divergencias = comparar_e_sinalizar(cfg, calibrado)
    escrever_referencias(calibrado, divergencias)
    logar_resumo(calibrado, divergencias)

    logger.info("✅ Calibragem concluída")


if __name__ == "__main__":
    main()
