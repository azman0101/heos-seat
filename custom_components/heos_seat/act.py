"""Niveaux des arrières de la barre HEOS, via le service UPnP Denon `ACT` (port 60006).

Mêmes requêtes que `heos_core/src/levels.rs` du dépôt heos-uniffi : lecture de la config complète,
écriture d'un fragment par enceinte (renvoyer la config complète donne une erreur 500).
"""

from __future__ import annotations

import asyncio
import html
import re

PORT = 60006
URN = "urn:schemas-denon-com:service:ACT:1"
TIMEOUT_S = 3
ZERO_DB = 12  # niveau brut = 12 + dB
MAX_RAW = 24


class BarError(Exception):
    pass


async def read_rear(host: str) -> tuple[int, int]:
    """Niveaux bruts (0..24) de Surround G et Surround D."""
    xml = html.unescape(await _soap(host, "GetSurroundSpeakerConfig", ""))
    rear = re.search(r"<Rear>(.*?)</Rear>", xml, re.S)
    if rear is None:
        raise BarError("section Rear absente de la config de la barre")

    def level(side: str) -> int:
        m = re.search(rf"<{side}>.*?<level>(\d+)</level>", rear.group(1), re.S)
        if m is None:
            raise BarError(f"niveau Rear/{side} absent")
        return int(m.group(1))

    return level("Left"), level("Right")


async def write_rear(host: str, side: str, raw: int) -> None:
    raw = min(max(raw, 0), MAX_RAW)
    fragment = (
        f"<SurroundSpeakerConfig><Rear><{side}><level>{raw}</level></{side}></Rear>"
        "</SurroundSpeakerConfig>"
    )
    await _soap(
        host,
        "SetSurroundSpeakerConfig",
        f"<SurroundSpeakerConfig>{html.escape(fragment, quote=False)}</SurroundSpeakerConfig>",
    )


async def _soap(host: str, action: str, args: str) -> str:
    """HTTP/1.0 : la barre ferme la connexion à la fin de la réponse."""
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{URN}">{args}</u:{action}></s:Body></s:Envelope>'
    ).encode()
    head = (
        f"POST /ACT/control HTTP/1.0\r\nHost: {host}:{PORT}\r\n"
        'Content-Type: text/xml; charset="utf-8"\r\n'
        f'SOAPACTION: "{URN}#{action}"\r\nContent-Length: {len(body)}\r\n\r\n'
    ).encode()
    try:
        async with asyncio.timeout(TIMEOUT_S):
            reader, writer = await asyncio.open_connection(host, PORT)
            try:
                writer.write(head + body)
                await writer.drain()
                response = (await reader.read()).decode(errors="replace")
            finally:
                writer.close()
    except (OSError, TimeoutError) as e:
        raise BarError(f"barre {host} injoignable : {e or 'délai dépassé'}") from e
    status = response.split("\r\n", 1)[0]
    if " 200" not in status:
        raise BarError(f"{action} : {status}")
    return response
