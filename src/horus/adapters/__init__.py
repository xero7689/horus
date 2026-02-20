from horus.adapters.base import SiteAdapter
from horus.adapters.threads import ThreadsAdapter
from horus.adapters.web import GenericWebAdapter

_REGISTRY: dict[str, type[SiteAdapter]] = {}


def register(adapter_cls: type[SiteAdapter]) -> type[SiteAdapter]:
    """Register a site adapter. Can be used as a decorator."""
    _REGISTRY[adapter_cls.site_id] = adapter_cls
    return adapter_cls


def get_adapter(site_id: str) -> type[SiteAdapter]:
    if site_id not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"Unknown site: '{site_id}'. Available: {available}")
    return _REGISTRY[site_id]


def list_adapters() -> list[type[SiteAdapter]]:
    return list(_REGISTRY.values())


# Register built-in adapters
register(ThreadsAdapter)
register(GenericWebAdapter)

__all__ = ["SiteAdapter", "register", "get_adapter", "list_adapters"]
