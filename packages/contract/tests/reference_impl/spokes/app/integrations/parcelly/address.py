import re
from dataclasses import dataclass
from typing import Any

ADDRESS = re.compile(
    r"^(?:(?:Flat|Apt|Unit)\s+(?P<flat>\d+[A-Za-z]?)\s*,\s*)?"
    r"(?P<building>\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?)\s+(?P<street>[^,]*?\S)"
    r"(?:\s*,\s*(?:Flat|Apt|Unit)\s+(?P<flat_after>\d+[A-Za-z]?))?"
    r"\s*,\s*(?P<city>[^,]*?\S)\s+(?P<postcode>[A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2})\s*$"
)


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
        payload.update({"postcode": self.postcode, "city": self.city, "country": "GB"})
        return payload


def parse_address(line: str) -> ParsedAddress | None:
    found = ADDRESS.match(line.strip())
    if found is None:
        return None
    return ParsedAddress(
        street=found["street"],
        building=found["building"],
        flat=found["flat"] or found["flat_after"],
        postcode=found["postcode"],
        city=found["city"],
    )
