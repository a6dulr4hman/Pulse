class BaseChatExtension:
    @property
    def name(self) -> str:
        return "base"
    
    def verify_webhook(self, url: str) -> bool:
        raise NotImplementedError

    def send_message(self, url: str, message: str) -> bool:
        raise NotImplementedError

class BaseVCSExtension:
    @property
    def name(self) -> str:
        return "base"

    def verify_signature(self, request, secret: str) -> bool:
        raise NotImplementedError

    def parse_commit_payload(self, raw_data: dict) -> list:
        raise NotImplementedError

class BasePMExtension:
    @property
    def name(self) -> str:
        return "base"

    def verify_signature(self, request, secret: str) -> bool:
        raise NotImplementedError

    def parse_task_payload(self, raw_data: dict) -> dict:
        raise NotImplementedError
