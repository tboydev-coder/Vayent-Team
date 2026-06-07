"""API routers package."""
from importlib import import_module

__all__ = ["auth", "connections", "chat", "copilot", "health"]
__all__.append("voice")


def __getattr__(name: str):
    if name in __all__:
        module = import_module(f"app.routers.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
