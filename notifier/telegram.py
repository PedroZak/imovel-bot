"""
notifier/telegram.py
────────────────────
Envia notificações para dois canais Telegram distintos.
Cada função de formatação é isolada → fácil mudar layout sem tocar na lógica de envio.
"""

import logging, requests
from scorers.base import ScoreResult, LeilaoScoreResult

logger      = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ── API pública ───────────────────────────────────────────────

def enviar_mercado(resultado: ScoreResult, cfg: dict) -> bool:
    canal = cfg["telegram"]["channels"]["mercado"]
    token = cfg["telegram"]["token"]
    return _enviar(token, canal, _formatar_mercado(resultado), resultado.listing.fotos)


def enviar_leilao(resultado: LeilaoScoreResult, cfg: dict) -> bool:
    canal = cfg["telegram"]["channels"]["leilao"]
    token = cfg["telegram"]["token"]
    foto  = [resultado.listing.foto] if resultado.listing.foto else []
    return _enviar(token, canal, _formatar_leilao(resultado), foto)


def enviar_texto(cfg: dict, canal: str, texto: str) -> bool:
    """
    Envia uma mensagem de texto simples (sem foto/score) para um canal.
    Usado por relatórios administrativos, como o resumo de calibragem
    (calibragem/calibrar.py) — não faz parte do fluxo de notificação
    de imóveis normal.
    """
    chat_id = cfg["telegram"]["channels"].get(canal)
    token   = cfg["telegram"]["token"]
    if not chat_id:
        logger.warning(f"Canal '{canal}' não configurado — mensagem não enviada")
        return False
    return _enviar(token, chat_id, texto)


# ── Envio HTTP ────────────────────────────────────────────────

def _enviar(token: str, chat_id: str, texto: str, fotos: list = None) -> bool:
    try:
        if fotos and fotos[0]:
            url     = TELEGRAM_API.format(token=token, method="sendPhoto")
            payload = {"chat_id": chat_id, "photo": fotos[0],
                       "caption": texto[:1024], "parse_mode": "HTML"}
        else:
            url     = TELEGRAM_API.format(token=token, method="sendMessage")
            payload = {"chat_id": chat_id, "text": texto,
                       "parse_mode": "HTML", "disable_web_page_preview": False}

        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Telegram erro: {e}")
        return False


# ── Formatação de mensagens ───────────────────────────────────

def _formatar_mercado(r: ScoreResult) -> str:
    l = r.listing
    fonte_emoji = {"olx": "🟠", "zap": "🔵", "vivareal": "🟢", "quintoandar": "🟣"}.get(l.fonte, "🏠")

    just_txt = "\n".join(f"  {j}" for j in r.justificativas) if r.justificativas else ""

    return (
        f"{fonte_emoji} <b>{l.titulo}</b>\n"
        f"📍 {l.bairro}, {l.cidade}\n\n"
        f"💰 <b>{_fmt_brl(l.preco)}</b>  |  "
        f"📐 {_fmt_m2(l.area)}  |  "
        f"🛏 {l.quartos or '?'}q\n"
        f"📊 {_fmt_brl(l.preco_m2)}/m²\n"
        f"🏢 Cond: {_fmt_brl(l.condominio)}/mês  |  "
        f"🏛 IPTU: {_fmt_brl(l.iptu)}/ano\n\n"
        f"⭐ Score: <b>{r.score}/100</b>  {_score_bar(r.score)}\n"
        f"{just_txt}\n\n"
        f"🔗 <a href='{l.url}'>Ver anúncio ({l.fonte.upper()})</a>"
    )


def _formatar_leilao(r: LeilaoScoreResult) -> str:
    l = r.listing
    fonte_label = {"caixa_leilao": "LEILÃO CAIXA", "resale": "LEILÃO RESALE"}.get(l.fonte, f"LEILÃO {l.fonte.upper()}")
    mod_emoji = {"2º Leilão": "🔥", "Licitação Aberta": "📋", "1º Leilão": "⚡"}.get(l.modalidade, "🏠")
    sit_emoji = "✅" if l.situacao == "Desocupado" else "⚠️"

    just_txt   = "\n".join(f"  {j}" for j in r.justificativas)
    alertas_txt = ("\n\n" + "\n".join(r.alertas)) if r.alertas else ""

    return (
        f"🏦 <b>{fonte_label}</b> {mod_emoji}\n"
        f"📍 {l.endereco}, {l.estado}\n\n"
        f"🏷 Lance mínimo: <b>{_fmt_brl(l.preco)}</b>\n"
        f"📋 Avaliação: {_fmt_brl(l.avaliacao)}\n"
        f"📉 Desconto: <b>{l.desconto_pct:.1f}%</b>\n"
        f"📐 {_fmt_m2(l.area)}  |  🛏 {l.quartos or '?'}q\n"
        f"{sit_emoji} {l.situacao}  |  {l.modalidade}\n\n"
        f"⭐ Score: <b>{r.score}/100</b>  {_score_bar(r.score)}\n"
        f"{just_txt}"
        f"{alertas_txt}\n\n"
        f"🔗 <a href='{l.url_edital}'>Ver edital</a>"
    )


# ── Helpers de formatação ─────────────────────────────────────

def _fmt_brl(v) -> str:
    return f"R${v:,.0f}" if v else "—"

def _fmt_m2(v) -> str:
    return f"{v:.0f}m²" if v else "—"

def _score_bar(score: float) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)
