import streamlit as st
import pandas as pd
import requests
import time
import re
import os
import pydeck as pdk
from geopy.geocoders import Nominatim
import json
from typing import Any, Dict, Optional, Tuple
from io import BytesIO
from urllib.parse import urlparse

try:
    from apify_client import ApifyClient  # type: ignore
except Exception:
    ApifyClient = None  # type: ignore

# --- CONFIGURATION ---
st.set_page_config(page_title="BizFinder Automation", layout="wide")

# PhantomBuster Agent ID (constant / not editable)
PHANTOMBUSTER_AGENT_ID = "5654810775307548"

# --- HELPER FUNCTIONS ---

def extract_lat_lon_from_url(url):
    """
    Extracts latitude and longitude from a Google Maps URL.
    Handles standard URLs and shortened URLs (by expanding them).
    """
    try:
        # Handle shortened URLs (e.g. https://maps.app.goo.gl/...)
        if "google.com/maps" not in url:
            # We use a session to handle potential cookie/redirect chains better, 
            # though basic requests.get usually works for expansion.
            response = requests.get(url, allow_redirects=True, timeout=10)
            url = response.url
        
        # Look for the @lat,lon pattern in the URL
        # e.g. https://www.google.com/maps/place/.../@40.748,-73.985,17z
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        
        return None, None
    except Exception as e:
        st.error(f"Error parsing URL: {e}")
        return None, None

def get_lat_lon_from_address(address):
    """
    Converts a text address (e.g., 'New York, NY') to coordinates using 
    OpenStreetMap (Nominatim) to save on Google Geocoding API costs.
    """
    geolocator = Nominatim(user_agent="streamlit_biz_finder")
    try:
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception as e:
        st.error(f"Error geocoding address: {e}")
        return None, None

def get_place_details(place_id, api_key):
    """
    Fetches additional details (phone, website) for a specific place.
    """
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "formatted_phone_number,website",
        "key": api_key
    }
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            result = response.json().get("result", {})
            return result.get("formatted_phone_number"), result.get("website")
    except:
        pass
    return None, None

