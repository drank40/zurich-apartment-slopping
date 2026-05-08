"""Provider API clients for apartment listing sites.

Each module exposes a Client class with:
    search(criteria: dict, limit: int | None = None) -> list[Listing]
    fetch_detail(url_or_id: str) -> Listing | None

The Listing dataclass is the one defined in src.apartment_finder_llm.
"""

from .flatfox_api import FlatfoxClient
from .homegate_api import HomegateClient
from .comparis_api import ComparisClient

__all__ = ["FlatfoxClient", "HomegateClient", "ComparisClient"]
