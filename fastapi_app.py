from __future__ import annotations

import json
import os
import re
import time
from io import BytesIO
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException
from geopy.geocoders import Nominatim
from pydantic import BaseModel, Field

try:
    from apify_client import ApifyClient  # type: ignore
except Exception:
    ApifyClient = None  # type: ignore


PHANTOMBUSTER_AGENT_ID = "5654810775307548"

app = FastAPI(title="Business Extractor API", version="1.0.0")


class LocationInput(BaseModel):
    maps_url: Optional[str] = Field(default=None, description="Google Maps URL containing @lat,lon")
    latitude: Optional[float] = Field(default=None, description="Latitude")
    longitude: Optional[float] = Field(default=None, description="Longitude")
    address: Optional[str] = Field(default=None, description="Free-form address (geocoded via Nominatim)")


class PhantomConfig(BaseModel):
    enabled: bool = False
    api_key: Optional[str] = None
    market: str = "en-US"
    number_of_lines_to_process: int = 4
    agent_id: str = PHANTOMBUSTER_AGENT_ID


class ApifyConfig(BaseModel):
    enabled: bool = False
    token: Optional[str] = None
    actor_id: str = "Vb6LZkh4EqRlR0Ka9"
    max_items: int = 25
    profile_scraper_mode: str = "Full ($8 per 1k)"
    company_batch_mode: str = "all_at_once"


class BusinessesRequest(BaseModel):
    apiKey: str
    keyword: str
    lat: float
    lon: float
    radius: int = 1000
    pageSize: int = 20
    pageIndex: int = 0
    pageToken: Optional[str] = None


class ContactsRequest(BaseModel):
    domain: Optional[str] = None
    placeId: Optional[str] = None
    address: Optional[str] = None
    apiKey: Optional[str] = None


class RunRequest(BaseModel):
    google_api_key: str
    industry: str
    radius_m: int = 1000
    location: LocationInput
    enrich: bool = True
    phantom: Optional[PhantomConfig] = None
    apify: Optional[ApifyConfig] = None
    linkedin_company_urls: Optional[list[str]] = None


