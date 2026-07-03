#!/usr/bin/env python3
import unittest
from unittest import mock

import weather_image


OPEN_METEO_SAMPLE = {
    "current": {
        "temperature_2m": 18.8,
        "apparent_temperature": 17.0,
        "relative_humidity_2m": 72,
        "weather_code": 2,
        "wind_speed_10m": 9.7,
        "wind_direction_10m": 238,
        "pressure_msl": 1021.6,
    },
    "daily": {
        "time": ["2026-07-03", "2026-07-04", "2026-07-05"],
        "temperature_2m_max": [24.2, 22.8, 21.1],
        "temperature_2m_min": [12.1, 13.4, 11.8],
        "precipitation_probability_max": [15, 60, 25],
        "sunrise": [
            "2026-07-03T04:45",
            "2026-07-04T04:46",
            "2026-07-05T04:47",
        ],
        "sunset": [
            "2026-07-03T21:33",
            "2026-07-04T21:32",
            "2026-07-05T21:31",
        ],
        "weather_code": [2, 61, 3],
    },
}

GEOCODING_SAMPLE = {
    "results": [
        {
            "id": 2641170,
            "name": "Nottingham",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "elevation": 46,
            "feature_code": "PPLA2",
            "country_code": "GB",
            "admin1": "England",
            "country": "United Kingdom",
            "timezone": "Europe/London",
            "population": 323632,
        },
        {
            "id": 741098,
            "name": "İstanbul",
            "latitude": 41.0138,
            "longitude": 28.9497,
            "admin1": "Istanbul",
            "country": "Türkiye",
            "timezone": "Europe/Istanbul",
        },
    ]
}


class OpenMeteoConversionTests(unittest.TestCase):
    def test_weather_code_mapping(self):
        expected = {
            0: "clear",
            2: "partly_cloudy",
            3: "cloudy",
            45: "fog",
            61: "rain",
            71: "snow",
            95: "storm",
        }
        for code, kind in expected.items():
            with self.subTest(code=code):
                self.assertEqual(
                    weather_image.open_meteo_weather_kind(code),
                    kind,
                )

    def test_wind_degrees_to_compass(self):
        self.assertEqual(weather_image.degrees_to_compass(0), "N")
        self.assertEqual(weather_image.degrees_to_compass(90), "E")
        self.assertEqual(weather_image.degrees_to_compass(180), "S")
        self.assertEqual(weather_image.degrees_to_compass(238), "WSW")
        self.assertEqual(weather_image.degrees_to_compass(359), "N")

    def test_kmh_to_mph(self):
        self.assertEqual(weather_image.kmh_to_mph(0), 0)
        self.assertEqual(weather_image.kmh_to_mph(9.7), 6)
        self.assertEqual(weather_image.kmh_to_mph(16.1), 10)

    def test_open_meteo_normalization_matches_renderer_contract(self):
        result = weather_image.normalize_open_meteo(OPEN_METEO_SAMPLE)
        current = result["current_condition"][0]
        today = result["weather"][0]
        tomorrow = result["weather"][1]

        self.assertEqual(current["temp_C"], "19")
        self.assertEqual(current["FeelsLikeC"], "17")
        self.assertEqual(current["humidity"], "72")
        self.assertEqual(current["windspeedMiles"], "6")
        self.assertEqual(current["winddir16Point"], "WSW")
        self.assertEqual(current["pressure"], "1022")
        self.assertEqual(current["weatherCode"], "partly_cloudy")
        self.assertEqual(today["maxtempC"], "24")
        self.assertEqual(today["mintempC"], "12")
        self.assertEqual(today["astronomy"][0]["sunrise"], "04:45")
        self.assertEqual(today["astronomy"][0]["sunset"], "21:33")
        self.assertEqual(today["hourly"][4]["chanceofrain"], "15")
        self.assertEqual(tomorrow["hourly"][4]["weatherCode"], "rain")

    def test_fetch_weather_falls_back_to_wttr(self):
        fallback = {
            "current_condition": [{
                "temp_C": "20",
                "FeelsLikeC": "18",
            }],
            "weather": [],
        }
        with (
            mock.patch(
                "weather_image.fetch_open_meteo",
                side_effect=RuntimeError("controlled failure"),
            ),
            mock.patch(
                "weather_image.fetch_wttr",
                return_value=fallback,
            ) as wttr,
        ):
            result = weather_image.fetch_weather("Nottingham", "Europe/London")

        self.assertIs(result, fallback)
        wttr.assert_called_once_with("Nottingham")

    def test_geocoding_results_are_normalized_without_raw_fields(self):
        with mock.patch(
            "weather_image.http_json",
            return_value=GEOCODING_SAMPLE,
        ):
            results = weather_image.geocode_locations("Nottingham")

        self.assertEqual(results[0], {
            "city": "Nottingham",
            "region": "England",
            "country": "United Kingdom",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "timezone": "Europe/London",
            "display_name": "Nottingham, England, United Kingdom",
        })
        self.assertEqual(results[1]["city"], "İstanbul")
        self.assertNotIn("population", results[0])
        self.assertNotIn("id", results[0])

    def test_coordinate_weather_fetch_skips_geocoding(self):
        with mock.patch(
            "weather_image.http_json",
            return_value=OPEN_METEO_SAMPLE,
        ) as http_json:
            weather_image.fetch_open_meteo(
                "Nottingham",
                "Europe/London",
                latitude=52.9536,
                longitude=-1.1505,
            )

        self.assertEqual(http_json.call_count, 1)
        url = http_json.call_args.args[0]
        self.assertIn("latitude=52.9536", url)
        self.assertIn("longitude=-1.1505", url)


if __name__ == "__main__":
    unittest.main()
