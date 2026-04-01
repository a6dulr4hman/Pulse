import logging
from extensions import CHAT_EXTENSIONS

logger = logging.getLogger(__name__)

async def send_chat_alert(provider: str, webhook_url: str, message: str) -> bool:
    if not webhook_url or not provider:
        logger.warning("No webhook or provider provided.")
        return False
        
    ext = CHAT_EXTENSIONS.get(provider)
    if not ext:
        logger.error(f"Chat provider '{provider}' not found.")
        return False
        
    # Standard 1900 truncation to be safe for most platforms
    if len(message) > 1900:
        message = message[:1900] + "\n...[Truncated]"
        
    return ext.send_message(webhook_url, message)
