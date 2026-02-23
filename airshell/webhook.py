"""Webhook delivery for AirShell.

Fire-and-forget POST to the agent gateway. Runs with a short timeout so the
main loop is never blocked by slow or unreachable endpoints.
"""

import logging
import threading

import requests

log = logging.getLogger(__name__)

# Short timeout — we'd rather drop a webhook than stall the main loop
_TIMEOUT_S = 10


def send_webhook(url: str, token: str, message: str) -> int:
    """POST a message to the gateway webhook endpoint.

    Args:
        url: Full URL (e.g. http://localhost:3456/hooks/agent)
        token: Authentication token for the gateway
        message: The message string to send

    Returns:
        HTTP status code, or 0 on connection failure.
    """
    if not url:
        log.warning("Webhook URL not configured — skipping")
        return 0

    try:
        resp = requests.post(
            url,
            json={"message": message, "token": token},
            timeout=_TIMEOUT_S,
        )
        if resp.status_code >= 400:
            log.warning("Webhook returned %d: %s", resp.status_code, resp.text[:200])
        else:
            log.info("Webhook delivered (%d)", resp.status_code)
        return resp.status_code
    except requests.RequestException as e:
        log.warning("Webhook failed: %s", e)
        return 0


def send_webhook_async(url: str, token: str, message: str,
                       callback=None):
    """Send a webhook in a background thread (non-blocking).

    Args:
        url: Full URL for the webhook endpoint
        token: Authentication token
        message: The message to send
        callback: Optional callable(status_code) called after delivery
    """
    def _deliver():
        status = send_webhook(url, token, message)
        if callback:
            callback(status)

    t = threading.Thread(target=_deliver, daemon=True)
    t.start()
