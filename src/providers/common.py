"""Provider-agnostic listing schema + cross-provider search criteria.

Each provider's ``fetch_full_detail`` already returns a dict with this exact
shape; ``Listing.from_dict`` just freezes it into a typed dataclass and
exposes a few derived properties used by the ranker.

Convention for ``Optional`` fields: ``None`` means "not provided by this
provider". Don't substitute zero or empty string — the ranker treats them
differently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Iterable, Optional

from dateutil import parser as dt_parser

# Provider-specific attribute names → canonical vocabulary.
# Sources of truth:
#   - flatfox: harvested from the SPA bundle (`main.*.js`); 21 names.
#   - homegate: harvested from a 600-listing sample of search results;
#     55 boolean characteristics.
# Anything not mapped here passes through verbatim (still searchable
# under its provider-native name).
FEATURE_MAP: dict[str, str] = {
    # ---- flatfox attributes (from SPA bundle) -------------------------
    "view": "view",
    "parkingspace": "parking",
    "garage": "garage",
    "lift": "elevator",
    "balconygarden": "balcony",
    "fireplace": "fireplace",
    "cable": "cable_tv",
    "broadbandinternet": "broadband",
    "parquetflooring": "parquet",
    "stonefloor": "stone_floor",
    "accessiblewithwheelchair": "wheelchair_accessible",
    "petsallowed": "pets_allowed",
    "partofcooperative": "part_of_cooperative",
    "dishwasher": "dishwasher",
    "tumbler": "tumble_dryer",
    "washingmachine": "washing_machine",
    "minergie": "minergie",
    "ramp": "ramp",
    "liftingplatform": "lifting_platform",
    "raisedgroundfloor": "raised_ground_floor",
    "underbuildinglaws": "under_building_laws",
    # ---- homegate boolean characteristics -----------------------------
    "hasBalcony": "balcony",
    "hasElevator": "elevator",
    "hasParking": "parking",
    "hasGarage": "garage",
    "hasCarPort": "car_port",
    "hasDoubleCarPort": "car_port",
    "hasGarden": "garden",
    "hasGardenShed": "garden_shed",
    "hasFireplace": "fireplace",
    "hasCellar": "cellar",
    "hasAttic": "attic",
    "hasStoreRoom": "store_room",
    "hasSwimmingPool": "swimming_pool",
    "hasPlayground": "playground",
    "hasNiceView": "view",
    "hasMountainView": "mountain_view",
    "hasLakeView": "lake_view",
    "hasCableTv": "cable_tv",
    "hasDishwasher": "dishwasher",
    "hasWashingMachine": "washing_machine",
    "hasTumbleDryer": "tumble_dryer",
    "hasSteamer": "steamer",
    "hasPhotovoltaic": "photovoltaic",
    "hasIsdn": "isdn",
    "hasLiftingPlatform": "lifting_platform",
    "hasRamp": "ramp",
    "hasGasSupply": "gas_supply",
    "hasWaterSupply": "water_supply",
    "hasPowerSupply": "power_supply",
    "hasSewageSupply": "sewage_supply",
    "hasConnectedBuildingLand": "connected_building_land",
    "hasBuildingLawRestrictions": "under_building_laws",
    "hasFlatSharingCommunity": "flat_sharing_community",
    "hasRemoteViewings": "remote_viewings",
    "isWheelchairAccessible": "wheelchair_accessible",
    "isChildFriendly": "child_friendly",
    "isCatsAllowed": "cats_allowed",
    "isDogsAllowed": "dogs_allowed",
    "arePetsAllowed": "pets_allowed",
    "isSmokingAllowed": "smoking_allowed",
    "isSecondaryResidenceAllowed": "secondary_residence_allowed",
    "isQuiet": "quiet",
    "isSunny": "sunny",
    "isWellTended": "well_tended",
    "isModernized": "modernized",
    "isRefurbished": "refurbished",
    "isPartiallyRefurbished": "partially_refurbished",
    "isInNeedOfRenovation": "needs_renovation",
    "isMinergieGeneral": "minergie",
    "isMinergieCertified": "minergie_certified",
    "isNewBuilding": "new_building",
    "isOldBuilding": "old_building",
    "isCornerHouse": "corner_house",
    "isMiddleHouse": "middle_house",
    "isGroundFloor": "ground_floor",
    "isGroundFloorRaised": "raised_ground_floor",
    "onEvenGround": "even_ground",
    "isProjection": "projection",
}


def _canonical(attr: str) -> str:
    return FEATURE_MAP.get(attr, attr)


_DATE_VALUE_RE = (
    r"(\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|"
    r"\d{1,2}\s+[A-Za-zÄÖÜäöüéèêàâîôû]+\s+\d{2,4})"
)
_AVAILABLE_CONTEXT_RE = re.compile(
    r"(?:available|availability|move[- ]?in|entry|enter\s+date|start\s+date|"
    r"bezug|einzug|verf[üu]gbar|frei\s+ab|disponible|"
    r"date\s+d['’]entr[ée]e)\D{0,35}" + _DATE_VALUE_RE,
    re.I,
)


def normalize_date(value: Any) -> Optional[str]:
    """Return ``YYYY-MM-DD`` when ``value`` clearly parses as a date."""
    if not value:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    iso = re.search(r"\d{4}-\d{1,2}-\d{1,2}", text)
    if iso:
        try:
            return dt_parser.parse(iso.group(0), dayfirst=False).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        return dt_parser.parse(text, dayfirst=True, fuzzy=True).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def infer_available_from_text(text: str) -> Optional[str]:
    """Extract a high-confidence move-in date from free text.

    This intentionally requires an availability/move-in cue near the date so
    unrelated viewing, publication, renovation, or appointment dates do not
    become false exclusions.
    """
    if not text:
        return None
    for match in _AVAILABLE_CONTEXT_RE.finditer(text):
        parsed = normalize_date(match.group(1))
        if parsed:
            return parsed
    return None


@dataclass
class Address:
    street: Optional[str] = None
    zipcode: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    public: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass
class Agency:
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    logo_url: Optional[str] = None


@dataclass
class Listing:
    provider: str
    listing_id: str
    url: str
    title: str
    description: str
    price_chf: Optional[float]
    rent_net_chf: Optional[float]
    rent_charges_chf: Optional[float]
    currency: str
    rooms: Optional[float]
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    surface_living_m2: Optional[float] = None
    surface_usable_m2: Optional[float] = None
    surface_property_m2: Optional[float] = None
    floor: Optional[int] = None
    year_built: Optional[int] = None
    year_renovated: Optional[int] = None
    available_from: Optional[str] = None  # ISO date string
    is_furnished: Optional[bool] = None
    is_temporary: Optional[bool] = None
    has_washing_machine: Optional[bool] = None
    object_category: Optional[str] = None
    object_type: Optional[str] = None
    offer_type: Optional[str] = None
    address: Address = field(default_factory=Address)
    images: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    agency: Agency = field(default_factory=Agency)
    # Augmented by enrichers (see ``providers.enrich``):
    municipality_tax_rate: Optional[float] = None
    commute: Optional[dict[str, Any]] = None  # {depart, walk_min, walk_km, alternatives:[{modes,travel_min}]}
    raw: dict[str, Any] = field(default_factory=dict)

    # -- derived ----------------------------------------------------------

    @property
    def price_per_m2(self) -> Optional[float]:
        if self.price_chf and self.surface_living_m2:
            return round(self.price_chf / self.surface_living_m2, 2)
        return None

    @property
    def has_coords(self) -> bool:
        return self.address.lat is not None and self.address.lon is not None

    @property
    def canonical_attributes(self) -> list[str]:
        """Attributes mapped to the cross-provider vocabulary (see FEATURE_MAP).

        Provider-only attributes pass through unchanged so they remain
        searchable when the caller knows the provider name.
        """
        return [_canonical(a) for a in self.attributes]

    @property
    def haystack(self) -> str:
        """Lowercased blob used by ``matches_keywords``.

        Includes: title, description, canonical_attributes, address.public,
        agency.name, agency.email, the slug-y part of the URL. Built fresh
        per call site; cheap.
        """
        parts: list[str] = [
            self.title or "",
            self.description or "",
            " ".join(self.canonical_attributes),
            self.address.public or "",
            self.agency.name or "",
            self.agency.email or "",
            self.url or "",
        ]
        return " ".join(parts).lower()

    def matches_keywords(
        self,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
    ) -> bool:
        """Substring (case-insensitive) match across title+description+attrs+address.

        Returns True when EVERY ``include`` term is present AND no
        ``exclude`` term is present. Empty/None lists are no-ops.
        """
        h = self.haystack
        for kw in include or ():
            if kw.lower() not in h:
                return False
        for kw in exclude or ():
            if kw.lower() in h:
                return False
        return True

    def matches_features(
        self,
        must: Iterable[str] | None = None,
        must_not: Iterable[str] | None = None,
    ) -> bool:
        """Canonical-vocabulary feature filter.

        ``must`` and ``must_not`` are lists of canonical names (e.g.
        ``["balcony", "elevator"]``). See ``FEATURE_MAP``.
        """
        canon = set(self.canonical_attributes)
        for f in must or ():
            if f not in canon:
                return False
        for f in must_not or ():
            if f in canon:
                return False
        return True

    # -- (de)serialization -----------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Listing":
        addr = d.get("address") or {}
        ag = d.get("agency") or {}
        description = d.get("description") or ""
        available_from = normalize_date(d.get("available_from")) or infer_available_from_text(description)
        return cls(
            provider=d["provider"],
            listing_id=d["listing_id"],
            url=d["url"],
            title=d.get("title") or "",
            description=description,
            price_chf=d.get("price_chf"),
            rent_net_chf=d.get("rent_net_chf"),
            rent_charges_chf=d.get("rent_charges_chf"),
            currency=d.get("currency") or "CHF",
            rooms=d.get("rooms"),
            bedrooms=d.get("bedrooms"),
            bathrooms=d.get("bathrooms"),
            surface_living_m2=d.get("surface_living_m2"),
            surface_usable_m2=d.get("surface_usable_m2"),
            surface_property_m2=d.get("surface_property_m2"),
            floor=d.get("floor"),
            year_built=d.get("year_built"),
            year_renovated=d.get("year_renovated"),
            available_from=available_from,
            is_furnished=d.get("is_furnished"),
            is_temporary=d.get("is_temporary"),
            has_washing_machine=d.get("has_washing_machine"),
            object_category=d.get("object_category"),
            object_type=d.get("object_type"),
            offer_type=d.get("offer_type"),
            address=Address(
                street=addr.get("street"),
                zipcode=str(addr["zipcode"]) if addr.get("zipcode") is not None else None,
                city=addr.get("city"),
                country=addr.get("country"),
                public=addr.get("public"),
                lat=addr.get("lat"),
                lon=addr.get("lon"),
            ),
            images=list(d.get("images") or []),
            attributes=list(d.get("attributes") or []),
            agency=Agency(
                name=ag.get("name"),
                phone=ag.get("phone"),
                email=ag.get("email"),
                logo_url=ag.get("logo_url"),
            ),
            raw=d.get("raw") or {},
        )

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@dataclass
class SearchCriteria:
    """Provider-agnostic search input.

    Provider clients translate the *structured* fields into their native
    query (price, rooms, bbox, geoTags, offerType). The *common-filter*
    fields — keywords, must/must_not features, plus furnished/temporary
    when the provider lacks native support — are applied client-side after
    fetching, on the normalized ``Listing`` shape.

    This means ``limit`` is the count AFTER common filtering. If you ask
    for 10 results with strict keyword filtering, the client will page
    through more than 10 raw rows under the hood (up to ``page_cap``).
    """

    # Native filters (mapped to provider query)
    offer_type: str = "RENT"  # RENT | BUY
    categories: list[str] = field(default_factory=lambda: ["APARTMENT", "HOUSE"])
    min_rooms: Optional[float] = None
    max_rooms: Optional[float] = None
    min_price_chf: Optional[int] = None
    max_price_chf: Optional[int] = None
    min_surface_m2: Optional[int] = None
    max_surface_m2: Optional[int] = None
    cities: list[str] = field(default_factory=list)         # human names
    zipcodes: list[str] = field(default_factory=list)       # postal codes
    bbox: Optional[tuple[float, float, float, float]] = None  # (south, west, north, east)

    # Common filters (post-fetch)
    keywords: list[str] = field(default_factory=list)            # AND, all must appear
    exclude_keywords: list[str] = field(default_factory=list)    # OR, any disqualifies
    must_features: list[str] = field(default_factory=list)       # canonical names
    must_not_features: list[str] = field(default_factory=list)
    must_be_furnished: Optional[bool] = None    # None = don't care
    must_be_temporary: Optional[bool] = None
    must_be_swap: Optional[bool] = False
    # Applied after commute enrichment (no-op when ``commute=False``).
    # Listings whose best transit alternative exceeds this are dropped;
    # listings with no commute info pass through (false-negative-safe).
    max_commute_min: Optional[int] = None
    # Drop only when a structured or high-confidence inferred date is known
    # and later than this cutoff. Unknown dates pass through.
    available_on_or_before: Optional[str] = None

    # Paging
    limit: Optional[int] = 20
    page_cap: int = 200  # max raw rows we'll fetch before giving up
    sort_by_newest: bool = False  # True = newest first (good for polling)


def best_commute_min(listing: "Listing") -> Optional[int]:
    """Best (lowest) commute alternative in minutes, or None when missing."""
    c = listing.commute or {}
    alts = c.get("alternatives") or []
    if not alts:
        return None
    travels = [a.get("travel_min") for a in alts if a.get("travel_min") is not None]
    return min(travels) if travels else None


def filter_by_commute(
    listings: Iterable["Listing"], max_min: Optional[int],
) -> list["Listing"]:
    """Drop listings whose best commute exceeds ``max_min`` minutes.

    ``None`` for the listing's commute (no coords, commute disabled, no
    transit found) is kept so we don't false-negative when the API
    couldn't tell us. Set the kwarg to ``None`` to skip the filter.
    """
    listings = list(listings)
    if max_min is None:
        return listings
    out = []
    for l in listings:
        m = best_commute_min(l)
        if m is None or m <= max_min:
            out.append(l)
    return out


def is_available_after_cutoff(listing: "Listing", cutoff: Optional[str]) -> bool:
    """True only when both dates parse and listing availability is later."""
    target = normalize_date(cutoff)
    available = normalize_date(listing.available_from)
    return bool(target and available and available > target)


def filter_by_availability(
    listings: Iterable["Listing"],
    cutoff: Optional[str],
) -> list["Listing"]:
    """Keep unknown dates; drop known dates after ``cutoff``."""
    if not cutoff:
        return list(listings)
    return [l for l in listings if not is_available_after_cutoff(l, cutoff)]


def apply_common_filters(
    listings: Iterable[Listing], criteria: SearchCriteria
) -> list[Listing]:
    """Run the post-fetch filters in the agreed order.

    Order: keywords → features → boolean flags (furnished, temporary) →
    surface bounds (homegate has no native min_surface). Returns up to
    ``criteria.limit`` matches.
    """
    out: list[Listing] = []
    for l in listings:
        if criteria.keywords or criteria.exclude_keywords:
            if not l.matches_keywords(criteria.keywords, criteria.exclude_keywords):
                continue
        if criteria.must_features or criteria.must_not_features:
            if not l.matches_features(criteria.must_features, criteria.must_not_features):
                continue
        # Tri-state: True = must, False = must-not, None = don't care.
        # When the listing's value is None we DON'T drop — provider may not
        # populate the field in search rows (homegate omits ``isFurnished``
        # from the search fieldset). Push the strict filter into native
        # provider query when possible to avoid this fallthrough.
        if criteria.must_be_furnished is True and l.is_furnished is False:
            continue
        if criteria.must_be_furnished is False and l.is_furnished is True:
            continue
        if criteria.must_be_temporary is True and l.is_temporary is False:
            continue
        if criteria.must_be_temporary is False and l.is_temporary is True:
            continue
        if criteria.min_surface_m2 and (l.surface_living_m2 or 0) < criteria.min_surface_m2:
            continue
        if criteria.max_surface_m2 and l.surface_living_m2 and l.surface_living_m2 > criteria.max_surface_m2:
            continue
        if is_available_after_cutoff(l, criteria.available_on_or_before):
            continue
        out.append(l)
        if criteria.limit and len(out) >= criteria.limit:
            break
    return out
