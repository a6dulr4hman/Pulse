import httpx
import logging

logger = logging.getLogger(__name__)

async def send_discord_alert(webhook_url: str, message: str) -> bool:
    if not webhook_url:
        logger.warning("No Discord webhook provided.")
        return False
    # Max size logic to avoid 400 bad requests from Discord for large summaries
    if len(message) > 1900:
        message = message[:1900] + "\n...[Truncated to fit Discord]"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json={"content": message},
                timeout=10.0
            )
            response.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Failed to send Discord alert: {e}")
        return False