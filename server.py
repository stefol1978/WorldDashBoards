from flask import Flask, jsonify, send_from_directory
import requests
import threading
import time
import os
from datetime import datetime, timezone

app = Flask(__name__)

# Paths are resolved from server.py so deployment does not depend
# on the process working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")


# =========================================================
# CORS
# =========================================================
# Allows the dashboard to be opened directly as file://
# while still calling the local Flask API.
@app.after_request
def add_cors_headers(response):

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, OPTIONS"
    )

    return response



# =========================================================
# API
# =========================================================

USGS_URL = (
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/"
    "summary/all_hour.geojson"
)

SPACE_URL = (
    "https://ll.thespacedevs.com/2.3.0/launches/upcoming/"
)

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
)

CURRENTS_URL = (
    "https://api.currentsapi.services/v1/latest-news"
)

CURRENTS_API_KEY = os.environ.get(
    "CURRENTS_API_KEY"
)

# GeoNames geocoding
# Set GEONAMES_USERNAME to your free GeoNames username.
GEONAMES_USERNAME = os.environ.get(
    "GEONAMES_USERNAME"
)

GEONAMES_URL = (
    "https://secure.geonames.org/searchJSON"
)

geocode_cache = {}

country_metadata = {}
country_metadata_updated = None


# World Bank - World population (WLD / SP.POP.TOTL)
WORLD_POPULATION_URL = (
    "https://api.worldbank.org/v2/country/WLD/indicator/"
    "SP.POP.TOTL"
)

COUNTRY_POPULATION_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/"
    "SP.POP.TOTL"
)

world_population = {
    "population": None,
    "year": None,
    "previous_population": None,
    "previous_year": None,
    "growth_per_year": 0.0,
    "updated": None
}

country_populations = {}
country_population_year = None
country_populations_updated = None


# World Bank macro indicators
COUNTRY_GDP_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/"
    "NY.GDP.MKTP.CD"
)

COUNTRY_GDP_PER_CAPITA_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/"
    "NY.GDP.PCAP.CD"
)

COUNTRY_GDP_GROWTH_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/"
    "NY.GDP.MKTP.KD.ZG"
)

country_macro = {}
country_macro_updated = None


# IMF DataMapper - WEO general government gross debt (% GDP)
IMF_DEBT_URL = (
    "https://www.imf.org/external/datamapper/api/v2/"
    "GGXWDG_NGDP"
)

country_debt = {}
country_debt_updated = None


# =========================================================
# EVENT STORAGE
# =========================================================

events = []
seen = set()


def add_event(event):

    events.insert(0, event)

    # Massimo 30 eventi
    del events[30:]


# =========================================================
# WORLD POPULATION
# =========================================================