def extract_email_from_website(website_url):
    """
    Scrapes the website for email addresses.
    """
    if not website_url:
        return None
    try:
        # standard headers to avoid being blocked by some sites
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(website_url, timeout=10, headers=headers)
        if response.status_code == 200:
            # Simple regex for email extraction
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = re.findall(email_pattern, response.text)
            
            # Filter and deduplicate
            unique_emails = set()
            for email in emails:
                # Filter out likely false positives (images, files)
                if not any(email.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js', '.woff', '.ttf']):
                     unique_emails.add(email)
            
            return ", ".join(list(unique_emails)[:3]) if unique_emails else None
    except:
        pass
    return None

def fetch_places(api_key, location, radius, keyword):
    """
    Fetches places from Google Maps Places API.
    Handles pagination to get more than just the first 20 results.
    """
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    results = []
    
    params = {
        "location": f"{location[0]},{location[1]}",
        "radius": radius,
        "keyword": keyword,
        "key": api_key
    }

    while True:
        response = requests.get(url, params=params)
        if response.status_code != 200:
            st.error("API Error: " + response.text)
            break
            
        data = response.json()
        #st.write(data)
        results.extend(data.get("results", []))
        
        # Check if there is a next page
        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break
            
        # Google requires a short delay before the next_page_token becomes valid
        time.sleep(2)
        params["pagetoken"] = next_page_token
        # Remove keyword/radius/location from params when using pagetoken 
        # (though some versions of the API tolerate it, it's cleaner to reset)
    
    return results

def process_basic_results(results):
    """
    Creates a basic DataFrame from initial search results.
    Doesn't perform detailed API calls or scraping yet.
    """
    cleaned_data = []
    for place in results:
        loc = place.get("geometry", {}).get("location", {})
        cleaned_data.append({
            "Name": place.get("name"),
            "Rating": place.get("rating", "N/A"),
            "Address": place.get("vicinity"),
            "lat": loc.get("lat"),
            "lon": loc.get("lng"),
            "Place ID": place.get("place_id"),
            "Phone": "Pending...",
            "Website": "Pending...",
            "Email": "Pending..."
        })
    return pd.DataFrame(cleaned_data)

def enrich_data(df, api_key, status_placeholder, table_placeholder):
    """
    Iterates through the DataFrame and fetches details for each row.
    Updates the UI progressively.
    """
    total = len(df)
    progress_bar = st.progress(0)
    
    for index, row in df.iterrows():
        place_id = row["Place ID"]
        
        status_placeholder.text(f"Fetching details for: {row['Name']} ({index + 1}/{total})")
        
        # Fetch details (Phone, Website)
        phone, website = get_place_details(place_id, api_key)
        
        # Extract Email if website exists
        email = extract_email_from_website(website)
        
        # Update DataFrame in memory
        df.at[index, "Phone"] = phone
        df.at[index, "Website"] = website
        df.at[index, "Email"] = email
        
        # Update progress
        progress_bar.progress((index + 1) / total)
        
        # Update the table in the UI every 5 rows or on the last one to reduce flicker/lag
        if (index + 1) % 5 == 0 or (index + 1) == total:
             table_placeholder.dataframe(df, use_container_width=True)
             
    progress_bar.empty()
    status_placeholder.text("Data enrichment complete!")
    return df

class PhantomBusterClient:
    """
    Minimal PhantomBuster API v2 client + tmpfiles.org uploader.
      - Launch agent (/agents/launch)
      - Poll container status (/containers/fetch)
      - Fetch run logs (/containers/fetch-output)
    """

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
        """
        tmpfiles.org may return a "page" URL like:
          https://tmpfiles.org/123456
        The direct download form is commonly:
          https://tmpfiles.org/dl/123456
        """
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
        """
        Upload bytes to tmpfiles.org and return a URL suitable for downloading the file.

        API:
          POST https://tmpfiles.org/api/v1/upload
          multipart form: file=@/path/to/file
        Response JSON typically includes: {"data": {"url": "..."}}
        """
        if not data:
            raise ValueError("No data to upload")
        files = {"file": (filename, data)}
        r = requests.post(self.tmpfiles_upload_url, files=files, timeout=self.timeout_s)
        r.raise_for_status()

        try:
            payload = r.json()
        except Exception as e:
            raise RuntimeError(f"tmpfiles upload returned non-JSON response: {r.text[:500]}") from e

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
        """
        Fetch agent metadata (orgS3Folder/s3Folder) to build result download URLs.
        """
        url = f"{self.base_url}/agents/fetch"
        r = self.session.get(url, params={"id": agent_id}, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def build_result_download_url(org_s3_folder: str, s3_folder: str, filename: str, ext: str) -> str:
        """
        PhantomBuster results are downloadable from:
          https://phantombuster.s3.amazonaws.com/{orgS3Folder}/{s3Folder}/{NAME}.{json|csv}
        """
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
    """
    Best-effort extraction of LinkedIn company URLs from a CSV/JSON-like object.
    - If obj is a DataFrame, scans string columns.
    - If obj is dict/list, traverses recursively.
    """
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
    """
    Normalize a website/url/domain-like string to a bare domain (lowercase, no www).
    Returns "" if not parseable.
    """
    s = (value or "").strip().lower()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        host = urlparse(s).netloc
    else:
        # allow bare domains or www.* or domain/path
        host = s.split("/")[0]
    host = host.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    # strip port
    host = host.split(":")[0]
    # crude validity
    if "." not in host:
        return ""
    return host

def _filter_pb_results_to_current_run(df_pb: pd.DataFrame, df_run_companies: pd.DataFrame) -> pd.DataFrame:
    """
    Filter Phantom results to only rows relevant to companies we sent in the current run.
    Uses domains (preferred) and company names as a fallback.
    """
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

    # Prefer matching on likely domain/url columns if present.
    urlish_cols = [c for c in df_pb.columns if any(k in c.lower() for k in ["website", "url", "domain", "site"])]
    nameish_cols = [c for c in df_pb.columns if any(k in c.lower() for k in ["company", "name", "organization", "org"])]

    mask = pd.Series(False, index=df_pb.index)

    # Domain match
    if run_domains:
        if urlish_cols:
            for col in urlish_cols:
                col_domains = df_str[col].map(_normalize_domain)
                mask = mask | col_domains.isin(run_domains)
        else:
            # Fallback: scan all columns for a domain substring (chunked to avoid giant regex)
            haystack = df_str.apply(lambda r: " | ".join(r.values.astype(str)).lower(), axis=1)
            chunk_size = 150
            for i in range(0, len(run_domains), chunk_size):
                chunk = run_domains[i : i + chunk_size]
                rx = "(" + "|".join(re.escape(d) for d in chunk) + ")"
                mask = mask | haystack.str.contains(rx, regex=True)

    # Name match (fallback / additive)
    if run_names:
        cols = nameish_cols or list(df_pb.columns)
        # Keep only a few columns to reduce false positives
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

# --- UI LAYOUT ---

st.title("🗺️ Business Extractor")
st.markdown("Generate leads by extracting businesses from Google Maps based on industry and location.")

# Sidebar for Inputs
with st.sidebar:
    st.header("Search Parameters")
    
    api_key = st.text_input("Google Maps API Key", type="password", help="Enable 'Places API' in Google Console")
    
    industry = st.text_input("Industry / Keyword", value="Coffee Shop", placeholder="e.g. Gym, Dentist, Restaurant")
    
    search_method = st.radio("Location Input Method", [ "Google Maps URL", "Enter Coordinates"])
    
    lat, lon = None, None
    

    if search_method == "Google Maps URL":
        url = st.text_input("Paste Google Maps Link", placeholder="https://maps.google.com/...")
        if url:
            with st.spinner("Parsing URL..."):
                lat, lon = extract_lat_lon_from_url(url)
            if lat:
                st.success(f"Extracted: {lat:.4f}, {lon:.4f}")
            else:
                st.error("Could not extract coordinates from this URL.")
    else:
        col1, col2 = st.columns(2)
        lat = col1.number_input("Latitude", value=40.785091, format="%.6f")
        lon = col2.number_input("Longitude", value=-73.968285, format="%.6f")

    radius = st.slider("Search Radius (meters)", min_value=100, max_value=50000, value=1000, step=100)
    
    search_btn = st.button("Find Businesses", type="primary")

# --- PHANTOMBUSTER UI ---
with st.sidebar:
    st.write("---")
    st.header("PhantomBuster (Optional)")

    pb_enabled = st.checkbox("Enable PhantomBuster", value=False)
    pb_api_key = st.text_input("PhantomBuster API Key", type="password", disabled=not pb_enabled)
    pb_agent_id = PHANTOMBUSTER_AGENT_ID
    st.text_input("PhantomBuster Agent ID", value=PHANTOMBUSTER_AGENT_ID, disabled=True)

    pb_market = st.text_input("Phantom market", value="en-US", disabled=not pb_enabled)
    pb_number_lines = st.number_input("numberOfLinesToProcess", min_value=1, max_value=100000, value=4, step=1, disabled=not pb_enabled)
    st.caption("Input source: enriched CSV from Step 1 (filtered to rows with a Website).")
    st.caption("Defaults: csvName=result, results file=result.csv")

    pb_launch_btn = st.button("Launch Phantom", type="secondary", disabled=not pb_enabled)

# --- APIFY UI (STEP 3) ---
with st.sidebar:
    st.write("---")
    st.header("Apify (Step 3: People from LinkedIn company URLs)")

    apify_enabled = st.checkbox("Enable Apify", value=False)
    apify_token = st.text_input(
        "APIFY_TOKEN (fallback)",
        type="password",
        disabled=not apify_enabled,
        help="Prefer setting APIFY_TOKEN as an environment variable; this field is a fallback.",
    )
    apify_actor_id = st.text_input("Apify Actor ID", value="Vb6LZkh4EqRlR0Ka9", disabled=not apify_enabled)
    apify_max_items = st.number_input("Max people to fetch", min_value=1, max_value=5000, value=25, step=1, disabled=not apify_enabled)
    apify_profile_mode = st.selectbox("Profile scraper mode", options=["Full ($8 per 1k)", "Basic ($3 per 1k)"], index=0, disabled=not apify_enabled)
    apify_run_btn = st.button("Run Apify (People)", type="primary", disabled=not apify_enabled)

# --- MAIN LOGIC ---

if search_btn:
    if not api_key:
        st.error("Please enter a Google Maps API Key.")
    elif not lat or not lon:
        st.error("Invalid Location.")
    else:
        with st.spinner(f"Searching for '{industry}' within {radius}m..."):
            raw_results = fetch_places(api_key, (lat, lon), radius, industry)
            #st.write(raw_results)
            if raw_results:
                # 1. Show Basic Results First
                st.info(f"Found {len(raw_results)} businesses. Displaying basic info...")
                df = process_basic_results(raw_results)
                
                # Metric Summary
                st.metric("Businesses Found", len(df))
                
                # Visuals Setup
                tab1, tab2 = st.tabs(["📍 Map View", "📄 Data Table"])
                
                with tab1:
                    # Map is based on lat/lon which we already have, so we can show it immediately
                    st.pydeck_chart(pdk.Deck(
                        map_style='mapbox://styles/mapbox/light-v9',
                        initial_view_state=pdk.ViewState(
                            latitude=lat,
                            longitude=lon,
                            zoom=13,
                            pitch=0,
                        ),
                        layers=[
                            pdk.Layer(
                                'ScatterplotLayer',
                                data=df,
                                get_position='[lon, lat]',
                                get_color='[200, 30, 0, 160]',
                                get_radius=100,
                                pickable=True,
                            ),
                        ],
                        tooltip={
                            "html": "<b>{Name}</b><br/>{Address}<br/>Rating: {Rating}",
                            "style": {"backgroundColor": "steelblue", "color": "white"}
                        }
                    ))
                    
                with tab2:
                    # Create a placeholder for the dataframe
                    table_placeholder = st.empty()
                    table_placeholder.dataframe(df, use_container_width=True)
                
                # 2. Enrich Data (Phone, Email)
                st.write("---")
                status_placeholder = st.empty()
                
                # Button to start enrichment (optional, or auto-start)
                # Here we auto-start as requested "then do the number and email search"
                df_enriched = enrich_data(df, api_key, status_placeholder, table_placeholder)
                st.session_state["df_enriched"] = df_enriched
                
                # Export (using the final enriched dataframe)
                csv = df_enriched.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Export to CSV",
                    data=csv,
                    file_name=f"{industry}_results.csv",
                    mime="text/csv"
                )
            else:
                st.warning("No results found. Try increasing the radius or changing the keyword.")

# --- PHANTOMBUSTER ACTION ---
if pb_launch_btn:
    if not pb_api_key:
        st.error("Please enter your PhantomBuster API Key.")
    else:
        try:
            pb = PhantomBusterClient(api_key=pb_api_key, org_id=None)

            df_enriched = st.session_state.get("df_enriched")
            if df_enriched is None:
                st.error("No enriched results available yet. Run 'Find Businesses' first (and let enrichment finish).")
                st.stop()
            if "Website" not in df_enriched.columns:
                st.error("Enriched results are missing the 'Website' column; cannot filter for PhantomBuster.")
                st.stop()

            website_str = df_enriched["Website"].fillna("").astype(str).str.strip()
            df_with_website = df_enriched[
                (website_str != "")
                & (~website_str.str.lower().isin({"pending...", "none", "nan"}))
            ].copy()

            if df_with_website.empty:
                st.error("No companies with a Website found in Step 1 results. PhantomBuster will not run.")
                st.stop()

            st.info(
                f"PhantomBuster will run on {len(df_with_website)} / {len(df_enriched)} companies that have a Website."
            )
            st.session_state["df_enriched_for_phantom"] = df_with_website

            preview_cols = [c for c in ["Name", "Website", "Email", "Phone", "Address"] if c in df_with_website.columns]
            with st.expander("Preview: companies with website (sent to PhantomBuster)"):
                st.dataframe(df_with_website[preview_cols].head(50), use_container_width=True)

            data = _df_to_csv_bytes(df_with_website)
            spreadsheet_url = pb.upload_bytes_to_tmpfiles("enriched_results_with_website.csv", data)

            argument = _build_phantom_argument(
                spreadsheet_url=spreadsheet_url,
                csv_name="result",
                market=pb_market,
                number_of_lines_to_process=int(pb_number_lines),
            )

            st.info(f"Using spreadsheetUrl: {spreadsheet_url}")
            launch_resp = pb.launch_agent(agent_id=pb_agent_id, argument=argument)
            container_id = str(launch_resp.get("containerId") or "")
            if not container_id:
                st.error(f"Launch response did not include containerId: {launch_resp}")
                st.stop()

            st.success(f"Launched Phantom. containerId = {container_id}")

            with st.spinner("Waiting for Phantom run to finish..."):
                final_container = pb.wait_for_container(container_id, poll_s=5, timeout_s=15 * 60)

            st.subheader("Phantom run status")
            st.json(final_container)

            st.subheader("Phantom run output (fetch-output)")
            run_output = pb.fetch_container_output(container_id)
            st.json(run_output)

            # --- STEP 2 OUTPUT: Download Phantom results file + extract LinkedIn company URLs ---
            try:
                agent_meta = pb.fetch_agent(pb_agent_id)
                org_s3 = agent_meta.get("orgS3Folder")
                s3 = agent_meta.get("s3Folder")
                if org_s3 and s3:
                    result_url = pb.build_result_download_url(
                        org_s3_folder=str(org_s3),
                        s3_folder=str(s3),
                        filename="result",
                        ext="csv",
                    )
                    st.info(f"Downloading Phantom results: {result_url}")
                    result_bytes = pb.download_bytes(result_url)
                    st.session_state["pb_result_url"] = result_url
                    st.session_state["pb_result_bytes"] = result_bytes

                    df_pb = _read_pb_results_bytes_as_df(result_bytes, "csv")
                    if df_pb is not None:
                        df_run_companies = st.session_state.get("df_enriched_for_phantom")
                        df_pb_filtered = _filter_pb_results_to_current_run(df_pb, df_run_companies) if df_run_companies is not None else df_pb

                        st.subheader("Phantom results (current-run filtered)")
                        st.caption(f"Rows: {len(df_pb_filtered)} (filtered) / {len(df_pb)} (raw)")
                        st.dataframe(df_pb_filtered, use_container_width=True)

                        st.session_state["df_pb_results_raw"] = df_pb
                        st.session_state["df_pb_results"] = df_pb_filtered
                        linkedin_company_urls = _extract_linkedin_company_urls(df_pb_filtered)
                    else:
                        txt = result_bytes.decode("utf-8", errors="replace")
                        linkedin_company_urls = _extract_linkedin_company_urls(txt)

                    st.session_state["linkedin_company_urls"] = linkedin_company_urls
                    if linkedin_company_urls:
                        st.success(f"Extracted {len(linkedin_company_urls)} LinkedIn company URLs for Step 3.")
                        st.write(linkedin_company_urls[:25])
                    else:
                        st.warning(
                            "No LinkedIn company URLs found in Phantom results. Check the Phantom output fields/filename."
                        )
                else:
                    st.warning("Could not build Phantom result URL (missing orgS3Folder/s3Folder in agent metadata).")
            except Exception as e:
                st.warning(f"Could not download/parse Phantom results for Step 2: {e}")

        except Exception as e:
            st.exception(e)

# --- APIFY ACTION (STEP 3) ---
if apify_run_btn:
    try:
        token = (os.getenv("APIFY_TOKEN") or apify_token or "").strip()
        companies = st.session_state.get("linkedin_company_urls") or []
        companies = [c for c in companies if _is_linkedin_company_url(c)]
        companies = list(dict.fromkeys(companies))  # dedupe, keep order

        with st.spinner("Running Apify Actor to fetch people..."):
            items, run_meta = _run_apify_people_scraper(
                token=token,
                actor_id=(apify_actor_id or "").strip(),
                companies=companies,
                max_items=int(apify_max_items),
                profile_scraper_mode=apify_profile_mode,
            )

        st.subheader("Apify run metadata")
        st.json(run_meta)

        st.subheader("People results (Apify dataset)")
        df_people = pd.DataFrame(items)
        st.dataframe(df_people, use_container_width=True)
        st.session_state["df_people"] = df_people

        st.download_button(
            label="📥 Download People CSV",
            data=_df_to_csv_bytes(df_people),
            file_name="company_people.csv",
            mime="text/csv",
        )
    except Exception as e:
        st.exception(e)