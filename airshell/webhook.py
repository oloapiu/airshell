"""Webhook delivery for AirShell.

Fire-and-forget POST to the agent gateway. Runs with a short timeout so the
main loop is never blocked by slow or unreachable endpoints.

Auth: token is sent as `Authorization: Bearer <token>` header (per OpenClaw docs).
Delivery: pass channel/to so the agent routes the response back to the user.
"""

import logging
import threading
from typing import Optional

import requests

log = logging.getLogger(__name__)

# Short timeout — we'd rather drop a webhook than stall the main loop
_TIMEOUT_S = 10


def send_webhook(
    url: str,
    token: str,
    message: str,
    deliver: bool = True,
    channel: Optional[str] = None,
    to: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> int:
    """POST a message to the gateway /hooks/agent endpoint.

    Args:
        url:      Full URL (e.g. https://<tailnet-host>/hooks/agent)
        token:    Gateway hook token (sent as Authorization Bearer header)
        message:  The alarm/event message for the agent to process
        deliver:  If True, agent response is delivered to the messaging channel
        channel:  Delivery channel (e.g. "telegram"). Uses gateway default if None.
        to:       Recipient ID (e.g. Telegram chat ID). Uses last recipient if None.
        agent_id: OpenClaw agent to route to (e.g. "airshell"). Uses gateway default if None.

    Returns:
        HTTP status code, or 0 on connection failure.
    """
    if not url:
        log.warning("Webhook URL not configured — skipping")
        return 0

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload: dict = {"message": message}
    if agent_id:
        payload["agentId"] = agent_id
    if deliver:
        payload["deliver"] = True
    if channel:
        payload["channel"] = channel
    if to:
        payload["to"] = to

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT_S)
        if resp.status_code >= 400:
            log.warning("Webhook returned %d: %s", resp.status_code, resp.text[:200])
        else:
            log.info("Webhook delivered (%d)", resp.status_code)
        return resp.status_code
    except requests.RequestException as e:
        log.warning("Webhook failed: %s", e)
        return 0


def send_webhook_async(
    url: str,
    token: str,
    message: str,
    deliver: bool = True,
    channel: Optional[str] = None,
    to: Optional[str] = None,
    agent_id: Optional[str] = None,
    callback=None,
):
    """Send a webhook in a background thread (non-blocking).

    Args:
        url:      Full URL for the webhook endpoint
        token:    Gateway hook token
        message:  The message to send
        deliver:  Forward agent response to messaging channel
        channel:  Delivery channel override
        to:       Recipient ID override
        agent_id: OpenClaw agent to route to
        callback: Optional callable(status_code) called after delivery
    """
    def _deliver():
        status = send_webhook(url, token, message, deliver=deliver,
                              channel=channel, to=to, agent_id=agent_id)
        if callback:
            callback(status)

    t = threading.Thread(target=_deliver, daemon=True)
    t.start()
