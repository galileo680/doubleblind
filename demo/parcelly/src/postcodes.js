const POSTCODE_AREA_TO_CITY = Object.freeze({
  E: "London",
  EC: "London",
  N: "London",
  NW: "London",
  SE: "London",
  SW: "London",
  W: "London",
  WC: "London",
  M: "Manchester",
  B: "Birmingham",
  L: "Liverpool",
  LS: "Leeds",
  S: "Sheffield",
  BS: "Bristol",
  NG: "Nottingham",
  NE: "Newcastle upon Tyne",
  G: "Glasgow",
  EH: "Edinburgh",
  CF: "Cardiff",
  OX: "Oxford",
  CB: "Cambridge",
  BN: "Brighton",
});

function normalizeCity(city) {
  return city.normalize("NFC").trim().toLocaleLowerCase("en-GB");
}

function postcodeArea(postcode) {
  return postcode.match(/^[A-Z]+/)[0];
}

const KNOWN_CITIES = new Set(Object.values(POSTCODE_AREA_TO_CITY).map(normalizeCity));

export function postcodeMatchesCity(postcode, city) {
  const expected = POSTCODE_AREA_TO_CITY[postcodeArea(postcode)];
  const normalizedCity = normalizeCity(city);
  if (expected !== undefined) {
    return normalizeCity(expected) === normalizedCity;
  }
  return !KNOWN_CITIES.has(normalizedCity);
}
