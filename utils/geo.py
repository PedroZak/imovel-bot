"""
utils/geo.py
────────────
Determina se um imóvel está dentro do raio de caminhada
até o escritório via 3 níveis de fallback + cache SQLite.

Nível 1: lat/lng direto do anúncio       (mais preciso)
Nível 2: geocodificar endereço (Nominatim, OSM, gratuito)
Nível 3: centroide do bairro              (tabela estática)
Nível 4: None                             (scorer não penaliza)
"""

from math import radians, sin, cos, sqrt, atan2
import sqlite3, time, logging, requests
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Configuração ─────────────────────────────────────────────
ESCRITORIO    = (-23.5874, -46.6785)   # Leopoldo Couto Magalhães Jr, 700
DISTANCIA_MAX = 3.0                    # km

# Nível 3: centroides por bairro (fallback quando não há coords nem endereço)
CENTROIDES: dict[str, Tuple[float, float]] = {
    "itaim bibi":      (-23.5874, -46.6785),
    "jardim paulista": (-23.5744, -46.6604),
    "pinheiros":       (-23.5617, -46.6836),
    "moema":           (-23.6056, -46.6638),
    "paraíso":         (-23.5756, -46.6487),
    "vila mariana":    (-23.5911, -46.6332),
}

NOMINATIM_URL     = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "imovel-bot/1.0 (pedro@gmail.com)"}


# ── API pública ───────────────────────────────────────────────

def eh_caminhavel(
    imovel: dict,
    db_path: str,
    distancia_max: float = DISTANCIA_MAX,
) -> Optional[bool]:
    """
    True  → dentro do raio (bônus no scorer)
    False → fora do raio
    None  → não foi possível determinar (sem penalidade)
    """
    coords = _get_coordenadas(imovel, db_path)
    if coords is None:
        return None
    dist = _haversine(*coords, *ESCRITORIO)
    logger.debug(f"[geo] id={imovel.get('id')} dist={dist:.2f}km")
    return dist <= distancia_max


# ── Resolução de coordenadas (3 níveis) ──────────────────────

def _get_coordenadas(
    imovel: dict,
    db_path: str,
) -> Optional[Tuple[float, float]]:

    lat = imovel.get("lat")
    lng = imovel.get("lng") or imovel.get("lon")
    if lat and lng:
        return (float(lat), float(lng))

    endereco = imovel.get("endereco") or imovel.get("logradouro", "")
    bairro   = imovel.get("bairro", "")
    cidade   = imovel.get("cidade", "São Paulo")

    if endereco:
        query  = f"{endereco}, {bairro}, {cidade}, Brasil"
        cached = _cache_get(db_path, query)
        if cached:
            return cached
        coords = _geocodificar(query)
        if coords:
            _cache_set(db_path, query, coords)
            return coords

    coords = _centroide_bairro(bairro)
    if coords:
        return coords

    logger.debug(f"[geo] localização não determinada: id={imovel.get('id')}")
    return None


def _centroide_bairro(bairro: str) -> Optional[Tuple[float, float]]:
    bk = bairro.lower().strip()
    for key, coords in CENTROIDES.items():
        if key in bk or bk in key:
            logger.debug(f"[geo] usando centroide: {key}")
            return coords
    return None


# ── Geocoding via Nominatim ───────────────────────────────────

def _geocodificar(query: str) -> Optional[Tuple[float, float]]:
    """Geocodifica via Nominatim (OSM). Gratuito, sem API key. Limite: 1 req/s."""
    try:
        time.sleep(1.1)
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            return (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception as e:
        logger.debug(f"[geo] geocoding falhou '{query}': {e}")
    return None


# ── Cache SQLite ─────────────────────────────────────────────

def _cache_get(db_path: str, query: str) -> Optional[Tuple[float, float]]:
    _init_cache(db_path)
    try:
        conn = sqlite3.connect(db_path)
        row  = conn.execute(
            "SELECT lat, lng FROM geo_cache WHERE query = ?", (query,)
        ).fetchone()
        conn.close()
        return (row[0], row[1]) if row else None
    except Exception:
        return None


def _cache_set(db_path: str, query: str, coords: Tuple[float, float]):
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO geo_cache (query, lat, lng) VALUES (?,?,?)",
            (query, coords[0], coords[1]),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[geo] erro cache set: {e}")


def _init_cache(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geo_cache (
            query     TEXT PRIMARY KEY,
            lat       REAL NOT NULL,
            lng       REAL NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


# ── Haversine ────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos geográficos."""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (sin(dlat / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2)
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
