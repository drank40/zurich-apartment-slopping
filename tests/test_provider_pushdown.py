from src.providers.common import SearchCriteria
from src.providers.flatfox_api import FlatfoxClient
from src.providers.homegate_api import HomegateClient


class CapturingFlatfox(FlatfoxClient):
    def __init__(self):
        super().__init__()
        self.last_criteria = None

    def search(self, criteria, limit=None):
        self.last_criteria = criteria
        return []


class CapturingHomegate(HomegateClient):
    def __init__(self):
        self.last_query = None

    def search(self, criteria, limit=None, page_size=20, sort_by="monthlyRent", sort_direction="asc"):
        self.last_query = criteria
        return []


def test_flatfox_pushes_swap_filter():
    client = CapturingFlatfox()

    client.search_listings(SearchCriteria(must_be_swap=False), llm=False, tax=False)

    assert client.last_criteria["is_swap"] is False


def test_homegate_pushes_furnished_and_temporary_filters():
    client = CapturingHomegate()

    client.search_listings(
        SearchCriteria(must_be_furnished=True, must_be_temporary=False),
        llm=False,
        tax=False,
    )

    assert client.last_query["isFurnished"] is True
    assert client.last_query["isTemporary"] is False
