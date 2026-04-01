import requests
from extensions.base import BaseChatExtension

class TeamsExtension(BaseChatExtension):
    @property
    def name(self) -> str:
        return "teams"

    def verify_webhook(self, url: str) -> bool:
        try:
            r = requests.post(url, json={"text": "Setup Verification"}, timeout=5)
            # Teams returns 200, 202, etc.
            return r.status_code in [200, 202]
        except:
            return False

    def send_message(self, url: str, message: str) -> bool:
        try:
            r = requests.post(url, json={"text": message}, timeout=5)
            return r.status_code in [200, 202]
        except:
            return False
