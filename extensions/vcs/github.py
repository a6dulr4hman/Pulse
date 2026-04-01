import hmac
import hashlib
from extensions.base import BaseVCSExtension

class GitHubExtension(BaseVCSExtension):
    @property
    def name(self) -> str:
        return "github"

    def verify_signature(self, request, secret: str) -> bool:
        signature = request.headers.get("x-hub-signature-256")
        if not signature or not secret:
            return False
            
        try:
            body = request._body # Needs to be awaited or injected from FastAPI
            # we will handle hashing later in the route
            return True
        except:
            return False

    def parse_commit_payload(self, raw_data: dict) -> list:
        commits_info = []
        author = "unknown"
        if "pusher" in raw_data and "name" in raw_data["pusher"]:
            author = raw_data["pusher"]["name"]
        
        for commit in raw_data.get("commits", []):
            files_added = commit.get("added", [])
            files_modified = commit.get("modified", [])
            files_removed = commit.get("removed", [])
            all_files = files_added + files_modified + files_removed

            commits_info.append({
                "author": author,
                "message": commit.get("message", ""),
                "files": all_files,
                "url": commit.get("url", "")
            })
        return commits_info
