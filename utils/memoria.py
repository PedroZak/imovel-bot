"""
utils/memoria.py
────────────────
Memória de anúncios entre rodadas.

Problema: o ZAP/VivaReal embaralha a ordem dos resultados a cada consulta
(duas rodadas seguidas repetem só ~40% dos anúncios), a OLX toma 403 no
GitHub Actions, e o bot só lê as primeiras páginas da cidade. Um anúncio
visto numa rodada pode, portanto, simplesmente não aparecer na seguinte —
e como o dashboard mostra só a última rodada, ele sumia.

Solução: todo anúncio coletado é gravado em `anuncios_memoria` (mesmo
SQLite do histórico de preços, que no Actions já persiste via
actions/cache). Na rodada seguinte, anúncios vistos nos últimos
`DIAS_RETENCAO` dias que NÃO vieram agora voltam à lista, marcados com
`visto_por_ultimo`, e passam pelo scorer de novo (config e referências de
preço atuais — nada fica "congelado").

A memória é uma melhoria: qualquer falha aqui devolve só os anúncios
frescos, nunca derruba a rodada.
"""

import dataclasses
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils.bairro import resolver_bairro, texto_localizacao

logger = logging.getLogger(__name__)

DIAS_RETENCAO = 7

# Formato fixo em UTC: comparação de texto no SQLite == comparação de data.
_FMT = "%Y-%m-%dT%H:%M:%S+00:00"

_ARREDONDAR_PRECO = 500   # mesmo critério do antigo dedup por conteúdo
_ARREDONDAR_AREA  = 2


def inicializar(db_path: str):
    """Cria a tabela da memória (idempotente)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS anuncios_memoria (
            escopo         TEXT NOT NULL,
            fonte          TEXT NOT NULL,
            listing_id     TEXT NOT NULL,
            primeiro_visto TEXT NOT NULL,
            ultimo_visto   TEXT NOT NULL,
            payload        TEXT NOT NULL,
            PRIMARY KEY (escopo, fonte, listing_id)
        );
        CREATE INDEX IF NOT EXISTS idx_memoria_escopo
            ON anuncios_memoria(escopo, ultimo_visto);
    """)
    conn.commit()
    conn.close()


def mesclar(
    db_path: str,
    escopo: str,
    frescos: list,
    classe,
    refs: Optional[dict] = None,
    dias: int = DIAS_RETENCAO,
    agora: Optional[datetime] = None,
) -> tuple[list, dict]:
    """
    Grava os anúncios `frescos` (coletados agora) e devolve
    (frescos + recuperados, recuperados_por_fonte).

    escopo  — chave da memória (nome da região, ou "leilao"): cada região
              recupera só os próprios anúncios.
    classe  — Listing ou LeilaoListing, usada pra reconstruir os recuperados.
    refs    — bairros_referencia (só mercado). Quando informado: (1) só
              guarda anúncio de bairro-alvo, que é o único que o scorer
              pode aprovar; (2) um recuperado cujo conteúdo (bairro +
              preço + área + quartos arredondados) bate com um anúncio
              fresco é descartado — é o mesmo imóvel repostado com ID
              novo, e sem isso a memória duplicaria o card.
    """
    try:
        return _mesclar(db_path, escopo, frescos, classe, refs, dias, agora)
    except Exception as e:
        logger.warning(f"Memória de anúncios indisponível [{escopo}] (não crítico): {e}")
        return list(frescos), {}


def _mesclar(db_path, escopo, frescos, classe, refs, dias, agora):
    agora   = agora or datetime.now(timezone.utc)
    agora_s = agora.strftime(_FMT)
    corte_s = (agora - timedelta(days=dias)).strftime(_FMT)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM anuncios_memoria WHERE ultimo_visto < ?", (corte_s,))

        chaves_frescas: set = set()
        impressoes: set = set()
        for l in frescos:
            chaves_frescas.add((l.fonte, str(l.id)))
            imp = _impressao(l, refs)
            if imp:
                impressoes.add(imp)
            if refs is not None and not resolver_bairro(texto_localizacao(l), refs):
                continue
            conn.execute(
                """INSERT INTO anuncios_memoria
                       (escopo, fonte, listing_id, primeiro_visto, ultimo_visto, payload)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(escopo, fonte, listing_id) DO UPDATE SET
                       ultimo_visto = excluded.ultimo_visto,
                       payload      = excluded.payload""",
                (escopo, l.fonte, str(l.id), agora_s, agora_s, _serializar(l)),
            )

        linhas = conn.execute(
            """SELECT fonte, listing_id, ultimo_visto, payload
                 FROM anuncios_memoria
                WHERE escopo = ? AND ultimo_visto >= ?
                ORDER BY ultimo_visto DESC""",
            (escopo, corte_s),
        ).fetchall()
        conn.commit()
    finally:
        conn.close()

    campos = {f.name for f in dataclasses.fields(classe) if f.init}
    recuperados: list = []
    por_fonte: dict = {}

    for fonte, lid, ultimo, payload in linhas:
        if (fonte, lid) in chaves_frescas:
            continue
        try:
            obj = classe(**{k: v for k, v in json.loads(payload).items() if k in campos})
        except Exception as e:
            logger.debug(f"Memória: registro {fonte}/{lid} ilegível, ignorado ({e})")
            continue
        imp = _impressao(obj, refs)
        if imp:
            if imp in impressoes:
                continue
            impressoes.add(imp)
        obj.visto_por_ultimo = ultimo
        recuperados.append(obj)
        por_fonte[fonte] = por_fonte.get(fonte, 0) + 1

    if recuperados:
        logger.info(
            f"Memória [{escopo}]: {len(recuperados)} anúncio(s) recuperado(s) "
            f"(vistos nos últimos {dias} dias, ausentes nesta rodada)"
        )
    return list(frescos) + recuperados, por_fonte


def _serializar(listing) -> str:
    dados = dataclasses.asdict(listing)
    dados.pop("visto_por_ultimo", None)
    return json.dumps(dados, ensure_ascii=False, default=str)


def _impressao(l, refs: Optional[dict]) -> Optional[str]:
    """Assinatura de conteúdo do imóvel (só mercado, quando há refs)."""
    if refs is None or not getattr(l, "preco", None) or not getattr(l, "area", None):
        return None
    chave = resolver_bairro(texto_localizacao(l), refs) or l.bairro or ""
    preco = round(l.preco / _ARREDONDAR_PRECO) * _ARREDONDAR_PRECO
    area  = round(l.area / _ARREDONDAR_AREA) * _ARREDONDAR_AREA
    return f"{chave.strip().lower()}|{preco:.0f}|{area:.0f}|{l.quartos or 0}"