def load_world_population():

    print("🌍 World population: caricamento World Bank...")

    try:

        response = requests.get(
            WORLD_POPULATION_URL,
            params={
                "format": "json",
                "per_page": 100
            },
            timeout=20
        )

        print(
            f"🌍 World Bank population HTTP: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        data = response.json()

        if (
            not isinstance(data, list)
            or len(data) < 2
            or not isinstance(data[1], list)
        ):
            raise ValueError(
                "Risposta World Bank WLD non valida"
            )

        rows = data[1]

        print(
            f"🌍 World Bank population rows: "
            f"{len(rows)}"
        )

        values = []

        for row in rows:

            if not isinstance(row, dict):
                continue

            value = row.get("value")
            year = row.get("date")

            if value is None or year is None:
                continue

            try:

                values.append(
                    (
                        int(year),
                        float(value)
                    )
                )

            except (ValueError, TypeError):

                continue

        values.sort(
            key=lambda item: item[0],
            reverse=True
        )

        # -------------------------------------------------
        # PRIMARY: World Bank WLD aggregate
        # -------------------------------------------------

        if values:

            latest_year, latest_population = values[0]

            previous = None

            for item in values[1:]:

                if item[0] < latest_year:
                    previous = item
                    break

            previous_year = None
            previous_population = None
            growth_per_year = 0.0

            if previous:

                previous_year, previous_population = previous

                years = (
                    latest_year -
                    previous_year
                )

                if (
                    years > 0
                    and previous_population > 0
                ):

                    growth_per_year = (
                        latest_population -
                        previous_population
                    ) / years

            world_population["population"] = (
                latest_population
            )

            world_population["year"] = (
                latest_year
            )

            world_population["previous_population"] = (
                previous_population
            )

            world_population["previous_year"] = (
                previous_year
            )

            world_population["growth_per_year"] = (
                growth_per_year
            )

            world_population["updated"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            print(
                f"🌍 WORLD POPULATION: "
                f"{latest_population:,.0f} "
                f"(World Bank {latest_year})"
            )

            return

        # -------------------------------------------------
        # FALLBACK: sum latest available country values
        # -------------------------------------------------

        print(
            "⚠️ World Bank WLD senza valori: "
            "uso fallback country/all"
        )

        page = 1
        per_page = 400
        all_rows = []

        while True:

            fallback_response = requests.get(
                COUNTRY_POPULATION_URL,
                params={
                    "format": "json",
                    "per_page": per_page,
                    "page": page
                },
                timeout=30
            )

            print(
                f"🌍 World Bank country population "
                f"page {page} HTTP: "
                f"{fallback_response.status_code}"
            )

            fallback_response.raise_for_status()

            fallback_data = (
                fallback_response.json()
            )

            if (
                not isinstance(fallback_data, list)
                or len(fallback_data) < 2
            ):
                raise ValueError(
                    "Fallback World Bank country response "
                    "non valido"
                )

            all_rows.extend(
                fallback_data[1] or []
            )

            meta = fallback_data[0] or {}

            total_pages = int(
                meta.get("pages", page)
            )

            if page >= total_pages:
                break

            page += 1

            time.sleep(0.15)

        latest_by_country = {}

        for row in all_rows:

            if not isinstance(row, dict):
                continue

            iso3 = (
                row.get("countryiso3code")
                or ""
            ).upper()

            value = row.get("value")
            year = row.get("date")

            if (
                len(iso3) != 3
                or iso3 == "WLD"
                or value is None
                or year is None
            ):
                continue

            try:

                year_int = int(year)
                population = float(value)

            except (ValueError, TypeError):

                continue

            if (
                iso3 not in latest_by_country
                or year_int >
                latest_by_country[iso3]["year"]
            ):

                latest_by_country[iso3] = {

                    "population":
                        population,

                    "year":
                        year_int

                }

        if not latest_by_country:

            raise ValueError(
                "Fallback country/all senza dati"
            )

        # Prefer a common latest year where possible.
        years = [
            item["year"]
            for item in latest_by_country.values()
        ]

        target_year = max(
            set(years),
            key=years.count
        )

        total_population = 0.0

        for item in latest_by_country.values():

            if item["year"] == target_year:

                total_population += (
                    item["population"]
                )

        if total_population <= 0:

            raise ValueError(
                "Fallback country/all ha prodotto "
                "una popolazione nulla"
            )

        world_population["population"] = (
            total_population
        )

        world_population["year"] = (
            target_year
        )

        world_population["previous_population"] = None
        world_population["previous_year"] = None
        world_population["growth_per_year"] = 0.0

        world_population["updated"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        print(
            f"🌍 WORLD POPULATION: "
            f"{total_population:,.0f} "
            f"(World Bank country fallback "
            f"{target_year}, "
            f"{len(latest_by_country)} countries)"
        )

    except Exception as error:

        print(
            "❌ Errore popolazione mondiale:",
            repr(error)
        )


def world_population_monitor():

    while True:

        load_world_population()

        # Aggiorna una volta al giorno
        time.sleep(86400)


def get_live_world_population():

    population = world_population["population"]

    if population is None:
        return None

    base_year = world_population["year"] or datetime.now(
        timezone.utc
    ).year

    growth_per_year = (
        world_population["growth_per_year"] or 0.0
    )

    now = datetime.now(timezone.utc)

    # Interpolazione/proiezione lineare dalla stima
    # annuale più recente disponibile.
    elapsed_years = (
        now.year +
        (
            now.timetuple().tm_yday - 1
        ) / 365.25 +
        (
            now.hour * 3600 +
            now.minute * 60 +
            now.second
        ) / (365.25 * 86400)
        - base_year
    )

    live_population = (
        population +
        growth_per_year * elapsed_years
    )

    return max(
        0,
        round(live_population)
    )


# =========================================================
# COUNTRY METADATA - STATIC OPEN DATA
# =========================================================
# Country metadata is loaded from a public static dataset rather than
# GeoNames. This avoids API credentials/rate limits for the country
# mouseover. GeoNames remains available below only for dynamic weather
# geocoding.

COUNTRY_METADATA_URL = (
    "https://raw.githubusercontent.com/"
    "mledoze/countries/master/countries.json"
)

COUNTRY_METADATA_FALLBACK_URL = (
    "https://raw.githubusercontent.com/"
    "lukes/ISO-3166-Countries-with-Regional-Codes/"
    "master/all/all.json"
)


def load_country_metadata():

    global country_metadata
    global country_metadata_updated

    try:

        response = requests.get(
            COUNTRY_METADATA_URL,
            timeout=30
        )

        response.raise_for_status()

        rows = response.json()

        if not isinstance(rows, list):
            raise ValueError(
                "Country metadata JSON non valido"
            )

        metadata = {}

        for row in rows:

            if not isinstance(row, dict):
                continue

            cca3 = (
                row.get("cca3")
                or ""
            ).upper()

            if len(cca3) != 3:
                continue

            name_data = (
                row.get("name")
                or {}
            )

            if isinstance(name_data, dict):

                name = (
                    name_data.get("common")
                    or name_data.get("official")
                )

            else:

                name = str(name_data)

            capital_data = (
                row.get("capital")
                or []
            )

            capital = (
                capital_data[0]
                if isinstance(capital_data, list)
                and capital_data
                else None
            )

            region = (
                row.get("region")
                or row.get("subregion")
            )

            area = row.get("area")

            try:
                area = (
                    float(area)
                    if area is not None
                    else None
                )
            except (ValueError, TypeError):
                area = None

            currencies = (
                row.get("currencies")
                or {}
            )

            currency_codes = []

            if isinstance(currencies, dict):

                for code, info in currencies.items():

                    currency_codes.append(
                        code
                    )

            languages = (
                row.get("languages")
                or {}
            )

            language_names = []

            if isinstance(languages, dict):

                language_names = list(
                    languages.values()
                )

            timezones = (
                row.get("timezones")
                or []
            )

            timezone_value = (
                timezones[0]
                if isinstance(timezones, list)
                and timezones
                else None
            )

            metadata[cca3] = {

                "name":
                    name,

                "capital":
                    capital,

                "region":
                    region,

                "area":
                    area,

                "currency":
                    ", ".join(
                        currency_codes
                    ) or None,

                "languages":
                    ", ".join(
                        language_names
                    ) or None,

                "timezone":
                    timezone_value

            }

        if not metadata:
            raise ValueError(
                "Dataset country metadata vuoto"
            )

        country_metadata = metadata

        country_metadata_updated = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        print(
            f"🌍 Loaded country metadata: "
            f"{len(country_metadata)} countries "
            f"(open dataset)"
        )

    except Exception as error:

        print(
            "Errore country metadata:",
            error
        )


def country_metadata_monitor():

    print(
        "🌍 Country metadata monitor avviato"
    )

    while True:

        load_country_metadata()

        # Static metadata changes rarely.
        time.sleep(604800)


# =========================================================
# COUNTRY POPULATIONS
# =========================================================

def load_country_populations():

    global country_populations
    global country_population_year
    global country_populations_updated

    try:

        # World Bank can paginate this endpoint. Fetch all pages so
        # the country mouseover gets the complete country set.
        page = 1
        per_page = 400
        all_rows = []

        while True:

            response = requests.get(
                COUNTRY_POPULATION_URL,
                params={
                    "format": "json",
                    "per_page": per_page,
                    "page": page
                },
                timeout=30
            )

            response.raise_for_status()

            data = response.json()

            if (
                not isinstance(data, list)
                or len(data) < 2
            ):
                raise ValueError(
                    "Risposta World Bank popolazioni non valida"
                )

            rows = data[1] or []
            all_rows.extend(rows)

            meta = data[0] or {}

            total_pages = int(
                meta.get("pages", page)
            )

            if page >= total_pages:
                break

            page += 1

            # Be polite to the public API.
            time.sleep(0.2)

        rows = all_rows

        # Teniamo l'anno più recente disponibile per ogni ISO3.
        latest = {}

        for row in rows:

            iso3 = row.get("countryiso3code")
            value = row.get("value")
            year = row.get("date")

            if not iso3 or value is None or year is None:
                continue

            # Evita aggregati regionali/di reddito privi di ISO3.
            if len(iso3) != 3:
                continue

            try:

                year_int = int(year)
                population = float(value)

            except (ValueError, TypeError):

                continue

            if (
                iso3 not in latest
                or year_int > latest[iso3]["year"]
            ):

                latest[iso3] = {

                    "population":
                        population,

                    "year":
                        year_int

                }

        country_populations = latest

        years = [
            item["year"]
            for item in latest.values()
        ]

        country_population_year = (
            max(years)
            if years
            else None
        )

        country_populations_updated = (
            datetime.now(timezone.utc).isoformat()
        )

        print(
            f"🌍 Loaded populations: "
            f"{len(country_populations)} countries "
            f"(latest data around "
            f"{country_population_year}) "
            f"[World Bank pages: {page}]"
        )

    except Exception as error:

        print(
            "Errore popolazioni nazionali:",
            error
        )


def country_population_monitor():

    print(
        "🌍 Country population monitor avviato"
    )

    while True:

        load_country_populations()

        # Aggiornamento giornaliero
        time.sleep(86400)


# =========================================================
# COUNTRY MACRO DATA
# =========================================================

def load_world_bank_indicator(url):

    page = 1
    per_page = 400
    all_rows = []

    while True:

        response = requests.get(
            url,
            params={
                "format": "json",
                "per_page": per_page,
                "page": page
            },
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        if (
            not isinstance(data, list)
            or len(data) < 2
        ):
            raise ValueError(
                "Risposta World Bank indicatore non valida"
            )

        all_rows.extend(
            data[1] or []
        )

        meta = data[0] or {}

        total_pages = int(
            meta.get("pages", page)
        )

        if page >= total_pages:
            break

        page += 1

        time.sleep(0.15)

    return all_rows


def latest_indicator_values(rows):

    latest = {}

    for row in rows:

        iso3 = (
            row.get("countryiso3code")
            or ""
        ).upper()

        value = row.get("value")
        year = row.get("date")

        if (
            len(iso3) != 3
            or value is None
            or year is None
        ):
            continue

        try:

            year_int = int(
                str(year)[:4]
            )

            numeric_value = float(
                value
            )

        except (ValueError, TypeError):

            continue

        if (
            iso3 not in latest
            or year_int > latest[iso3]["year"]
        ):

            latest[iso3] = {

                "value":
                    numeric_value,

                "year":
                    year_int

            }

    return latest


def load_country_macro():

    global country_macro
    global country_macro_updated

    try:

        print(
            "💰 Country macro monitor: "
            "caricamento World Bank..."
        )

        gdp_rows = load_world_bank_indicator(
            COUNTRY_GDP_URL
        )

        gdp_pc_rows = load_world_bank_indicator(
            COUNTRY_GDP_PER_CAPITA_URL
        )

        growth_rows = load_world_bank_indicator(
            COUNTRY_GDP_GROWTH_URL
        )

        gdp = latest_indicator_values(
            gdp_rows
        )

        gdp_pc = latest_indicator_values(
            gdp_pc_rows
        )

        growth = latest_indicator_values(
            growth_rows
        )

        macro = {}

        for iso3 in (
            set(gdp.keys())
            | set(gdp_pc.keys())
            | set(growth.keys())
        ):

            gdp_item = gdp.get(
                iso3,
                {}
            )

            gdp_pc_item = gdp_pc.get(
                iso3,
                {}
            )

            growth_item = growth.get(
                iso3,
                {}
            )

            macro[iso3] = {

                "gdp":
                    gdp_item.get(
                        "value"
                    ),

                "gdp_year":
                    gdp_item.get(
                        "year"
                    ),

                "gdp_per_capita":
                    gdp_pc_item.get(
                        "value"
                    ),

                "gdp_per_capita_year":
                    gdp_pc_item.get(
                        "year"
                    ),

                "gdp_growth":
                    growth_item.get(
                        "value"
                    ),

                "gdp_growth_year":
                    growth_item.get(
                        "year"
                    )

            }

        country_macro = macro

        country_macro_updated = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        print(
            f"💰 Loaded macro data: "
            f"{len(country_macro)} countries "
            f"(World Bank)"
        )

    except Exception as error:

        print(
            "Errore dati macro World Bank:",
            error
        )


def load_country_debt():

    global country_debt
    global country_debt_updated

    try:

        response = requests.get(
            IMF_DEBT_URL,
            params={
                "periods": "2024,2025,2026"
            },
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        values = (
            data.get("values", {})
            if isinstance(data, dict)
            else {}
        )

        # DataMapper normally returns:
        # values -> indicator -> country -> year -> value
        indicator_values = values.get(
            "GGXWDG_NGDP",
            {}
        )

        if not indicator_values:

            # Some API responses use the full indicator ID.
            for key, item in values.items():

                if (
                    isinstance(item, dict)
                    and (
                        "GGXWDG_NGDP" in key
                        or "GGXWDG" in key
                    )
                ):

                    indicator_values = item
                    break

        debt = {}

        if isinstance(
            indicator_values,
            dict
        ):

            for iso3, series in (
                indicator_values.items()
            ):

                if not isinstance(
                    series,
                    dict
                ):
                    continue

                selected_year = None
                selected_value = None

                # Prefer the most recent actual/available year.
                for year in sorted(
                    series.keys(),
                    key=lambda x: int(x),
                    reverse=True
                ):

                    value = series.get(
                        year
                    )

                    if value is None:
                        continue

                    try:

                        selected_year = int(
                            year
                        )

                        selected_value = float(
                            value
                        )

                        break

                    except (
                        ValueError,
                        TypeError
                    ):

                        continue

                if (
                    selected_year is not None
                    and selected_value is not None
                ):

                    debt[
                        iso3.upper()
                    ] = {

                        "debt_to_gdp":
                            selected_value,

                        "debt_year":
                            selected_year

                    }

        if not debt:

            raise ValueError(
                "IMF debt response vuota"
            )

        country_debt = debt

        country_debt_updated = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        print(
            f"🏦 Loaded public debt: "
            f"{len(country_debt)} countries "
            f"(IMF WEO)"
        )

    except Exception as error:

        print(
            "Errore debito IMF:",
            error
        )


def country_macro_monitor():

    print(
        "💰 Country macro monitor avviato"
    )

    while True:

        load_country_macro()

        load_country_debt()

        # Macro data does not need minute-level refreshes.
        time.sleep(86400)


# =========================================================
# EARTHQUAKE
# =========================================================

def earthquake_score(magnitude, depth):

    if magnitude >= 7:
        score = 100

    elif magnitude >= 6:
        score = 80

    elif magnitude >= 5:
        score = 50

    elif magnitude >= 4:
        score = 20

    else:
        score = 5

    if depth < 10:
        score += 20

    elif depth < 30:
        score += 10

    return score


def earthquake_level(score):

    if score >= 100:
        return "CRITICAL", "🚨"

    elif score >= 70:
        return "HIGH", "🔥"

    else:
        return "MEDIUM", "⚠️"


def earthquake_monitor():

    print("🌍 Earthquake monitor avviato")

    first_run = True

    while True:

        try:

            response = requests.get(
                USGS_URL,
                timeout=10
            )

            response.raise_for_status()

            data = response.json()

            for earthquake in data["features"]:

                earthquake_id = earthquake["id"]

                if earthquake_id in seen:
                    continue

                properties = earthquake["properties"]
                geometry = earthquake["geometry"]

                magnitude = properties["mag"]

                if magnitude is None:

                    seen.add(earthquake_id)

                    continue


                # Ignora terremoti sotto M1.0
                if magnitude < 1.0:

                    seen.add(earthquake_id)

                    continue


                place = properties["place"]

                timestamp = properties["time"]

                longitude = geometry["coordinates"][0]
                latitude = geometry["coordinates"][1]
                depth = geometry["coordinates"][2]

                score = earthquake_score(
                    magnitude,
                    depth
                )                   

                seen.add(earthquake_id)

                if score < 20:
                    continue

                level, emoji = earthquake_level(
                    score
                )

                event = {

                    "id": earthquake_id,

                    "time": datetime.fromtimestamp(
                        timestamp / 1000,
                        tz=timezone.utc
                    ).strftime(
                        "%H:%M:%S UTC"
                    ),

                    "type": "EARTHQUAKE",

                    "title": place,

                    "magnitude": round(
                        magnitude,
                        1
                    ),

                    "depth": round(
                        depth,
                        1
                    ),

                    "latitude": round(
                        latitude,
                        3
                    ),

                    "longitude": round(
                        longitude,
                        3
                    ),

                    "score": score,

                    "level": level,

                    "emoji": emoji
                }

                add_event(event)

                if first_run:

                    print(
                        f"🌍 Loaded earthquake: "
                        f"M{magnitude} - {place}"
                    )

                else:

                    print(
                        f"{emoji} NEW EARTHQUAKE: "
                        f"M{magnitude} - {place}"
                    )

            first_run = False

        except Exception as error:

            print(
                "Errore terremoti:",
                error
            )

        time.sleep(60)


# =========================================================
# SPACE
# =========================================================

def parse_iso_datetime(value):

    if not value:
        return None

    try:

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

    except (ValueError, TypeError):

        return None


def load_space_events_once():


    try:

        response = requests.get(
            SPACE_URL,
            params={
                "limit": 10,
                "mode": "normal"
            },
            timeout=15
        )

        if response.status_code == 429:

            print(
                "⚠️ Space API rate limit (429) - "
                "riprovare tra 30 minuti"
            )

            time.sleep(1800)

            continue

        response.raise_for_status()

        data = response.json()

        for launch in data.get(
            "results",
            []
        ):

            launch_id = (
                "SPACE-" +
                launch["id"]
            )

            if launch_id in seen:
                continue


            # -----------------------------------------
            # BASIC DATA
            # -----------------------------------------

            name = launch.get(
                "name"
            ) or "Unnamed launch"

            net_string = launch.get(
                "net"
            )

            status_data = (
                launch.get("status")
                or {}
            )

            status = (
                status_data.get("name")
                or "Status unavailable"
            )


            # -----------------------------------------
            # MISSION
            # -----------------------------------------

            mission = (
                launch.get("mission")
                or {}
            )

            if isinstance(
                mission,
                dict
            ):

                mission_name = (
                    mission.get("name")
                )

                mission_description = (
                    mission.get(
                        "description"
                    )
                )

            else:

                mission_name = None

                mission_description = None


            # -----------------------------------------
            # LAUNCH PAD
            # -----------------------------------------

            pad = (
                launch.get("pad")
                or {}
            )

            if isinstance(
                pad,
                dict
            ):

                pad_name = (
                    pad.get("name")
                )

                pad_latitude = (
                    pad.get("latitude")
                )

                pad_longitude = (
                    pad.get("longitude")
                )

                pad_location = (
                    pad.get("location")
                    or {}
                )

            else:

                pad_name = None

                pad_latitude = None

                pad_longitude = None

                pad_location = {}


            # -----------------------------------------
            # LOCATION
            # -----------------------------------------

            if isinstance(
                pad_location,
                dict
            ):

                location_name = (
                    pad_location.get(
                        "name"
                    )
                )

            else:

                location_name = None


            # -----------------------------------------
            # ROCKET
            # -----------------------------------------

            rocket = (
                launch.get("rocket")
                or {}
            )

            if isinstance(
                rocket,
                dict
            ):

                rocket_configuration = (
                    rocket.get(
                        "configuration"
                    )
                    or {}
                )

            else:

                rocket_configuration = {}


            if not isinstance(
                rocket_configuration,
                dict
            ):

                rocket_configuration = {}


            rocket_name = (
                rocket_configuration.get(
                    "full_name"
                )
                or
                rocket_configuration.get(
                    "name"
                )
            )


            # -----------------------------------------
            # LAUNCH SERVICE PROVIDER
            # -----------------------------------------

            provider = (
                launch.get(
                    "launch_service_provider"
                )
                or {}
            )

            if isinstance(
                provider,
                dict
            ):

                provider_name = (
                    provider.get(
                        "name"
                    )
                )

            else:

                provider_name = None


            # -----------------------------------------
            # LAUNCH TIME
            # -----------------------------------------

            launch_time = (
                parse_iso_datetime(
                    net_string
                )
            )


            # -----------------------------------------
            # LAUNCH TODAY - FUTURE ONLY
            # -----------------------------------------

            launch_today = False

            if launch_time:

                now = datetime.now(
                    timezone.utc
                )

                launch_today = (
                    launch_time.date() == now.date()
                    and
                    launch_time > now
                )


            # -----------------------------------------
            # LAUNCH WINDOW
            # -----------------------------------------

            window_start = (
                launch.get(
                    "window_start"
                )
            )

            window_end = (
                launch.get(
                    "window_end"
                )
            )


            # -----------------------------------------
            # SCORE
            # -----------------------------------------

            score = 30

            if "in flight" in status.lower():

                score = 90

            elif launch_time:

                now = datetime.now(
                    timezone.utc
                )

                seconds_until = (
                    launch_time - now
                ).total_seconds()

                # Entro un'ora
                if (
                    0 <
                    seconds_until <=
                    3600
                ):

                    score = 70

                # Entro 24 ore
                elif (
                    0 <
                    seconds_until <=
                    86400
                ):

                    score = 50


            # Un lancio di oggi è importante
            if launch_today and score < 50:

                score = 50


            # -----------------------------------------
            # LEVEL
            # -----------------------------------------

            if score >= 90:

                level = "CRITICAL"

                emoji = "🚨"

            elif score >= 70:

                level = "HIGH"

                emoji = "🔥"

            elif launch_today:

                level = "HIGH"

                emoji = "🚀"

            else:

                level = "NORMAL"

                emoji = "🚀"


            # -----------------------------------------
            # EVENT
            # -----------------------------------------

            event = {

                "id":
                    launch_id,

                "time":
                    net_string,

                "type":
                    "SPACE",

                "title":
                    name,

                "status":
                    status,

                "mission":
                    mission_name,

                "description":
                    mission_description,

                "pad":
                    pad_name,

                "location":
                    location_name,

                "pad_latitude":
                    pad_latitude,

                "pad_longitude":
                    pad_longitude,

                "rocket":
                    rocket_name,

                "provider":
                    provider_name,

                "launch_today":
                    launch_today,

                "window_start":
                    window_start,

                "window_end":
                    window_end,

                "score":
                    score,

                "level":
                    level,

                "emoji":
                    emoji
            }


            add_event(
                event
            )

            seen.add(
                launch_id
            )


            if launch_today:

                print(
                    f"🚀 SPACE TODAY: "
                    f"{name} | "
                    f"{location_name or 'Location TBD'} | "
                    f"{net_string}"
                )

            else:

                print(
                    f"{emoji} SPACE: "
                    f"{name}"
                )

    except Exception as error:

        print(
            "Errore spazio:",
            error
        )


    # Space: ogni 30 minuti


def load_space_events():

    try:

        response = requests.get(
            SPACE_URL,
            params={"limit": 10, "mode": "normal"},
            timeout=15
        )

        if response.status_code == 429:
            print("⚠️ Space API rate limit (429)")
            return

        response.raise_for_status()
        data = response.json()

        loaded = 0

        for launch in data.get("results", []):

            launch_id = "SPACE-" + str(launch.get("id"))

            if launch_id in seen:
                continue

            name = launch.get("name") or "Unnamed launch"
            net_string = launch.get("net")

            status_data = launch.get("status") or {}
            status = (
                status_data.get("name")
                or "Status unavailable"
            )

            mission = launch.get("mission") or {}

            mission_name = (
                mission.get("name")
                if isinstance(mission, dict)
                else None
            )

            mission_description = (
                mission.get("description")
                if isinstance(mission, dict)
                else None
            )

            pad = launch.get("pad") or {}

            pad_name = (
                pad.get("name")
                if isinstance(pad, dict)
                else None
            )

            pad_latitude = (
                pad.get("latitude")
                if isinstance(pad, dict)
                else None
            )

            pad_longitude = (
                pad.get("longitude")
                if isinstance(pad, dict)
                else None
            )

            pad_location = (
                pad.get("location") or {}
                if isinstance(pad, dict)
                else {}
            )

            location_name = (
                pad_location.get("name")
                if isinstance(pad_location, dict)
                else None
            )

            rocket = launch.get("rocket") or {}

            rocket_configuration = (
                rocket.get("configuration") or {}
                if isinstance(rocket, dict)
                else {}
            )

            rocket_name = (
                rocket_configuration.get("full_name")
                or rocket_configuration.get("name")
                if isinstance(rocket_configuration, dict)
                else None
            )

            provider = (
                launch.get("launch_service_provider")
                or {}
            )

            provider_name = (
                provider.get("name")
                if isinstance(provider, dict)
                else None
            )

            launch_time = parse_iso_datetime(net_string)

            launch_today = False

            if launch_time:

                now = datetime.now(timezone.utc)

                launch_today = (
                    launch_time.date() == now.date()
                    and launch_time > now
                )

            score = 30

            if "in flight" in status.lower():

                score = 90

            elif launch_time:

                now = datetime.now(timezone.utc)

                seconds_until = (
                    launch_time - now
                ).total_seconds()

                if 0 < seconds_until <= 3600:
                    score = 70

                elif 0 < seconds_until <= 86400:
                    score = 50

            if launch_today and score < 50:
                score = 50

            if score >= 90:
                level = "CRITICAL"
                emoji = "🚨"

            elif score >= 70:
                level = "HIGH"
                emoji = "🔥"

            elif launch_today:
                level = "HIGH"
                emoji = "🚀"

            else:
                level = "NORMAL"
                emoji = "🚀"

            event = {
                "id": launch_id,
                "time": net_string,
                "type": "SPACE",
                "title": name,
                "status": status,
                "mission": mission_name,
                "description": mission_description,
                "pad": pad_name,
                "location": location_name,
                "pad_latitude": pad_latitude,
                "pad_longitude": pad_longitude,
                "rocket": rocket_name,
                "provider": provider_name,
                "launch_today": launch_today,
                "window_start": launch.get("window_start"),
                "window_end": launch.get("window_end"),
                "score": score,
                "level": level,
                "emoji": emoji
            }

            add_event(event)
            seen.add(launch_id)
            loaded += 1

            if launch_today:
                print(
                    f"🚀 SPACE TODAY: {name} | "
                    f"{location_name or 'Location TBD'} | "
                    f"{net_string}"
                )
            else:
                print(f"{emoji} SPACE: {name}")

        print(f"🚀 Loaded space events: {loaded}")

    except Exception as error:

        print(
            "Errore caricamento spazio:",
            error
        )


def space_monitor():

    print("🚀 Space monitor avviato")

    while True:

        load_space_events()

        time.sleep(1800)


# =========================================================
# GEONAMES / WEATHER GEOCODING
# =========================================================

def geocode_location(location_name):

    if not location_name:
        return None, None

    key = location_name.strip().lower()

    if key in geocode_cache:
        cached = geocode_cache[key]
        return cached["latitude"], cached["longitude"]

    # Fallback: use the coordinates already configured for known locations.
    for location in LOCATIONS:
        if location["name"].strip().lower() == key:
            geocode_cache[key] = {
                "latitude": location["latitude"],
                "longitude": location["longitude"]
            }
            return (
                location["latitude"],
                location["longitude"]
            )

    if not GEONAMES_USERNAME:
        print(
            f"⚠️ GeoNames username non impostato: "
            f"{location_name}"
        )
        return None, None

    try:

        response = requests.get(
            GEONAMES_URL,
            params={
                "q": location_name,
                "maxRows": 1,
                "featureClass": "P",
                "username": GEONAMES_USERNAME
            },
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        results = data.get("geonames", [])

        if not results:
            print(
                f"⚠️ GeoNames: località non trovata: "
                f"{location_name}"
            )
            return None, None

        result = results[0]

        latitude = float(result["lat"])
        longitude = float(result["lng"])

        geocode_cache[key] = {
            "latitude": latitude,
            "longitude": longitude
        }

        print(
            f"📍 GeoNames: {location_name} "
            f"→ {latitude:.4f}, {longitude:.4f}"
        )

        return latitude, longitude

    except Exception as error:

        print(
            f"Errore GeoNames ({location_name}):",
            error
        )

        return None, None


# =========================================================
# WEATHER
# =========================================================

LOCATIONS = [

    {
        "name": "Bologna",
        "latitude": 44.4949,
        "longitude": 11.3426
    },

    {
        "name": "Tokyo",
        "latitude": 35.6762,
        "longitude": 139.6503
    },

    {
        "name": "New York",
        "latitude": 40.7128,
        "longitude": -74.0060
    },

    {
        "name": "London",
        "latitude": 51.5074,
        "longitude": -0.1278
    },

    {
        "name": "Dubai",
        "latitude": 25.2048,
        "longitude": 55.2708
    }

]


def weather_score(
    temperature,
    wind,
    precipitation
):

    score = 0

    if (
        temperature >= 40
        or temperature <= -20
    ):

        score += 70

    elif (
        temperature >= 35
        or temperature <= -10
    ):

        score += 40

    if wind >= 80:

        score += 70

    elif wind >= 60:

        score += 40

    if precipitation >= 20:

        score += 40

    return score


def load_weather_events_once():


    try:

        for location in LOCATIONS:

            params = {

                "latitude":
                    location["latitude"],

                "longitude":
                    location["longitude"],

                "current":
                    (
                        "temperature_2m,"
                        "wind_speed_10m,"
                        "precipitation"
                    ),

                "timezone":
                    "auto"
            }

            response = requests.get(
                WEATHER_URL,
                params=params,
                timeout=10
            )

            response.raise_for_status()

            data = response.json()

            current = data["current"]

            temperature = (
                current["temperature_2m"]
            )

            wind = (
                current["wind_speed_10m"]
            )

            precipitation = (
                current["precipitation"]
            )

            score = weather_score(
                temperature,
                wind,
                precipitation
            )

            if score < 40:
                continue

            event_id = (

                f"WEATHER-"
                f"{location['name']}-"
                f"{temperature}-"
                f"{wind}-"
                f"{precipitation}"

            )

            if event_id in seen:
                continue

            if score >= 70:

                level = "HIGH"
                emoji = "🚨"

            else:

                level = "MEDIUM"
                emoji = "⚠️"

            event = {

                "id": event_id,

                "time":
                    datetime.now(
                        timezone.utc
                    ).strftime(
                        "%H:%M:%S UTC"
                    ),

                "type": "WEATHER",

                "title":
                    (
                        "Extreme weather - "
                        + location["name"]
                    ),

                "location":
                    location["name"],

                "latitude":
                    location["latitude"],

                "longitude":
                    location["longitude"],

                "temperature":
                    temperature,

                "wind":
                    wind,

                "precipitation":
                    precipitation,

                "score":
                    score,

                "level":
                    level,

                "emoji":
                    emoji
            }

            add_event(event)

            seen.add(event_id)

            print(
                f"{emoji} WEATHER: "
                f"{location['name']} "
                f"{temperature}°C "
                f"wind={wind} km/h "
                f"score={score}"
            )

    except Exception as error:

        print(
            "Errore meteo:",
            error
        )



def weather_monitor(once=False):

    print('🌦️ Weather monitor avviato')

    while True:

        load_weather_events_once()

        if once:

            return

        time.sleep(600)


# =========================================================
# NEWS - CURRENTS
# =========================================================

NEWS_EVENT_GROUPS = {

    "EARTHQUAKE": [
        "earthquake",
        "quake",
        "seismic",
        "aftershock"
    ],

    "TSUNAMI": [
        "tsunami",
        "tidal wave"
    ],

    "VOLCANO": [
        "volcano",
        "volcanic",
        "eruption",
        "erupted"
    ],

    "WEATHER": [
        "hurricane",
        "typhoon",
        "tornado",
        "cyclone",
        "flood",
        "flooding",
        "wildfire",
        "forest fire",
        "extreme weather",
        "storm surge"
    ],

    "ACCIDENT": [
        "explosion",
        "blast",
        "major accident",
        "plane crash",
        "aircraft crash",
        "train crash",
        "train derailment",
        "ship collision",
        "industrial accident",
        "building collapse",
        "bridge collapse"
    ],

    "EMERGENCY": [
        "state of emergency",
        "emergency declared",
        "mass casualty",
        "evacuation",
        "rescue operation",
        "disaster",
        "major incident"
    ],

    "CONFLICT": [
        "missile strike",
        "missile attack",
        "airstrike",
        "air strike",
        "rocket attack",
        "drone strike",
        "military attack",
        "military offensive",
        "invasion",
        "war declared",
        "ceasefire",
        "armed conflict"
    ],

    "SPACE": [
        "rocket launch",
        "space launch",
        "launches into space",
        "rocket lifts off",
        "rocket liftoff",
        "spacecraft launch",
        "satellite launch"
    ]

}


NEWS_BLOCKED_WORDS = [

    "concert",
    "concerts",
    "music",
    "album",
    "song",
    "singer",
    "actress",
    "actor",
    "celebrity",
    "celebrities",
    "fashion",
    "red carpet",
    "reality show",
    "tv show",
    "television",
    "movie",
    "film",
    "hollywood",

    "wrestling",
    "aew",
    "wwe",

    "football",
    "soccer",
    "basketball",
    "baseball",
    "tennis",
    "sports",
    "match",
    "championship",

    "gaming",
    "video game",

    "bitcoin",
    "cryptocurrency",
    "stock market",
    "wall street",
    "earnings",
    "quarterly results",

    "artificial intelligence",
    "ai startup",
    "chatgpt",

    "iphone",
    "smartphone"

]


def get_news_event_type(title):

    title_lower = title.lower()

    for event_type, keywords in NEWS_EVENT_GROUPS.items():

        for keyword in keywords:

            if keyword in title_lower:

                return event_type

    return None


def is_blocked_news(title):

    title_lower = title.lower()

    for keyword in NEWS_BLOCKED_WORDS:

        if keyword in title_lower:

            return True

    return False


def is_relevant_news(title):

    if is_blocked_news(title):

        return False

    event_type = get_news_event_type(
        title
    )

    if event_type is None:

        return False

    return True


def news_score(title):

    title_lower = title.lower()

    event_type = get_news_event_type(
        title
    )

    score = 40

    critical_words = [

        "major",
        "mass casualty",
        "state of emergency",
        "evacuation",
        "missile strike",
        "airstrike",
        "invasion",
        "explosion",
        "tsunami",
        "earthquake",
        "volcano",
        "hurricane"

    ]

    for word in critical_words:

        if word in title_lower:

            score += 10

    if "breaking" in title_lower:

        score += 10

    if event_type in [

        "EARTHQUAKE",
        "TSUNAMI",
        "VOLCANO",
        "CONFLICT"

    ]:

        score += 10

    elif event_type in [

        "ACCIDENT",
        "EMERGENCY",
        "WEATHER"

    ]:

        score += 5

    return min(
        score,
        100
    )


def load_news_events_once():


    try:

        headers = {

            "Authorization":
                (
                    "Bearer "
                    +
                    CURRENTS_API_KEY
                )

        }

        params = {

            "language":
                "en",

            "page_size":
                20

        }

        response = requests.get(

            CURRENTS_URL,

            params=params,

            headers=headers,

            timeout=20

        )

        if response.status_code == 429:

            print(
                "⚠️ Currents rate limit (429) - "
                "riprovare tra 30 minuti"
            )

            time.sleep(1800)

            continue

        if response.status_code == 401:

            print(
                "❌ Currents API key "
                "non valida o mancante"
            )

            time.sleep(1800)

            continue

        response.raise_for_status()

        data = response.json()

        articles = data.get(
            "news",
            []
        )

        accepted = 0

        for article in articles:

            title = (
                article.get("title")
                or
                ""
            ).strip()

            url = (
                article.get("url")
                or
                ""
            ).strip()

            if not title or not url:
                continue

            if not is_relevant_news(
                title
            ):

                continue

            news_id = (
                "NEWS-" +
                url
            )

            if news_id in seen:
                continue

            score = news_score(
                title
            )

            if score >= 80:

                level = "CRITICAL"
                emoji = "🚨"

            elif score >= 60:

                level = "HIGH"
                emoji = "🔥"

            elif score >= 50:

                level = "MEDIUM"
                emoji = "⚠️"

            else:

                level = "NORMAL"
                emoji = "📰"

            event_type = (
                get_news_event_type(
                    title
                )
            )

            source = (

                article.get(
                    "source"
                )

                or

                article.get(
                    "author"
                )

                or

                "News"

            )

            published = (
                article.get(
                    "published"
                )
            )

            news_time = (
                datetime.now(
                    timezone.utc
                ).strftime(
                    "%H:%M:%S UTC"
                )
            )

            if published:

                try:

                    parsed = (
                        datetime.fromisoformat(
                            published.replace(
                                "Z",
                                "+00:00"
                            )
                        )
                    )

                    news_time = (
                        parsed.astimezone(
                            timezone.utc
                        ).strftime(
                            "%H:%M:%S UTC"
                        )
                    )

                except Exception:

                    pass

            clean_title = title

            if event_type:
                prefix = f"[{event_type}]"
                if clean_title.upper().startswith(prefix):
                    clean_title = clean_title[len(prefix):].strip(" -:|")

            event = {

                "id":
                    news_id,

                "time":
                    news_time,

                "type":
                    "NEWS",

                "title":
                    clean_title,

                "source":
                    source,

                "author":
                    source,

                "url":
                    url,

                "category":
                    event_type,

                "score":
                    score,

                "level":
                    level,

                "emoji":
                    emoji

            }

            add_event(
                event
            )

            seen.add(
                news_id
            )

            accepted += 1

            print(

                f"{emoji} NEWS: "
                f"[{event_type}] "
                f"{title} "
                f"[{source}]"

            )

            if accepted >= 5:

                break

    except Exception as error:

        print(
            "Errore news:",
            error
        )



def news_monitor(once=False):

    print('📰 News monitor avviato')

    while True:

        load_news_events_once()

        if once:

            return

        time.sleep(420)


# =========================================================
# WEB
# =========================================================

@app.route("/")
def home():

    return send_from_directory(
        WEB_DIR,
        "index.html"
    )


@app.route("/ambient.mp3")
def ambient_music():

    return send_from_directory(
        WEB_DIR,
        "ambient.mp3",
        mimetype="audio/mpeg"
    )


@app.route("/api/population")
def get_population():

    population = get_live_world_population()

    if population is None:

        print(
            "⚠️ /api/population richiesto: "
            "World Bank population ancora non disponibile"
        )

    response = jsonify({

        "population": population,

        "population_formatted":
            (
                f"{population:,}"
                if population is not None
                else None
            ),

        "population_billions":
            (
                round(
                    population / 1_000_000_000,
                    3
                )
                if population is not None
                else None
            ),

        "source":
            "World Bank",

        "base_year":
            world_population["year"],

        "previous_year":
            world_population["previous_year"],

        "growth_per_year":
            world_population["growth_per_year"],

        "updated":
            world_population["updated"]

    })

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"

    return response


@app.route("/api/countries")
def get_countries():

    result = {}

    iso_codes = (
        set(country_populations.keys())
        | set(country_metadata.keys())
        | set(country_macro.keys())
        | set(country_debt.keys())
    )

    for iso3 in iso_codes:

        population_item = (
            country_populations.get(iso3)
            or {}
        )

        metadata_item = (
            country_metadata.get(iso3)
            or {}
        )

        macro_item = (
            country_macro.get(iso3)
            or {}
        )

        debt_item = (
            country_debt.get(iso3)
            or {}
        )

        gdp = macro_item.get(
            "gdp"
        )

        debt_ratio = debt_item.get(
            "debt_to_gdp"
        )

        debt_year = debt_item.get(
            "debt_year"
        )

        gdp_year = macro_item.get(
            "gdp_year"
        )

        # Estimate absolute public debt using the available
        # GDP and the IMF gross-debt/GDP ratio. The two sources
        # can have different vintages, so keep the IMF debt year
        # separately instead of hiding the value.
        public_debt = None

        if (
            gdp is not None
            and debt_ratio is not None
        ):

            public_debt = (
                gdp *
                debt_ratio /
                100.0
            )

        result[iso3] = {

            "name":
                metadata_item.get(
                    "name"
                ),

            "population":
                (
                    round(
                        population_item[
                            "population"
                        ]
                    )
                    if population_item.get(
                        "population"
                    ) is not None
                    else None
                ),

            "year":
                population_item.get(
                    "year"
                ),

            "capital":
                metadata_item.get(
                    "capital"
                ),

            "region":
                metadata_item.get(
                    "region"
                ),

            "area":
                metadata_item.get(
                    "area"
                ),

            "currency":
                metadata_item.get(
                    "currency"
                ),

            "languages":
                metadata_item.get(
                    "languages"
                ),

            "gdp":
                gdp,

            "gdp_year":
                gdp_year,

            "gdp_per_capita":
                macro_item.get(
                    "gdp_per_capita"
                ),

            "gdp_per_capita_year":
                macro_item.get(
                    "gdp_per_capita_year"
                ),

            "gdp_growth":
                macro_item.get(
                    "gdp_growth"
                ),

            "gdp_growth_year":
                macro_item.get(
                    "gdp_growth_year"
                ),

            "debt_to_gdp":
                debt_ratio,

            "debt_year":
                debt_year,

            "public_debt":
                public_debt,

            "public_debt_year":
                (
                    debt_year
                    if public_debt is not None
                    else None
                )

        }

    return jsonify({

        "countries":
            result,

        "count":
            len(result),

        "sources": [
            "World Bank",
            "IMF WEO",
            "Open country dataset"
        ],

        "updated": {

            "population":
                country_populations_updated,

            "metadata":
                country_metadata_updated,

            "macro":
                country_macro_updated,

            "debt":
                country_debt_updated

        }

    })


@app.route("/api/country-populations")
def get_country_populations():

    result = {}

    for iso3, item in country_populations.items():

        result[iso3] = {

            "population":
                round(item["population"]),

            "year":
                item["year"]

        }

    return jsonify({

        "countries":
            result,

        "count":
            len(result),

        "source":
            "World Bank",

        "updated":
            country_populations_updated

    })


@app.route("/api/events")
def get_events():

    return jsonify(events)


@app.route("/api/geocode")
def geocode_api():

    from flask import request

    location = (
        request.args.get("location")
        or ""
    ).strip()

    if not location:
        return jsonify({
            "error": "missing location"
        }), 400

    latitude, longitude = geocode_location(
        location
    )

    if latitude is None or longitude is None:
        return jsonify({
            "location": location,
            "found": False
        })

    return jsonify({
        "location": location,
        "found": True,
        "latitude": latitude,
        "longitude": longitude
    })


# =========================================================
# BACKGROUND MONITORS / START
# =========================================================

_monitors_started = False


def start_background_monitors():

    global _monitors_started

    if _monitors_started:
        return

    _monitors_started = True

    print()
    print("======================================")
    print("       WORLDWIDE EVENTS ENGINE")
    print("======================================")
    print()

    threads = [

        threading.Thread(
            target=earthquake_monitor,
            daemon=True
        ),

        threading.Thread(
            target=space_monitor,
            daemon=True
        ),

        threading.Thread(
            target=weather_monitor,
            daemon=True
        ),

        threading.Thread(
            target=news_monitor,
            daemon=True
        ),

        threading.Thread(
            target=world_population_monitor,
            daemon=True
        ),

        threading.Thread(
            target=country_population_monitor,
            daemon=True
        ),

        threading.Thread(
            target=country_metadata_monitor,
            daemon=True
        ),

        threading.Thread(
            target=country_macro_monitor,
            daemon=True
        )

    ]

    for thread in threads:
        thread.start()


# Gunicorn imports "app" instead of executing this file as __main__.
# Populate the critical datasets synchronously before accepting requests.
# This prevents the browser from seeing an empty dataset during startup.
print("🚀 Initial data bootstrap...")

try:
    load_world_population()
except Exception as error:
    print("❌ Initial world population bootstrap:", repr(error))

try:
    load_country_populations()
except Exception as error:
    print("❌ Initial country population bootstrap:", repr(error))

try:
    load_country_metadata()
except Exception as error:
    print("❌ Initial country metadata bootstrap:", repr(error))

# Bootstrap event feeds once before starting their periodic loops.
# This prevents /api/events from returning [] during the first page load.
print("🚀 Initial event bootstrap...")

try:
    load_space_events_once()
except Exception as error:
    print("❌ Initial space bootstrap:", repr(error))

try:
    load_weather_events_once()
except Exception as error:
    print("❌ Initial weather bootstrap:", repr(error))

try:
    load_news_events_once()
except Exception as error:
    print("❌ Initial news bootstrap:", repr(error))

# Start the periodic/background monitors after the initial datasets exist.

print("🚀 Initial event bootstrap...")

try:
    load_space_events()
except Exception as error:
    print("❌ Initial space bootstrap:", repr(error))

try:
    weather_monitor(once=True)
except Exception as error:
    print("❌ Initial weather bootstrap:", repr(error))

try:
    news_monitor(once=True)
except Exception as error:
    print("❌ Initial news bootstrap:", repr(error))


start_background_monitors()


if __name__ == "__main__":

    import logging

    logging.getLogger(
        "werkzeug"
    ).setLevel(
        logging.ERROR
    )

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "5000"
            )
        ),
        debug=False
    )

