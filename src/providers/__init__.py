"""Provider API clients for apartment listing sites.

Each module exposes a Client class with:
    search(criteria: dict, limit: int | None = None) -> list[Listing]
    fetch_detail(url_or_id: str) -> Listing | None

The Listing dataclass is the one defined in src.apartment_finder_llm.
"""

from .common import Address, Agency, Listing, SearchCriteria
from .comparis_api import ComparisClient
from .flatfox_api import FlatfoxClient
from .homegate_api import HomegateClient
from .multi import fetch_listing, search_all, search_all_iter

__all__ = [
    "Address",
    "Agency",
    "ComparisClient",
    "FlatfoxClient",
    "HomegateClient",
    "Listing",
    "SearchCriteria",
    "fetch_listing",
    "search_all",
    "search_all_iter",
]
