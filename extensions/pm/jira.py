from extensions.base import BasePMExtension

class JiraExtension(BasePMExtension):
    @property
    def name(self) -> str:
        return "jira"

    def verify_signature(self, request, secret: str) -> bool:
        # Jira webhooks can be verified via tokens or basic auth
        # Implementing a simple pass-through for now
        return True

    def parse_task_payload(self, raw_data: dict) -> dict:
        return {
            "task_id": raw_data.get("issue", {}).get("key", "UNKNOWN"),
            "status": raw_data.get("issue", {}).get("fields", {}).get("status", {}).get("name", "Unknown"),
            "assignee": raw_data.get("issue", {}).get("fields", {}).get("assignee", {}).get("displayName", "Unassigned"),
            "summary": raw_data.get("issue", {}).get("fields", {}).get("summary", "")
        }
