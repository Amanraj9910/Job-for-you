"""Geography driven by the user's profile: home country, preferred cities, acceptable remote scopes.

Everything location-aware (location tiers, ATS location filters, remote-scope checks) asks this module
instead of hard-coding a country. The profile (``preferences.yaml`` → ``location``) decides:

* ``home_country``      ISO-3166 alpha-2 code, e.g. ``IN``, ``US``, ``DE``, ``GB``
* ``tier1`` / ``tier2`` first- and second-choice cities (any city in the home country is tier 3, abroad is 4)
* ``extra_cities``      home-country cities the built-in list below does not know
* ``remote_ok_scopes``  remote-job scopes that include you, e.g. ``[IN, APAC, ASIA, WORLDWIDE]``

The built-in gazetteer is intentionally small (major tech-hiring cities). Add your own cities in the profile;
add more countries/cities here in a pull request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

# --------------------------------------------------------------------------------------------- gazetteer
# cc: (scope token, country-name regex (case-insensitive), regions, major cities)
# The scope token is what remote_scope() returns for that country; "UK" is used for GB for readability.
COUNTRIES: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "IN": ("IN", r"india|indian|bharat", ("APAC", "ASIA"), ()),   # cities: INDIA_CITIES below
    "US": ("US", r"united states|usa|u\.s\.a?", ("AMERICAS", "NA"), (
        "new york", "nyc", "san francisco", "bay area", "seattle", "austin", "boston", "chicago", "denver",
        "los angeles", "san jose", "palo alto", "mountain view", "sunnyvale", "menlo park", "atlanta", "dallas",
        "houston", "miami", "portland", "philadelphia", "phoenix", "san diego", "raleigh", "pittsburgh",
        "minneapolis", "salt lake city", "detroit", "washington dc", "washington, d.c.")),
    "GB": ("UK", r"united kingdom|uk|u\.k\.|england|scotland|wales|britain", ("EMEA", "EUROPE"), (
        "london", "manchester", "edinburgh", "oxford", "bristol", "birmingham", "leeds", "glasgow", "belfast")),
    "CA": ("CA", r"canada", ("AMERICAS", "NA"), (
        "toronto", "vancouver", "montreal", "ottawa", "calgary", "waterloo", "edmonton")),
    "DE": ("DE", r"germany|deutschland", ("EMEA", "EU", "EUROPE"), (
        "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne", "köln", "stuttgart", "düsseldorf",
        "leipzig")),
    "FR": ("FR", r"france", ("EMEA", "EU", "EUROPE"), ("paris", "lyon", "toulouse", "marseille", "nantes", "lille")),
    "NL": ("NL", r"netherlands|holland", ("EMEA", "EU", "EUROPE"), (
        "amsterdam", "rotterdam", "utrecht", "eindhoven", "the hague")),
    "IE": ("IE", r"ireland", ("EMEA", "EU", "EUROPE"), ("dublin", "cork", "galway")),
    "ES": ("ES", r"spain|españa", ("EMEA", "EU", "EUROPE"), ("madrid", "barcelona", "valencia", "seville")),
    "PT": ("PT", r"portugal", ("EMEA", "EU", "EUROPE"), ("lisbon", "porto")),
    "PL": ("PL", r"poland", ("EMEA", "EU", "EUROPE"), ("warsaw", "krakow", "kraków", "wroclaw", "wrocław", "gdansk")),
    "IT": ("IT", r"italy", ("EMEA", "EU", "EUROPE"), ("milan", "rome", "turin")),
    "SE": ("SE", r"sweden", ("EMEA", "EU", "EUROPE"), ("stockholm", "gothenburg", "malmö")),
    "CH": ("CH", r"switzerland", ("EMEA", "EUROPE"), ("zurich", "zürich", "geneva", "basel", "lausanne")),
    "AT": ("AT", r"austria", ("EMEA", "EU", "EUROPE"), ("vienna",)),
    "BE": ("BE", r"belgium", ("EMEA", "EU", "EUROPE"), ("brussels", "antwerp")),
    "DK": ("DK", r"denmark", ("EMEA", "EU", "EUROPE"), ("copenhagen",)),
    "NO": ("NO", r"norway", ("EMEA", "EUROPE"), ("oslo",)),
    "FI": ("FI", r"finland", ("EMEA", "EU", "EUROPE"), ("helsinki",)),
    "CZ": ("CZ", r"czechia|czech republic", ("EMEA", "EU", "EUROPE"), ("prague", "brno")),
    "RO": ("RO", r"romania", ("EMEA", "EU", "EUROPE"), ("bucharest", "cluj")),
    "UA": ("UA", r"ukraine", ("EMEA", "EUROPE"), ("kyiv", "kiev", "lviv")),
    "GR": ("GR", r"greece", ("EMEA", "EU", "EUROPE"), ("athens",)),
    "HU": ("HU", r"hungary", ("EMEA", "EU", "EUROPE"), ("budapest",)),
    "AU": ("AU", r"australia", ("APAC",), ("sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra")),
    "NZ": ("NZ", r"new zealand", ("APAC",), ("auckland", "wellington")),
    "SG": ("SG", r"singapore", ("APAC", "ASIA"), ()),
    "AE": ("AE", r"uae|united arab emirates", ("EMEA", "ME"), ("dubai", "abu dhabi")),
    "SA": ("SA", r"saudi arabia|ksa", ("EMEA", "ME"), ("riyadh", "jeddah")),
    "QA": ("QA", r"qatar", ("EMEA", "ME"), ("doha",)),
    "IL": ("IL", r"israel", ("EMEA", "ME"), ("tel aviv", "jerusalem", "haifa")),
    "TR": ("TR", r"turkey|türkiye", ("EMEA", "EUROPE"), ("istanbul", "ankara")),
    "JP": ("JP", r"japan", ("APAC", "ASIA"), ("tokyo", "osaka")),
    "KR": ("KR", r"south korea|korea", ("APAC", "ASIA"), ("seoul",)),
    "CN": ("CN", r"china", ("APAC", "ASIA"), ("beijing", "shanghai", "shenzhen")),
    "HK": ("HK", r"hong kong", ("APAC", "ASIA"), ()),
    "TW": ("TW", r"taiwan", ("APAC", "ASIA"), ("taipei",)),
    "PH": ("PH", r"philippines", ("APAC", "ASIA"), ("manila", "makati", "cebu", "taguig")),
    "VN": ("VN", r"vietnam|viet nam", ("APAC", "ASIA"), ("ho chi minh city", "hanoi")),
    "ID": ("ID", r"indonesia", ("APAC", "ASIA"), ("jakarta",)),
    "MY": ("MY", r"malaysia", ("APAC", "ASIA"), ("kuala lumpur",)),
    "TH": ("TH", r"thailand", ("APAC", "ASIA"), ("bangkok",)),
    "PK": ("PK", r"pakistan", ("APAC", "ASIA"), ("karachi", "lahore", "islamabad")),
    "BD": ("BD", r"bangladesh", ("APAC", "ASIA"), ("dhaka",)),
    "LK": ("LK", r"sri lanka", ("APAC", "ASIA"), ("colombo",)),
    "NP": ("NP", r"nepal", ("APAC", "ASIA"), ("kathmandu",)),
    "BR": ("BR", r"brazil|brasil", ("AMERICAS", "LATAM"), ("são paulo", "sao paulo", "rio de janeiro", "belo horizonte")),
    "MX": ("MX", r"mexico|méxico", ("AMERICAS", "LATAM"), ("mexico city", "guadalajara", "monterrey")),
    "AR": ("AR", r"argentina", ("AMERICAS", "LATAM"), ("buenos aires",)),
    "CO": ("CO", r"colombia", ("AMERICAS", "LATAM"), ("bogota", "bogotá", "medellin", "medellín")),
    "CL": ("CL", r"chile", ("AMERICAS", "LATAM"), ("santiago",)),
    "NG": ("NG", r"nigeria", ("EMEA", "AFRICA"), ("lagos", "abuja")),
    "KE": ("KE", r"kenya", ("EMEA", "AFRICA"), ("nairobi",)),
    "ZA": ("ZA", r"south africa", ("EMEA", "AFRICA"), ("cape town", "johannesburg")),
    "EG": ("EG", r"egypt", ("EMEA", "AFRICA", "ME"), ("cairo",)),
}

# India keeps its detailed city table (aliases -> canonical), it was the first supported country.
INDIA_CITIES = {
    "indore": "Indore", "bengaluru": "Bengaluru", "bangalore": "Bengaluru", "bengaluru urban": "Bengaluru",
    "hyderabad": "Hyderabad", "secunderabad": "Hyderabad", "pune": "Pune", "mumbai": "Mumbai",
    "navi mumbai": "Mumbai", "thane": "Mumbai", "delhi": "Delhi", "new delhi": "Delhi", "delhi ncr": "Delhi",
    "ncr": "Delhi", "gurugram": "Gurugram", "gurgaon": "Gurugram", "noida": "Noida", "greater noida": "Noida",
    "chennai": "Chennai", "kolkata": "Kolkata", "ahmedabad": "Ahmedabad", "gandhinagar": "Ahmedabad",
    "jaipur": "Jaipur", "chandigarh": "Chandigarh", "mohali": "Chandigarh", "kochi": "Kochi", "cochin": "Kochi",
    "coimbatore": "Coimbatore", "thiruvananthapuram": "Thiruvananthapuram", "trivandrum": "Thiruvananthapuram",
    "bhopal": "Bhopal", "nagpur": "Nagpur", "mysore": "Mysuru", "mysuru": "Mysuru", "vadodara": "Vadodara",
    "surat": "Surat", "lucknow": "Lucknow", "bhubaneswar": "Bhubaneswar", "visakhapatnam": "Visakhapatnam",
    "jabalpur": "Jabalpur", "mangalore": "Mangaluru", "mangaluru": "Mangaluru", "vijayawada": "Vijayawada",
}
US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
             "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()

# Region scope tokens and the words that express them in job text.
REGION_SCOPES: list[tuple[str, str]] = [
    ("WORLDWIDE", r"worldwide|anywhere|global(?:ly)?|any location|all countries"),
    ("APAC", r"apac|asia[- ]pacific|apj"),
    ("ASIA", r"asia(?![- ]pacific)"),
    ("EMEA", r"emea"),
    ("EU", r"eu|europe|european|cet|cest"),
    ("LATAM", r"latam|latin america|south america"),
    ("ME", r"middle east|gcc"),
]
# Country scopes that keep their historical, slightly wider patterns (time zones, region words).
_SCOPE_OVERRIDES = {
    "US": r"us|u\.s\.|usa|united states|americas only|north america|est|pst|cst",
    "CA": r"canada",
    "UK": r"uk|united kingdom|england|britain",
    "AU": r"australia|anz|new zealand",
    "ME": r"uae|dubai|saudi|middle east|gcc|qatar",
    "SG": r"singapore",
    "IN": r"india|indian",
}
DEFAULT_OK_SCOPES = {"WORLDWIDE"}
# Region words that mark a location as foreign but are not remote-scope tokens of their own.
EXTRA_REGION_WORDS = {"AMERICAS": r"americas?|amer|north america", "EUROPE": r"emea|europe"}

# Indeed/Glassdoor (python-jobspy) country names, where they differ from a simple lower-case name.
JOBSPY_COUNTRY = {"IN": "India", "US": "USA", "GB": "UK", "AE": "United Arab Emirates", "KR": "South Korea",
                  "CZ": "Czech Republic", "NZ": "New Zealand", "ZA": "South Africa", "SA": "Saudi Arabia",
                  "HK": "Hong Kong"}
COUNTRY_NAMES = {"IN": "India", "US": "United States", "GB": "United Kingdom", "CA": "Canada", "DE": "Germany",
                 "FR": "France", "NL": "Netherlands", "IE": "Ireland", "ES": "Spain", "PT": "Portugal",
                 "PL": "Poland", "IT": "Italy", "SE": "Sweden", "CH": "Switzerland", "AT": "Austria",
                 "BE": "Belgium", "DK": "Denmark", "NO": "Norway", "FI": "Finland", "CZ": "Czechia",
                 "RO": "Romania", "UA": "Ukraine", "GR": "Greece", "HU": "Hungary", "AU": "Australia",
                 "NZ": "New Zealand", "SG": "Singapore", "AE": "United Arab Emirates", "SA": "Saudi Arabia",
                 "QA": "Qatar", "IL": "Israel", "TR": "Turkey", "JP": "Japan", "KR": "South Korea", "CN": "China",
                 "HK": "Hong Kong", "TW": "Taiwan", "PH": "Philippines", "VN": "Vietnam", "ID": "Indonesia",
                 "MY": "Malaysia", "TH": "Thailand", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka",
                 "NP": "Nepal", "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "CO": "Colombia", "CL": "Chile",
                 "NG": "Nigeria", "KE": "Kenya", "ZA": "South Africa", "EG": "Egypt"}


def _title(city: str) -> str:
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in city.split())


def _canon(cc: str) -> dict[str, str]:
    """lower-case alias -> canonical city name for one country."""
    if cc == "IN":
        return dict(INDIA_CITIES)
    return {c: _title(c) for c in COUNTRIES.get(cc, ("", "", (), ()))[3]}


def scope_token(cc: str) -> str:
    return COUNTRIES.get(cc, (cc,))[0]


def default_ok_scopes(cc: str) -> list[str]:
    """A sensible default for remote_ok_scopes: your country, its regions, and worldwide."""
    regs = [r for r in COUNTRIES.get(cc, ("", "", (), ()))[2] if r not in ("NA", "AFRICA", "EUROPE", "AMERICAS")]
    return [scope_token(cc), *regs, "WORLDWIDE"]


# --------------------------------------------------------------------------------------------- profile view
@dataclass(frozen=True)
class Geo:
    home_cc: str = "IN"
    tier1: tuple[str, ...] = ()
    tier2: tuple[str, ...] = ()
    extra_cities: tuple[str, ...] = ()
    ok_scopes: frozenset[str] = field(default_factory=lambda: frozenset({"IN", "APAC", "ASIA", "WORLDWIDE"}))

    @property
    def home_token(self) -> str:
        return scope_token(self.home_cc)

    @property
    def home_name(self) -> str:
        return COUNTRY_NAMES.get(self.home_cc, self.home_cc)

    # ---- compiled lookups (cached per Geo instance)
    def _c(self, key: str, build):
        cache = self.__dict__.setdefault("_cache", {})
        if key not in cache:
            cache[key] = build()
        return cache[key]

    def cities(self) -> dict[str, str]:
        def build():
            table = _canon(self.home_cc)
            for c in (*self.tier1, *self.tier2, *self.extra_cities):
                c = (c or "").strip()
                if c and c.lower() not in table:
                    table[c.lower()] = c
            return table
        return self._c("cities", build)

    def city_rx(self) -> re.Pattern | None:
        def build():
            keys = sorted(self.cities(), key=len, reverse=True)     # longest first ("navi mumbai" before "mumbai")
            return re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])", re.I) if keys else None
        return self._c("city_rx", build)

    def country_rx(self) -> re.Pattern:
        def build():
            names = COUNTRIES.get(self.home_cc, ("", re.escape(self.home_name.lower()), (), ()))[1]
            code = re.escape(self.home_cc)
            alt = "|IND" if self.home_cc == "IN" else "|USA" if self.home_cc == "US" else ""
            # names case-insensitive; the bare ISO code only in capitals (so the English word "in" is not India)
            return re.compile(rf"(?i:\b(?:{names})\b)|\b(?:{code}{alt})\b")
        return self._c("country_rx", build)

    def tier_of_city(self, city: str | None) -> int | None:
        if not city:
            return None
        canon = self.cities()
        t1 = {canon.get(c.lower(), c) for c in self.tier1}
        t2 = {canon.get(c.lower(), c) for c in self.tier2}
        return 1 if city in t1 else 2 if city in t2 else 3

    def foreign_rx(self) -> re.Pattern:
        """Names of other countries / their cities / regions that do not include you."""
        def build():
            parts = []
            for cc, (_tok, names, _regs, cities) in COUNTRIES.items():
                if cc == self.home_cc:
                    continue
                parts.append(names)
                parts.extend(re.escape(c) for c in cities)
                if cc != "IN":
                    continue
                parts.extend(re.escape(c) for c in INDIA_CITIES)
            for tok, words in REGION_SCOPES:
                if tok not in self.ok_scopes and tok != "WORLDWIDE":
                    parts.append(words)
            home_regions = COUNTRIES.get(self.home_cc, ("", "", (), ()))[2]
            for region, words in EXTRA_REGION_WORDS.items():
                if region not in home_regions and region not in self.ok_scopes:
                    parts.append(words)
            # drop anything that collides with a home city name (e.g. "cambridge" lists)
            home = set(self.cities())
            parts = [p for p in parts if p not in home]
            return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I)
        return self._c("foreign_rx", build)

    def state_code_rx(self) -> re.Pattern | None:
        """", CA" / "- NY" style US state codes (capitals only). Foreign for non-US users."""
        def build():
            codes = [c for c in US_STATES if c != self.home_cc]
            return re.compile(r"(?:,|-)\s*(?:" + "|".join(codes) + r")\b")
        return self._c("state_rx", build)


_override: Geo | None = None


def configure(geo: Geo | None) -> None:
    """Use an explicit Geo (tests, or a one-off run). ``configure(None)`` goes back to the profile."""
    global _override
    _override = geo
    _from_profile.cache_clear()


@lru_cache(maxsize=1)
def _from_profile() -> Geo:
    try:
        from .profile import preferences
        loc = (preferences() or {}).get("location") or {}
    except Exception:  # noqa: BLE001 — no profile yet: sensible India-free default would hide everything
        loc = {}
    cc = str(loc.get("home_country") or "IN").strip().upper()
    if cc == "UK":
        cc = "GB"
    scopes = loc.get("remote_ok_scopes") or default_ok_scopes(cc)
    scopes = {"UK" if str(s).upper() == "GB" else str(s).upper() for s in scopes}
    return Geo(home_cc=cc, tier1=tuple(loc.get("tier1") or ()), tier2=tuple(loc.get("tier2") or ()),
               extra_cities=tuple(loc.get("extra_cities") or ()), ok_scopes=frozenset(scopes))


def current() -> Geo:
    return _override or _from_profile()


# --------------------------------------------------------------------------------------------- helpers
def find_home_city(text: str | None) -> str | None:
    """Canonical name of the first home-country city mentioned in ``text`` (longest alias wins)."""
    g = current()
    rx = g.city_rx()
    if not rx or not text:
        return None
    m = rx.search(text)
    return g.cities()[m.group(1).lower()] if m else None


def is_home_country_code(country: str | None) -> bool:
    if not country:
        return False
    c = country.strip()
    g = current()
    return c.upper() in (g.home_cc, g.home_token) or (g.home_cc == "IN" and c.lower() == "ind") or \
        c.lower() == g.home_name.lower() or bool(re.fullmatch(COUNTRIES.get(g.home_cc, ("", "$^"))[1], c, re.I))


def is_home(text: str | None, country: str | None = None) -> bool:
    """True when the location text / country names your home country or one of its cities."""
    if is_home_country_code(country):
        return True
    g = current()
    t = text or ""
    if find_home_city(t) or g.country_rx().search(t):
        return True
    return g.home_cc == "US" and bool(re.search(r"(?:,|-)\s*(?:" + "|".join(US_STATES) + r")\b", t))


def ok_scopes() -> frozenset[str]:
    return current().ok_scopes


def foreign_hint(text: str | None) -> bool:
    """Text names a place outside your home country (other country, foreign city, non-OK region)."""
    if not text:
        return False
    g = current()
    if g.foreign_rx().search(text):
        return True
    return g.home_cc != "US" and bool(g.state_code_rx().search(text))


_SCOPE_ORDER = ["WORLDWIDE", "IN", "APAC", "ASIA", "US", "CA", "UK", "EMEA", "EU", "LATAM", "AU", "ME", "SG"]


@lru_cache(maxsize=1)
def _scope_patterns() -> list[tuple[str, re.Pattern]]:
    words = {tok: w for tok, w in REGION_SCOPES} | _SCOPE_OVERRIDES
    out = [(tok, re.compile(rf"\b(?:{words[tok]})\b", re.I)) for tok in _SCOPE_ORDER]
    seen = set(_SCOPE_ORDER)
    for _cc, (tok, names, _r, _c) in COUNTRIES.items():
        if tok not in seen:
            out.append((tok, re.compile(rf"\b(?:{names})\b", re.I)))
            seen.add(tok)
    return out


def remote_scope(text: str | None) -> list[str]:
    """Scope tokens a remote job is open to, e.g. ['IN'], ['US'], ['EMEA'], ['WORLDWIDE']."""
    t = text or ""
    return [tok for tok, rx in _scope_patterns() if rx.search(t)]


def jobspy_country() -> str:
    g = current()
    return JOBSPY_COUNTRY.get(g.home_cc, g.home_name)
