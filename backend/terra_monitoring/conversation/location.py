"""Resolve supplied device coordinates to ground elevation through USGS EPQS."""
from __future__ import annotations

import json
import math

import httpx

from ..farm_ops.store import utc

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
MAX_RESPONSE_BYTES = 65536


class LocationLookupError(RuntimeError):
    """A retryable public terrain lookup failed before any profile was saved."""


def _number(value, minimum, maximum):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and minimum <= value <= maximum)


def device_coordinates(value):
    if (not isinstance(value, dict) or not _number(value.get("lat"), -90, 90)
            or not _number(value.get("lon"), -180, 180)):
        raise ValueError("Provide finite device coordinates")
    accuracy = value.get("accuracy_m")
    if accuracy is not None and (not isinstance(accuracy, (int, float)) or isinstance(accuracy, bool)
                                 or not math.isfinite(accuracy) or accuracy < 0):
        raise ValueError("Provide finite nonnegative device accuracy")
    # Device altitude, names and other browser properties never supply terrain.
    return {"lat": float(value["lat"]), "lon": float(value["lon"]),
            "accuracy_m": float(accuracy) if accuracy is not None else None}


class GroundElevationResolver:
    def __init__(self, *, client=None):
        self.client = client

    def resolve(self, device_location, *, now=None):
        position = device_coordinates(device_location)
        client = self.client or httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False)
        try:
            with client.stream("GET", EPQS_URL, params={"x": position["lon"], "y": position["lat"],
                    "wkid": 4326, "units": "Feet", "includeDate": "true"},
                    headers={"Accept": "application/json"}, auth=None, follow_redirects=False, timeout=10.0) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise LocationLookupError("Terrain response exceeded its bounded size")
            body = json.loads(content)
            if not isinstance(body, dict) or not isinstance(body.get("location"), dict):
                raise LocationLookupError("Terrain location is unavailable")
            echo = body["location"]
            spatial = echo.get("spatialReference")
            if (not _number(echo.get("x"), -180, 180) or not _number(echo.get("y"), -90, 90)
                    or not math.isclose(echo["x"], position["lon"], rel_tol=0, abs_tol=1e-7)
                    or not math.isclose(echo["y"], position["lat"], rel_tol=0, abs_tol=1e-7)
                    or not isinstance(spatial, dict) or spatial.get("wkid") != 4326
                    or spatial.get("latestWkid", 4326) != 4326
                    or not _number(body.get("value"), -1500, 20000)):
                raise LocationLookupError("Terrain coordinates or elevation are unusable")
            attributes = body.get("attributes")
            acquisition = attributes.get("AcquisitionDate") if isinstance(attributes, dict) else None
            if not isinstance(acquisition, str) or len(acquisition) > 80 or any(ord(c) < 32 for c in acquisition):
                acquisition = None
            raster = body.get("rasterId")
            resolution = body.get("resolution")
            provenance = {"provider": "USGS EPQS", "url": EPQS_URL, "units": "Feet", "wkid": 4326,
                          "retrieved_at": utc(now).isoformat(),
                          "raster_id": raster if isinstance(raster, int) and not isinstance(raster, bool) and 0 <= raster < 2**63 else None,
                          "resolution": resolution if _number(resolution, 0, 1e6) else None,
                          "acquisition_date": acquisition}
            return {**position, "elevation_ft": float(body["value"]), "elevation_source": provenance}
        except LocationLookupError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError, UnicodeError, OverflowError):
            raise LocationLookupError("Terrain lookup is temporarily unavailable") from None
        finally:
            if self.client is None:
                client.close()
