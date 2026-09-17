"""
utils/historico.py
───────────────────
Histórico de preços coletados — base da autocalibragem "autodidata".

Grava TODO listing com preço+área válidos, independente de ter passado
no score ou não. Isso é proposital: se só guardássemos os aprovados, a
mediana ficaria enviesada para baixo (só vendo os já considerados baratos)
e o sistema perderia a capacidade de perceber quando o próprio mercado
mudou de patamar.

Usado por calibragem/calibrar.py para calcular a mediana móvel de
compra_m2 por bairro a partir dos dados que o bot mesmo coletou —
essencial para Campinas/Piracicaba, que não têm uma fonte externa
confiável equivalente ao Atlas (SP capital).
"""

import logging
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scrapers.base import Listing
from utils.bairro  import resolver_bairro, texto_localizacao

logger = logging.getLogger(__name__)


def inicializar(db_path: str):
    """Cria a tabela de histórico (idempotente)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS precos_historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bairro      TEXT NOT NULL,
            regiao      TEXT,
            fonte       TEXT NOT NULL,
            preco       REAL NOT NULL,
            area        REAL NOT NULL,
            preco_m2    REAL NOT NULL,
            coletado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_precos_bairro
            ON precos_historico(bairro, coletado_em);
    """)
    conn.commit()
    conn.close()


def registrar_listings(
    db_path: str,
    listings: list[Listing],
    refs: dict,
    regiao: Optional[str] = None,
):
    """
    Grava no histórico todos os listings com preço+área válidos,
    resolvendo o bairro pela mesma lógica usada no scorer (garante
    que a mediana fica na mesma chave usada em bairros_referencia).
    Listings cujo bairro não bate com nenhuma referência conhecida
    são ignorados (não sabemos onde tabular).
    """
    conn = sqlite3.connect(db_path)
    gravados = 0

    for l in listings:
        if not l.preco or not l.area or l.area <= 0:
            continue
        bairro_key = resolver_bairro(texto_localizacao(l), refs)
        if not bairro_key:
            continue

        preco_m2 = l.preco / l.area
        conn.execute(
            """INSERT INTO precos_historico
               (bairro, regiao, fonte, preco, area, preco_m2)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bairro_key, regiao, l.fonte, l.preco, l.area, preco_m2),
        )
        gravados += 1

    conn.commit()
    conn.close()
    logger.info(f"Histórico: {gravados}/{len(listings)} listings gravados"
                + (f" [{regiao}]" if regiao else ""))


@dataclass
class MedianaResult:
    bairro:   str
    mediana:  float
    amostras: int
    minimo:   float
    maximo:   float


def mediana_movel(
    db_path: str,
    bairro: str,
    dias: int = 60,
    min_amostras: int = 15,
) -> Optional[MedianaResult]:
    """
    Calcula a mediana de preco_m2 para um bairro nos últimos N dias.
    Retorna None se não houver amostras suficientes — nesse caso o
    chamador deve manter a referência manual/curada como está.
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT preco_m2 FROM precos_historico
           WHERE bairro = ?
             AND coletado_em >= datetime('now', ?)""",
        (bairro, f"-{dias} days"),
    ).fetchall()
    conn.close()

    valores = [r[0] for r in rows]
    if len(valores) < min_amostras:
        logger.debug(
            f"Mediana móvel '{bairro}': só {len(valores)} amostras "
            f"(mínimo {min_amostras}) — mantendo referência manual"
        )
        return None

    return MedianaResult(
        bairro=bairro,
        mediana=round(statistics.median(valores), 2),
        amostras=len(valores),
        minimo=round(min(valores), 2),
        maximo=round(max(valores), 2),
    )


def mediana_movel_todos_bairros(
    db_path: str,
    bairros: list[str],
    dias: int = 60,
    min_amostras: int = 15,
) -> dict[str, MedianaResult]:
    """Calcula a mediana móvel de todos os bairros informados de uma vez."""
    resultado = {}
    for bairro in bairros:
        m = mediana_movel(db_path, bairro, dias, min_amostras)
        if m:
            resultado[bairro] = m
    return resultado
