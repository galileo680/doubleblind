import re
from dataclasses import dataclass
from typing import Any

ADDRESS = re.compile(
    r"^(?P<street>.+?)\s+(?P<building>\d+[A-Za-z]?)"
    r"(?:\s*/\s*(?P<flat>\d+[A-Za-z]?)|\s+(?:m\.|lok\.)\s*(?P<flat_word>\d+[A-Za-z]?))?"
    r"\s*,\s*(?P<postcode>\d{2}-\d{3})\s+(?P<city>\S.*?)\s*$"
)
DROPPED_STREET_PREFIX = re.compile(r"^ul\.\s+", re.IGNORECASE)
AMBIGUOUS_STREET = re.compile(r"\d\s*/|\b(?:m|lok)\.$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedAddress:
    street: str
    building: str
    flat: str | None
    postcode: str
    city: str

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"street": self.street, "building": self.building}
        if self.flat is not None:
            payload["flat"] = self.flat
        payload.update({"postcode": self.postcode, "city": self.city, "country": "PL"})
        return payload


def parse_address(line: str) -> ParsedAddress | None:
    found = ADDRESS.match(line.strip())
    if found is None:
        return None
    street = DROPPED_STREET_PREFIX.sub("", found["street"]).strip()
    if not street or AMBIGUOUS_STREET.search(street):
        return None
    return ParsedAddress(
        street=street,
        building=found["building"],
        flat=found["flat"] or found["flat_word"],
        postcode=found["postcode"],
        city=found["city"],
    )
