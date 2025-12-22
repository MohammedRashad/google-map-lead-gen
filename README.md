# Google Maps Business Extractor

A Streamlit application that extracts business information (leads) from Google Maps based on industry and location. It fetches basic information using the Google Places API and attempts to enrich the data by finding phone numbers and scraping email addresses from business websites.

## Features

- **Search by Location**: Use a Google Maps URL or enter specific coordinates (Latitude/Longitude).
- **Keyword Search**: Filter businesses by industry or keyword (e.g., "Coffee Shop", "Gym", "Dentist").
- **Radius Control**: Adjustable search radius (from 100m to 50km).
- **Data Enrichment**:
  - Fetches formatted phone numbers and websites via Google Places Details API.
  - Scrapes business websites to find contact email addresses.
- **Visualizations**: Interactive map view using PyDeck and a sortable data table.
- **Export**: Download the enriched dataset as a CSV file.

## Prerequisites

- **Python 3.8+**
- **Google Maps API Key**: You need a valid API Key with the following APIs enabled:
  - **Places API (New)** or **Places API** (for Nearby Search and Place Details)

## Installation

1. Clone the repository or navigate to the project directory:
   ```bash
   cd maps
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *If `requirements.txt` is not available, install manually:*
   ```bash
   pip install streamlit pandas requests pydeck geopy
   ```

## Usage

1. Run the Streamlit app:
   ```bash
   streamlit run test.py
   ```

2. The application will open in your default web browser (usually at `http://localhost:8501`).

3. **Enter your Google Maps API Key** in the sidebar.

4. **Configure Search**:
   - Enter an Industry/Keyword.
   - Choose a Location Method (Paste a Google Maps URL or enter Lat/Lon manually).
   - Adjust the Search Radius.

5. Click **Find Businesses**.

6. Wait for the process to complete:
   - The app will first fetch the list of businesses.
   - It will then proceed to "enrich" the data by fetching phone numbers and scraping emails.
   - Progress is displayed in real-time.

7. **Export Data**: Once complete, click the "Export to CSV" button to download your leads.

## Disclaimer

This tool is for educational and legitimate business intelligence purposes.
- **Google Maps Platform Terms**: Ensure you comply with Google Maps Platform Terms of Service regarding caching and data usage.
- **Web Scraping**: The email extraction feature involves web scraping. Respect `robots.txt` policies and do not use this tool for spamming or unauthorized data collection.

## File Structure

- `test.py`: Main application script.
- `requirements.txt`: Python dependencies.

