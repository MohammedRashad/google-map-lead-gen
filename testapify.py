from apify_client import ApifyClient
import os

# Initialize the ApifyClient with your API token
token = os.getenv("APIFY_TOKEN")
if not token:
    raise RuntimeError("Missing APIFY_TOKEN environment variable.")
client = ApifyClient(token)

# Prepare the Actor input
run_input = {
    "profileScraperMode": "Full ($8 per 1k)",
    "maxItems": 25,
    "companies": ["https://linkedin.com/company/hyperion-ai"],
    # Optional filters: when not used, omit them or use empty arrays (never None)
    "locations": [],
    # "searchQuery": "…",
    # "jobTitles": ["…"],
    # "pastJobTitles": ["…"],
    # "industryIds": [123],
    # "yearsAtCurrentCompanyIds": [1, 2],
    # "yearsOfExperienceIds": [3, 4],
    # "seniorityLevelIds": [5],
    # "functionIds": [6],
    # "recentlyChangedJobs": False,
    "companyBatchMode": "all_at_once",
}

# Drop any None values defensively (keeps Actor input schema happy)
run_input = {k: v for k, v in run_input.items() if v is not None}

# Run the Actor and wait for it to finish
run = client.actor("Vb6LZkh4EqRlR0Ka9").call(run_input=run_input)

# Fetch and print Actor results from the run's dataset (if there are any)
for item in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(item)