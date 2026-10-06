export const POSTCODE_PREFIX_TO_CITY = Object.freeze({
  "00": "Warszawa",
  "01": "Warszawa",
  "02": "Warszawa",
  "03": "Warszawa",
  "04": "Warszawa",
  10: "Olsztyn",
  15: "Białystok",
  20: "Lublin",
  25: "Kielce",
  30: "Kraków",
  31: "Kraków",
  35: "Rzeszów",
  40: "Katowice",
  45: "Opole",
  50: "Wrocław",
  51: "Wrocław",
  53: "Wrocław",
  60: "Poznań",
  61: "Poznań",
  65: "Zielona Góra",
  70: "Szczecin",
  71: "Szczecin",
  80: "Gdańsk",
  81: "Gdynia",
  85: "Bydgoszcz",
  87: "Toruń",
  90: "Łódź",
  91: "Łódź",
  92: "Łódź",
  93: "Łódź",
  94: "Łódź",
});

function normalizeCity(city) {
  return city.normalize("NFC").trim().toLocaleLowerCase("pl-PL");
}

const KNOWN_CITIES = new Set(Object.values(POSTCODE_PREFIX_TO_CITY).map(normalizeCity));

export function postcodeMatchesCity(postcode, city) {
  const expected = POSTCODE_PREFIX_TO_CITY[postcode.slice(0, 2)];
  const normalizedCity = normalizeCity(city);
  if (expected !== undefined) {
    return normalizeCity(expected) === normalizedCity;
  }
  return !KNOWN_CITIES.has(normalizedCity);
}
