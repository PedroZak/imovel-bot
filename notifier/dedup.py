"""
notifier/dedup.py
─────────────────
Deduplicação via SQLite.
Garante que o mesmo imóvel não seja notificado duas vezes.
Também inicializa a tabela de cache geo (utils/geo.py).

Duas camadas de dedup:
  1. Por (id, fonte, canal) — exata, pega o caso trivial de rodar o
     bot de novo e ver o mesmo anúncio da mesma fonte.
  2. Por "fingerprint" de conteúdo (bairro + preço + área + quartos)
     — pega o caso real observado pelo usuário: o mesmo apartamento
     reanunciado com ID novo (repost no OLX) ou cross-postado em
     portais diferentes (ZAP e VivaReal compartilham inventário; sem
     isso, ativar VivaReal duplicaria quase todo notificação do ZAP).
     Preço/área são arredondados antes de virar fingerprint — dois
     anúncios do MESMO imóvel raramente batem o centavo exato entre
     portais, mas caem na mesma faixa arredondada.
"""

import sqlite3, logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ARREDONDAR_PRECO = 500   # agrupa preços dentro da mesma faixa de R$500
_ARREDONDAR_AREA   = 2     # agrupa área dentro de 2m²


def inicializar(db_path: str):
    """Cria todas as tabelas necessárias (idempotente)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vistos (
            id       TEXT NOT NULL,
            fonte    TEXT NOT NULL,
            canal    TEXT NOT NULL,
            visto_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id, fonte, canal)
        );

        CREATE TABLE IF NOT EXISTS vistos_fingerprint (
            fingerprint TEXT NOT NULL,
            canal       TEXT NOT NULL,
            id          TEXT,
            fonte       TEXT,
            visto_em    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (fingerprint, canal)
        );

        CREATE TABLE IF NOT EXISTS geo_cache (
            query     TEXT PRIMARY KEY,
            lat       REAL NOT NULL,
            lng       REAL NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    logger.info(f"DB inicializado: {db_path}")


def ja_visto(db_path: str, listing_id: str, fonte: str, canal: str) -> bool:
    conn = sqlite3.connect(db_path)
    row  = conn.execute(
        "SELECT 1 FROM vistos WHERE id=? AND fonte=? AND canal=?",
        (listing_id, fonte, canal),
    ).fetchone()
    conn.close()
    return row is not None


def marcar_visto(db_path: str, listing_id: str, fonte: str, canal: str):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO vistos (id, fonte, canal) VALUES (?,?,?)",
        (listing_id, fonte, canal),
    )
    conn.commit()
    conn.close()


def calcular_fingerprint(
    bairro_key: str, preco: float, area: float, quartos: Optional[int],
) -> str:
    """
    Assinatura de conteúdo pra pegar o mesmo imóvel sob IDs/fontes
    diferentes. Arredonda preço e área pra absorver pequenas
    divergências entre portais (ex: um mostra R$430.650, outro
    R$430.600 pro mesmo anúncio).
    """
    preco_bucket = round(preco / _ARREDONDAR_PRECO) * _ARREDONDAR_PRECO
    area_bucket  = round(area / _ARREDONDAR_AREA) * _ARREDONDAR_AREA
    return f"{bairro_key.strip().lower()}|{preco_bucket:.0f}|{area_bucket:.0f}|{quartos or 0}"


def ja_visto_fingerprint(db_path: str, fingerprint: str, canal: str) -> bool:
    conn = sqlite3.connect(db_path)
    row  = conn.execute(
        "SELECT 1 FROM vistos_fingerprint WHERE fingerprint=? AND canal=?",
        (fingerprint, canal),
    ).fetchone()
    conn.close()
    return row is not None


def marcar_visto_fingerprint(
    db_path: str, fingerprint: str, canal: str,
    listing_id: str = "", fonte: str = "",
):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO vistos_fingerprint (fingerprint, canal, id, fonte) "
        "VALUES (?,?,?,?)",
        (fingerprint, canal, listing_id, fonte),
    )
    conn.commit()
    conn.close()
