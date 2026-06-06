"""Data providers for the unified search_index tool.

Each provider exposes an async `search(query, **filters) -> list[Opportunity]`
and degrades gracefully: a provider that errors or times out returns an empty
list and logs, rather than failing the whole request. That mirrors the paper's
first deployment lesson (heterogeneous source quality -> graceful degradation).
"""

from .grants_gov import GrantsGovProvider
from .kindora import KindoraProvider
from .web_search import WebSearchProvider

__all__ = ["GrantsGovProvider", "KindoraProvider", "WebSearchProvider"]
