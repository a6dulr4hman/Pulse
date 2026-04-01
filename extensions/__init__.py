import os
import importlib
import inspect
from pathlib import Path
from extensions.base import BaseChatExtension, BaseVCSExtension, BasePMExtension

CHAT_EXTENSIONS = {}
VCS_EXTENSIONS = {}
PM_EXTENSIONS = {}

def load_extensions():
    base_dir = Path(__file__).parent
    
    # Load Chat
    chat_dir = base_dir / "chat"
    if chat_dir.exists():
        for file in chat_dir.glob("*.py"):
            if file.stem != "__init__":
                module = importlib.import_module(f"extensions.chat.{file.stem}")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseChatExtension) and obj != BaseChatExtension:
                        instance = obj()
                        CHAT_EXTENSIONS[instance.name] = instance

    # Load VCS
    vcs_dir = base_dir / "vcs"
    if vcs_dir.exists():
        for file in vcs_dir.glob("*.py"):
            if file.stem != "__init__":
                module = importlib.import_module(f"extensions.vcs.{file.stem}")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseVCSExtension) and obj != BaseVCSExtension:
                        instance = obj()
                        VCS_EXTENSIONS[instance.name] = instance

    # Load PM
    pm_dir = base_dir / "pm"
    if pm_dir.exists():
        for file in pm_dir.glob("*.py"):
            if file.stem != "__init__":
                module = importlib.import_module(f"extensions.pm.{file.stem}")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BasePMExtension) and obj != BasePMExtension:
                        instance = obj()
                        PM_EXTENSIONS[instance.name] = instance

load_extensions()
