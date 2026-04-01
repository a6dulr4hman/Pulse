import requests
from extensions.base import BaseChatExtension

class SlackExtension(BaseChatExtension):
    @property
    def name(self) -> str:
        return "slack"

    def verify_webhook(self, url: str) -> bool:
        try:
            r = requests.post(url, json={"text": "Setup Verification"}, timeout=5)
            return r.status_code == 200
        except:
            return False

    def send_message(self, url: str, message: str) -> bool:
        try:
            r = requests.post(url, json={"text": message}, timeout=5)
            return r.status_code == 200
        except:
            return False
