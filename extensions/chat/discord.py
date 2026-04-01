import requests
from extensions.base import BaseChatExtension

class DiscordExtension(BaseChatExtension):
    @property
    def name(self) -> str:
        return "discord"

    def verify_webhook(self, url: str) -> bool:
        try:
            r = requests.post(url, json={"content": "Setup Verification"}, timeout=5)
            return r.status_code in [200, 204]
        except:
            return False

    def send_message(self, url: str, message: str) -> bool:
        try:
            r = requests.post(url, json={"content": message}, timeout=5)
            return r.status_code in [200, 204]
        except:
            return False