def extract_lat_lon_from_url(url: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        if "google.com/maps" not in url:
            response = requests.get(url, allow_redirects=True, timeout=10)
            url = response.url
        match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None, None
    except Exception:
        return None, None


def get_lat_lon_from_address(address: str) -> Tuple[Optional[float], Optional[float]]:
    geolocator = Nominatim(user_agent="fastapi_biz_finder")
    try:
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception:
        return None, None


def get_place_details(place_id: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "formatted_phone_number,website",
        "key": api_key,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            result = response.json().get("result", {})
            return result.get("formatted_phone_number"), result.get("website")
    except Exception:
        pass
    return None, None


def extract_email_from_website(website_url: Optional[str]) -> Optional[str]:
    if not website_url:
        return None
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        response = requests.get(website_url, timeout=10, headers=headers)
        if response.status_code == 200:
            email_pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
            emails = re.findall(email_pattern, response.text)
            unique_emails = set()
            for email in emails:
                if not any(
                    email.lower().endswith(ext)
                    for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".woff", ".ttf"]
                ):
                    unique_emails.add(email)
            return ", ".join(list(unique_emails)[:3]) if unique_emails else None
    except Exception:
        pass
    return None


def _parse_address_parts(address: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if not address:
        return None, None, None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return None, None, None
    country = parts[-1] if len(parts) >= 1 else None
    state = parts[-2] if len(parts) >= 2 else None
    city = parts[-3] if len(parts) >= 3 else None
    return country, city, state


def _email_to_name(email: str) -> Tuple[Optional[str], Optional[str]]:
    local = (email or "").split("@")[0]
    local = re.sub(r"[^a-zA-Z0-9._-]+", " ", local)
    parts = [p for p in re.split(r"[._-]+", local) if p]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0].capitalize(), None
    return parts[0].capitalize(), parts[-1].capitalize()


def fetch_places_page(
    api_key: str,
    location: Tuple[float, float],
    radius: int,
    keyword: str,
    page_token: Optional[str] = None,
) -> Tuple[list[dict], Optional[str]]:
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    if page_token:
        params = {"pagetoken": page_token, "key": api_key}
    else:
        params = {
            "location": f"{location[0]},{location[1]}",
            "radius": radius,
            "keyword": keyword,
            "key": api_key,
        }

    response = requests.get(url, params=params, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"Google Places API error: {response.text}")
    data = response.json()
    status = (data.get("status") or "").upper()
    if status not in {"OK", "ZERO_RESULTS"}:
        if status == "INVALID_REQUEST" and page_token:
            raise RuntimeError("next_page_token not ready; wait 2 seconds and retry")
        raise RuntimeError(f"Google Places API error: {data}")
    return data.get("results", []), data.get("next_page_token")


def fetch_places(api_key: str, location: Tuple[float, float], radius: int, keyword: str) -> list[dict]:
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    results: list[dict] = []

    params = {
        "location": f"{location[0]},{location[1]}",
        "radius": radius,
        "keyword": keyword,
        "key": api_key,
    }

    while True:
        response = requests.get(url, params=params, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"Google Places API error: {response.text}")
        data = response.json()
        results.extend(data.get("results", []))
        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break
        time.sleep(2)
        params["pagetoken"] = next_page_token

    return results


def process_basic_results(results: list[dict]) -> pd.DataFrame:
    cleaned_data = []
    for place in results:
        loc = place.get("geometry", {}).get("location", {})
        cleaned_data.append(
            {
                "Name": place.get("name"),
                "Rating": place.get("rating", "N/A"),
                "Address": place.get("vicinity"),
                "lat": loc.get("lat"),
                "lon": loc.get("lng"),
                "Place ID": place.get("place_id"),
                "Phone": "Pending...",
                "Website": "Pending...",
                "Email": "Pending...",
            }
        )
    return pd.DataFrame(cleaned_data)


def enrich_data(df: pd.DataFrame, api_key: str, enable_email: bool = True) -> pd.DataFrame:
    for index, row in df.iterrows():
        place_id = row["Place ID"]
        phone, website = get_place_details(place_id, api_key)
        email = extract_email_from_website(website) if enable_email else None
        df.at[index, "Phone"] = phone
        df.at[index, "Website"] = website
        df.at[index, "Email"] = email
    return df


class PhantomBusterClient:
    def __init__(
        self,
        api_key: str,
        org_id: Optional[str] = None,
        base_url: str = "https://api.phantombuster.com/api/v2",
        timeout_s: int = 30,
        tmpfiles_upload_url: str = "https://tmpfiles.org/api/v1/upload",
    ):
        self.api_key = api_key
        self.org_id = org_id
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.tmpfiles_upload_url = tmpfiles_upload_url

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Phantombuster-Key": self.api_key,
                "Content-Type": "application/json",
            }
        )
        if self.org_id:
            self.session.headers["X-Phantombuster-Org"] = self.org_id

    @staticmethod
    def _normalize_tmpfiles_download_url(url: str) -> str:
        url = (url or "").strip()
        if not url:
            return url
        if "tmpfiles.org/dl/" in url:
            return url
        marker = "tmpfiles.org/"
        idx = url.find(marker)
        if idx == -1:
            return url
        prefix = url[: idx + len(marker)]
        rest = url[idx + len(marker) :].lstrip("/")
        return f"{prefix}dl/{rest}"

    def upload_bytes_to_tmpfiles(self, filename: str, data: bytes) -> str:
        if not data:
            raise ValueError("No data to upload")
        files = {"file": (filename, data)}
        r = requests.post(self.tmpfiles_upload_url, files=files, timeout=self.timeout_s)
        r.raise_for_status()
        payload = r.json()
        url = (((payload or {}).get("data") or {}).get("url") or "").strip()
        if not url:
            raise RuntimeError(f"tmpfiles upload response missing data.url: {payload}")
        return self._normalize_tmpfiles_download_url(url)

    def launch_agent(
        self,
        agent_id: str,
        argument: Optional[Dict[str, Any]] = None,
        bonus_argument: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/agents/launch"
        payload: Dict[str, Any] = {"id": agent_id}
        if argument is not None:
            payload["argument"] = argument
        if bonus_argument is not None:
            payload["bonusArgument"] = bonus_argument
        r = self.session.post(url, data=json.dumps(payload), timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def fetch_container(self, container_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/containers/fetch"
        r = self.session.get(url, params={"id": container_id}, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def fetch_container_output(self, container_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/containers/fetch-output"
        r = self.session.get(url, params={"id": container_id}, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def fetch_agent(self, agent_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/agents/fetch"
        r = self.session.get(url, params={"id": agent_id}, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def build_result_download_url(org_s3_folder: str, s3_folder: str, filename: str, ext: str) -> str:
        ext = (ext or "").lstrip(".")
        filename = (filename or "").strip() or "result"
        return f"https://phantombuster.s3.amazonaws.com/{org_s3_folder}/{s3_folder}/{filename}.{ext}"

    def download_bytes(self, url: str) -> bytes:
        r = self.session.get(url, timeout=self.timeout_s)
        r.raise_for_status()
        return r.content

    def wait_for_container(
        self,
        container_id: str,
        poll_s: float = 5.0,
        timeout_s: float = 10 * 60,
    ) -> Dict[str, Any]:
        start = time.time()
        while True:
            c = self.fetch_container(container_id)
            status = (c.get("status") or c.get("state") or "").lower()
            ended = c.get("endedAt") or c.get("endTime") or c.get("finishedAt") or c.get("ended")

            if status in {"success", "succeeded", "finished", "done"} or ended:
                return c
            if status in {"error", "failed", "killed", "stopped", "aborted"}:
                return c

            if time.time() - start > timeout_s:
                raise TimeoutError(f"Container {container_id} did not finish within {timeout_s} seconds")

            time.sleep(poll_s)


def _df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def _build_phantom_argument(
    spreadsheet_url: str,
    csv_name: str,
    market: str,
    number_of_lines_to_process: int,
) -> Dict[str, Any]:
    return {
        "csvName": csv_name,
        "market": market,
        "spreadsheetUrl": spreadsheet_url,
        "numberOfLinesToProcess": number_of_lines_to_process,
    }


def _is_linkedin_company_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return ("linkedin.com/company/" in u) and (u.startswith("http://") or u.startswith("https://"))


def _extract_linkedin_company_urls(obj: Any, limit: int = 500) -> list[str]:
    found: list[str] = []

    def add(u: str) -> None:
        u = (u or "").strip()
        if not u:
            return
        if u.startswith("www.linkedin.com/company/"):
            u = "https://" + u
        if u.startswith("linkedin.com/company/"):
            u = "https://" + u
        if _is_linkedin_company_url(u) and u not in found:
            found.append(u)

    def walk(x: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(x, str):
            for m in re.findall(r"https?://[^\s\"'>]+", x):
                add(m)
            add(x)
            return
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
            return
        if isinstance(x, list):
            for v in x:
                walk(v)
            return

    if isinstance(obj, pd.DataFrame):
        for col in obj.columns:
            if str(obj[col].dtype).lower() in {"object", "string"}:
                for v in obj[col].dropna().astype(str).tolist():
                    walk(v)
                    if len(found) >= limit:
                        break
        return found

    walk(obj)
    return found


def _read_pb_results_bytes_as_df(data: bytes, ext: str) -> Optional[pd.DataFrame]:
    ext = (ext or "").lower().lstrip(".")
    if not data:
        return None
    try:
        if ext == "csv":
            return pd.read_csv(BytesIO(data))
        if ext == "json":
            parsed = json.loads(data.decode("utf-8", errors="replace"))
            if isinstance(parsed, list):
                return pd.DataFrame(parsed)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("data"), list):
                    return pd.DataFrame(parsed["data"])
                return pd.DataFrame([parsed])
    except Exception:
        return None
    return None


def _normalize_domain(value: str) -> str:
    s = (value or "").strip().lower()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        host = urlparse(s).netloc
    else:
        host = s.split("/")[0]
    host = host.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0]
    if "." not in host:
        return ""
    return host


def _filter_pb_results_to_current_run(df_pb: pd.DataFrame, df_run_companies: pd.DataFrame) -> pd.DataFrame:
    if df_pb is None or df_pb.empty or df_run_companies is None or df_run_companies.empty:
        return df_pb

    run_domains: list[str] = []
    if "Website" in df_run_companies.columns:
        run_domains = [
            d
            for d in df_run_companies["Website"].fillna("").astype(str).map(_normalize_domain).tolist()
            if d
        ]
    run_domains = list(dict.fromkeys(run_domains))

    run_names: list[str] = []
    if "Name" in df_run_companies.columns:
        run_names = [n.strip().lower() for n in df_run_companies["Name"].fillna("").astype(str).tolist() if n.strip()]
    run_names = list(dict.fromkeys(run_names))

    if not run_domains and not run_names:
        return df_pb

    df_str = df_pb.copy()
    for c in df_str.columns:
        df_str[c] = df_str[c].fillna("").astype(str)

    urlish_cols = [c for c in df_pb.columns if any(k in c.lower() for k in ["website", "url", "domain", "site"])]
    nameish_cols = [c for c in df_pb.columns if any(k in c.lower() for k in ["company", "name", "organization", "org"])]

    mask = pd.Series(False, index=df_pb.index)

    if run_domains:
        if urlish_cols:
            for col in urlish_cols:
                col_domains = df_str[col].map(_normalize_domain)
                mask = mask | col_domains.isin(run_domains)
        else:
            haystack = df_str.apply(lambda r: " | ".join(r.values.astype(str)).lower(), axis=1)
            chunk_size = 150
            for i in range(0, len(run_domains), chunk_size):
                chunk = run_domains[i : i + chunk_size]
                rx = "(" + "|".join(re.escape(d) for d in chunk) + ")"
                mask = mask | haystack.str.contains(rx, regex=True)

    if run_names:
        cols = nameish_cols or list(df_pb.columns)
        cols = cols[:10]
        for col in cols:
            s = df_str[col].str.strip().str.lower()
            mask = mask | s.isin(run_names)

    filtered = df_pb[mask].copy()
    return filtered if not filtered.empty else df_pb


def _run_apify_people_scraper(
    token: str,
    actor_id: str,
    companies: list[str],
    max_items: int = 25,
    profile_scraper_mode: str = "Full ($8 per 1k)",
    company_batch_mode: str = "all_at_once",
) -> Tuple[list[dict], dict]:
    if ApifyClient is None:
        raise RuntimeError("Missing dependency: apify-client. Run: pip install -r requirements.txt")
    token = (token or "").strip()
    actor_id = (actor_id or "").strip()
    if not token:
        raise RuntimeError("Missing APIFY_TOKEN")
    if not actor_id:
        raise RuntimeError("Missing Apify Actor ID")
    if not companies:
        raise RuntimeError("No LinkedIn company URLs provided/found.")

    client = ApifyClient(token)
    run_input: Dict[str, Any] = {
        "profileScraperMode": profile_scraper_mode,
        "maxItems": int(max_items),
        "companies": companies,
        "locations": [],
        "companyBatchMode": company_batch_mode,
    }
    run_input = {k: v for k, v in run_input.items() if v is not None}

    run = client.actor(actor_id).call(run_input=run_input)
    items: list[dict] = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        if isinstance(item, dict):
            items.append(item)
        else:
            items.append({"value": item})
    return items, run


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    if df is None:
        return []
    safe_df = df.fillna("")
    return safe_df.to_dict(orient="records")


def _business_item_from_place(place: dict, api_key: str) -> dict:
    loc = place.get("geometry", {}).get("location", {})
    place_id = place.get("place_id")
    phone, website = get_place_details(place_id, api_key) if place_id else (None, None)
    domain = _normalize_domain(website or "")
    address = place.get("vicinity") or place.get("formatted_address")
    country, city, state = _parse_address_parts(address)
    return {
        "id": place_id,
        "name": place.get("name"),
        "domain": domain,
        "lat": loc.get("lat"),
        "lon": loc.get("lng"),
        "placeId": place_id,
        "rating": place.get("rating"),
        "phone": phone,
        "employeesCount": None,
        "linkedinUrl": None,
        "country": country or "",
        "city": city,
        "state": state,
        "industry": place.get("types", [None])[0] if place.get("types") else None,
    }


def _contacts_from_emails(emails: list[str]) -> list[dict]:
    contacts: list[dict] = []
    for email in emails:
        first_name, last_name = _email_to_name(email)
        contacts.append(
            {
                "id": email,
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "linkedinUrl": None,
                "country": None,
                "city": None,
                "state": None,
                "jobTitle": None,
            }
        )
    return contacts


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/businesses")
def get_businesses(
    apiKey: str,
    keyword: str,
    lat: float,
    lon: float,
    radius: int = 1000,
    pageSize: int = 20,
    pageIndex: int = 0,
    pageToken: Optional[str] = None,
) -> dict:
    if not apiKey:
        raise HTTPException(status_code=400, detail="apiKey is required")
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")
    if pageIndex > 0 and not pageToken:
        raise HTTPException(
            status_code=400,
            detail="pageToken is required when pageIndex > 0. Use nextPageToken from the previous response.",
        )

    try:
        raw_results, next_page_token = fetch_places_page(
            api_key=apiKey,
            location=(lat, lon),
            radius=int(radius),
            keyword=keyword,
            page_token=pageToken,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if pageSize > 0:
        raw_results = raw_results[: int(pageSize)]

    items = [_business_item_from_place(place, apiKey) for place in raw_results]
    return {
        "items": items,
        "totalCount": len(items),
        "nextPageToken": next_page_token,
    }


@app.post("/businesses")
def post_businesses(request: BusinessesRequest) -> dict:
    return get_businesses(
        apiKey=request.apiKey,
        keyword=request.keyword,
        lat=request.lat,
        lon=request.lon,
        radius=request.radius,
        pageSize=request.pageSize,
        pageIndex=request.pageIndex,
        pageToken=request.pageToken,
    )


@app.get("/contacts")
def get_contacts(
    domain: Optional[str] = None,
    placeId: Optional[str] = None,
    address: Optional[str] = None,
    apiKey: Optional[str] = None,
) -> list[dict]:
    website = None
    if placeId:
        if not apiKey:
            raise HTTPException(status_code=400, detail="apiKey is required when placeId is provided")
        _, website = get_place_details(placeId, apiKey)

    if not website and domain:
        website = domain.strip()
        if not website.startswith(("http://", "https://")):
            website = f"https://{website}"

    if not website and address:
        raise HTTPException(
            status_code=400,
            detail="address alone is not supported yet; provide domain or placeId",
        )

    if not website:
        raise HTTPException(status_code=400, detail="Provide domain or placeId to fetch contacts")

    emails_str = extract_email_from_website(website)
    emails = [e.strip() for e in (emails_str or "").split(",") if e.strip()]
    return _contacts_from_emails(emails)


@app.post("/contacts")
def post_contacts(request: ContactsRequest) -> list[dict]:
    return get_contacts(
        domain=request.domain,
        placeId=request.placeId,
        address=request.address,
        apiKey=request.apiKey,
    )


@app.post("/run")
def run(request: RunRequest) -> dict:
    if not request.google_api_key:
        raise HTTPException(status_code=400, detail="google_api_key is required")
    if not request.industry:
        raise HTTPException(status_code=400, detail="industry is required")

    lat, lon = None, None
    if request.location.maps_url:
        lat, lon = extract_lat_lon_from_url(request.location.maps_url)
    elif request.location.address:
        lat, lon = get_lat_lon_from_address(request.location.address)
    elif request.location.latitude is not None and request.location.longitude is not None:
        lat, lon = request.location.latitude, request.location.longitude

    if lat is None or lon is None:
        raise HTTPException(status_code=400, detail="Invalid location input (maps_url, address, or lat/lon)")

    try:
        raw_results = fetch_places(request.google_api_key, (lat, lon), int(request.radius_m), request.industry)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not raw_results:
        return {"businesses": [], "phantom": None, "apify": None}

    df = process_basic_results(raw_results)
    if request.enrich:
        df = enrich_data(df, request.google_api_key, enable_email=True)

    response: dict = {
        "businesses": _df_to_records(df),
        "phantom": None,
        "apify": None,
    }

    phantom_cfg = request.phantom or PhantomConfig(enabled=False)
    linkedin_company_urls: list[str] = []
    if phantom_cfg.enabled:
        if not phantom_cfg.api_key:
            raise HTTPException(status_code=400, detail="phantom.api_key is required when phantom.enabled is true")

        pb = PhantomBusterClient(api_key=phantom_cfg.api_key, org_id=None)

        website_str = df["Website"].fillna("").astype(str).str.strip()
        df_with_website = df[
            (website_str != "") & (~website_str.str.lower().isin({"pending...", "none", "nan"}))
        ].copy()

        if df_with_website.empty:
            raise HTTPException(
                status_code=400,
                detail="No companies with a Website found; PhantomBuster will not run.",
            )

        data = _df_to_csv_bytes(df_with_website)
        spreadsheet_url = pb.upload_bytes_to_tmpfiles("enriched_results_with_website.csv", data)

        argument = _build_phantom_argument(
            spreadsheet_url=spreadsheet_url,
            csv_name="result",
            market=phantom_cfg.market,
            number_of_lines_to_process=int(phantom_cfg.number_of_lines_to_process),
        )

        launch_resp = pb.launch_agent(agent_id=phantom_cfg.agent_id, argument=argument)
        container_id = str(launch_resp.get("containerId") or "")
        if not container_id:
            raise HTTPException(status_code=502, detail=f"Phantom launch missing containerId: {launch_resp}")

        final_container = pb.wait_for_container(container_id, poll_s=5, timeout_s=15 * 60)

        agent_meta = pb.fetch_agent(phantom_cfg.agent_id)
        org_s3 = agent_meta.get("orgS3Folder")
        s3 = agent_meta.get("s3Folder")
        if not org_s3 or not s3:
            raise HTTPException(status_code=502, detail="Phantom agent metadata missing orgS3Folder/s3Folder")

        result_url = pb.build_result_download_url(
            org_s3_folder=str(org_s3),
            s3_folder=str(s3),
            filename="result",
            ext="csv",
        )
        result_bytes = pb.download_bytes(result_url)

        df_pb = _read_pb_results_bytes_as_df(result_bytes, "csv")
        if df_pb is not None:
            df_pb_filtered = _filter_pb_results_to_current_run(df_pb, df_with_website)
            linkedin_company_urls = _extract_linkedin_company_urls(df_pb_filtered)
            phantom_payload = {
                "container": final_container,
                "result_url": result_url,
                "results_filtered": _df_to_records(df_pb_filtered),
                "results_raw_count": int(len(df_pb)),
                "results_filtered_count": int(len(df_pb_filtered)),
                "linkedin_company_urls": linkedin_company_urls,
            }
        else:
            txt = result_bytes.decode("utf-8", errors="replace")
            linkedin_company_urls = _extract_linkedin_company_urls(txt)
            phantom_payload = {
                "container": final_container,
                "result_url": result_url,
                "results_filtered": [],
                "results_raw_count": 0,
                "results_filtered_count": 0,
                "linkedin_company_urls": linkedin_company_urls,
            }

        response["phantom"] = phantom_payload

    if request.linkedin_company_urls:
        linkedin_company_urls.extend(request.linkedin_company_urls)

    apify_cfg = request.apify or ApifyConfig(enabled=False)
    if apify_cfg.enabled:
        token = (os.getenv("APIFY_TOKEN") or apify_cfg.token or "").strip()
        companies = [c for c in linkedin_company_urls if _is_linkedin_company_url(c)]
        companies = list(dict.fromkeys(companies))
        if not companies:
            raise HTTPException(status_code=400, detail="No LinkedIn company URLs available for Apify.")

        try:
            items, run_meta = _run_apify_people_scraper(
                token=token,
                actor_id=apify_cfg.actor_id,
                companies=companies,
                max_items=int(apify_cfg.max_items),
                profile_scraper_mode=apify_cfg.profile_scraper_mode,
                company_batch_mode=apify_cfg.company_batch_mode,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        response["apify"] = {
            "run": run_meta,
            "people": items,
            "people_count": len(items),
        }

    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("fastapi_app:app", host="0.0.0.0", port=8000, reload=True)
