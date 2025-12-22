import streamlit as st
import pandas as pd
import requests
import time
import re
import pydeck as pdk
from geopy.geocoders import Nominatim

# --- CONFIGURATION ---
st.set_page_config(page_title="BizFinder Automation", layout="wide")

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

# --- UI LAYOUT ---

st.title("🗺️ Google Maps Business Extractor")
st.markdown("Generate leads by extracting businesses from Google Maps based on industry and location.")

# Sidebar for Inputs
with st.sidebar:
    st.header("Search Parameters")
    
    api_key = st.text_input("Google Maps API Key", type="password", help="Enable 'Places API' in Google Console")
    
    industry = st.text_input("Industry / Keyword", value="Coffee Shop", placeholder="e.g. Gym, Dentist, Restaurant")
    
    search_method = st.radio("Location Input Method", ["Enter Address", "Google Maps URL", "Enter Coordinates"])
    
    lat, lon = None, None
    
    if search_method == "Enter Address":
        address = st.text_input("Location Address", value="Central Park, New York")
        if address:
            lat, lon = get_lat_lon_from_address(address)
            if lat:
                st.success(f"Found: {lat:.4f}, {lon:.4f}")
            else:
                st.warning("Address not found.")
    elif search_method == "Google Maps URL":
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

# --- MAIN LOGIC ---

if search_btn:
    if not api_key:
        st.error("Please enter a Google Maps API Key.")
    elif not lat or not lon:
        st.error("Invalid Location.")
    else:
        with st.spinner(f"Searching for '{industry}' within {radius}m..."):
            raw_results = fetch_places(api_key, (lat, lon), radius, industry)
            
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