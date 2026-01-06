# Business Extractor (Google Maps → PhantomBuster → Apify)

A Streamlit app (`test.py`) that helps generate lead lists by:

- **Step 1**: finding businesses near a location via **Google Places Nearby Search**
- **Step 1b**: enriching each business with **phone + website** (Places Details) and best-effort **email extraction** (website scraping)
- **Optional Step 2**: sending the enriched CSV (filtered to rows with a Website) to **PhantomBuster**
- **Optional Step 3**: taking LinkedIn **company URLs** from Phantom results and running an **Apify Actor** to fetch people, then exporting a people CSV

## Current features (as implemented in `test.py`)

- **Google Places search**
  - Location input: **Google Maps URL** (extracts `@lat,lon`) or **manual coordinates**
  - Keyword/industry search
  - Radius: **100m → 50km**
  - Pagination support (collects multiple pages)
- **Enrichment**
  - Phone + Website via **Place Details**
  - Email extraction by scanning website HTML with a simple regex
  - Live progress updates in the UI; table refreshes periodically to reduce flicker
- **UI**
  - Map view via **PyDeck** + data table
  - CSV export of the enriched businesses
- **PhantomBuster (optional)**
  - Uploads the “companies with website” CSV to `tmpfiles.org`
  - Launches a PhantomBuster agent (Agent ID is hard-coded in the app)
  - Downloads `result.csv` from PhantomBuster S3 and extracts LinkedIn company URLs
- **Apify (optional)**
  - Runs an Apify Actor (default Actor ID in the UI) using the extracted LinkedIn company URLs
  - Shows results in a table and exports a CSV

## Requirements

- **Python 3.9+** recommended
- A **Google Maps API Key** with:
  - **Places API** enabled (Nearby Search + Place Details)
- Optional integrations:
  - **PhantomBuster API Key** (if using Step 2)
  - **Apify token** (if using Step 3)

## Installation

From this folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run test.py
```

Open the app (Streamlit will print the local URL, usually `http://localhost:8501`).

## How to use (workflow)

### Step 1: Google Maps businesses + enrichment

1. Enter **Google Maps API Key**
2. Pick **Industry / Keyword**
3. Pick location input method:
   - **Google Maps URL**: paste a URL containing `@lat,lon` (short URLs are expanded)
   - **Enter Coordinates**: type latitude/longitude
4. Set **Search Radius**
5. Click **Find Businesses**

The app will:

- fetch businesses (Nearby Search)
- enrich each row (Place Details + optional email scraping)
- allow **Export to CSV**

### Optional Step 2: PhantomBuster

1. In the sidebar, enable **PhantomBuster**
2. Enter your **PhantomBuster API Key**
3. Click **Launch Phantom**

Notes:

- The app sends only companies where `Website` is present (not empty / not “Pending…”).
- The app expects Phantom results to be available as **`result.csv`** in the agent’s S3 folder, then filters those results back to the current run and extracts **LinkedIn company URLs**.

### Optional Step 3: Apify (people from LinkedIn company URLs)

1. In the sidebar, enable **Apify**
2. Provide an Apify token (recommended via env var `APIFY_TOKEN`, UI field is fallback)
3. Click **Run Apify (People)**

Output:

- a table of people results
- a downloadable **people CSV**

## Configuration / environment variables

- **`APIFY_TOKEN`**: recommended way to provide the Apify token for Step 3.

## Data + compliance notes

- **Google Maps Platform ToS**: ensure your usage (storage/caching/redistribution) complies with Google’s terms.
- **Scraping**: email extraction is best-effort HTML scanning and may violate site policies; respect `robots.txt` / local laws and do not use for spam.
- **Secrets**: do not commit API keys. Prefer environment variables where possible.

## Project files

- `test.py`: the Streamlit app (Step 1 + optional Step 2/3)
- `requirements.txt`: dependencies
