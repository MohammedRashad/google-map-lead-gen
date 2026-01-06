import os
import time
import json
import argparse
from pathlib import Path
from typing import Any, Dict, Optional

import requests


class PhantomBusterClient:
    """
    Minimal PhantomBuster API v2 client:
      - Launch agent (/agents/launch)
      - Poll container status (/containers/fetch)
      - Fetch run logs (/containers/fetch-output) or latest agent logs (/agents/fetch-output)
      - Fetch agent metadata (/agents/fetch) to build result file download URLs (S3)
    """

    def __init__(
        self,
        api_key: str,
        org_id: Optional[str] = None,
        base_url: str = "https://api.phantombuster.com/api/v2",
        timeout_s: int = 30,
        spreadsheet_csv_path: Optional[str] = None,
        tmpfiles_upload_url: str = "https://tmpfiles.org/api/v1/upload",
    ):
        self.api_key = api_key
        self.org_id = org_id
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.spreadsheet_csv_path = spreadsheet_csv_path
        self.tmpfiles_upload_url = tmpfiles_upload_url

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Phantombuster-Key": self.api_key,
                "Content-Type": "application/json",
            }
        )
        # Optional, only if you manage multiple orgs/workspaces:
        if self.org_id:
            self.session.headers["X-Phantombuster-Org"] = self.org_id

    @staticmethod
    def _normalize_tmpfiles_download_url(url: str) -> str:
        """
        tmpfiles.org often returns a "page" URL like:
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
        rest = url[idx + len(marker) :]
        # Avoid double slashes if rest starts with "/"
        rest = rest.lstrip("/")
        return f"{prefix}dl/{rest}"

    def upload_file_to_tmpfiles(self, file_path: str) -> str:
        """
        Uploads a file to tmpfiles.org and returns a URL suitable for downloading the file.

        API:
          POST https://tmpfiles.org/api/v1/upload
          multipart form: file=@/path/to/file
        Response JSON typically includes: {"data": {"url": "..."}}
        """
        p = Path(file_path).expanduser()
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        with p.open("rb") as f:
            r = requests.post(self.tmpfiles_upload_url, files={"file": f}, timeout=self.timeout_s)
        r.raise_for_status()

        try:
            payload = r.json()
        except Exception as e:
            raise RuntimeError(f"tmpfiles upload returned non-JSON response: {r.text[:500]}") from e

        url = (((payload or {}).get("data") or {}).get("url") or "").strip()
        if not url:
            raise RuntimeError(f"tmpfiles upload response missing data.url: {payload}")
        return self._normalize_tmpfiles_download_url(url)

    def resolve_spreadsheet_url(self, fallback_spreadsheet_url: Optional[str]) -> Optional[str]:
        """
        If spreadsheet_csv_path was provided, uploads it to tmpfiles and returns the resulting URL.
        Otherwise returns fallback_spreadsheet_url.
        """
        if self.spreadsheet_csv_path:
            return self.upload_file_to_tmpfiles(self.spreadsheet_csv_path)
        return fallback_spreadsheet_url

    def launch_agent(
        self,
        agent_id: str,
        argument: Optional[Dict[str, Any]] = None,
        bonus_argument: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Launch an agent. Response includes containerId (run id) when accepted.
        """
        url = f"{self.base_url}/agents/launch"
        payload: Dict[str, Any] = {"id": agent_id}

        # "argument" = the full phantom setup JSON (typical)
        if argument is not None:
            payload["argument"] = argument

        # "bonusArgument" = optional per-run overrides (dynamic input)
        if bonus_argument is not None:
            payload["bonusArgument"] = bonus_argument

        r = self.session.post(url, data=json.dumps(payload), timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def fetch_container(self, container_id: str) -> Dict[str, Any]:
        """
        Get container (launch) details by id.
        """
        url = f"{self.base_url}/containers/fetch"
        r = self.session.get(url, params={"id": container_id}, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def fetch_container_output(self, container_id: str) -> Dict[str, Any]:
        """
        Get container output/logs for a specific run.
        """
        url = f"{self.base_url}/containers/fetch-output"
        r = self.session.get(url, params={"id": container_id}, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def fetch_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Fetch agent metadata. Useful to get s3Folder/orgS3Folder for download links.
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
        ext = ext.lstrip(".")
        return f"https://phantombuster.s3.amazonaws.com/{org_s3_folder}/{s3_folder}/{filename}.{ext}"

    def wait_for_container(
        self,
        container_id: str,
        poll_s: float = 5.0,
        timeout_s: float = 10 * 60,
    ) -> Dict[str, Any]:
        """
        Polls container until it looks finished.
        Container schemas can vary a bit, so we check multiple common fields.
        """
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Launch a PhantomBuster agent with optional CSV upload via tmpfiles.org.")
    parser.add_argument("--agent-id", default=os.environ.get("PHANTOMBUSTER_AGENT_ID"), help="PhantomBuster agent id")
    parser.add_argument("--org-id", default=os.environ.get("PHANTOMBUSTER_ORG_ID"), help="Optional PhantomBuster org id")
    parser.add_argument("--api-key", default=os.environ.get("PHANTOMBUSTER_API_KEY"), help="PhantomBuster API key")
    parser.add_argument("--csv-name", default="result", help="Output csvName used by the Phantom")
    parser.add_argument("--market", default="en-US", help="Market used by the Phantom")
    parser.add_argument(
        "--spreadsheet-url",
        default=None,
        help='Spreadsheet URL (if you are not uploading a local CSV). If --csv-file is provided, this is ignored.',
    )
    parser.add_argument(
        "--csv-file",
        default=None,
        help="Local CSV path to upload to tmpfiles.org; uploaded URL will be used as spreadsheetUrl.",
    )
    parser.add_argument("--number-of-lines-to-process", type=int, default=4, help="numberOfLinesToProcess passed to Phantom")
    args = parser.parse_args()

    if not args.api_key:
        raise RuntimeError("Missing API key. Set PHANTOMBUSTER_API_KEY or pass --api-key.")
    if not args.agent_id:
        raise RuntimeError("Missing agent id. Set PHANTOMBUSTER_AGENT_ID or pass --agent-id.")

    pb = PhantomBusterClient(
        api_key=args.api_key,
        org_id=args.org_id,
        spreadsheet_csv_path=args.csv_file,
    )

    spreadsheet_url = pb.resolve_spreadsheet_url(args.spreadsheet_url)
    if not spreadsheet_url:
        raise RuntimeError("Missing spreadsheetUrl. Provide --spreadsheet-url or --csv-file.")

    # Your provided input JSON (must match your Phantom's expected field names)
    argument = {
        "csvName": "result",
        "market": "en-US",
        "spreadsheetUrl": spreadsheet_url,
        "numberOfLinesToProcess": args.number_of_lines_to_process,
    }
    # Let CLI override defaults if desired
    argument["csvName"] = args.csv_name
    argument["market"] = args.market

    # Launch
    launch_resp = pb.launch_agent(agent_id=args.agent_id, argument=argument)
    container_id = str(launch_resp.get("containerId") or "")
    if not container_id:
        raise RuntimeError(f"Launch response did not include containerId: {launch_resp}")

    print("Launched. containerId =", container_id)

    # Wait for completion
    final_container = pb.wait_for_container(container_id, poll_s=5, timeout_s=15 * 60)
    print("Container status snapshot:", final_container)

    # Fetch logs/output for that run
    run_output = pb.fetch_container_output(container_id)
    print("Run output keys:", list(run_output.keys()))

    # Optional: build a direct download URL for a result file (csv/json)
    # Fetch agent metadata to get s3Folder/orgS3Folder. :contentReference[oaicite:3]{index=3}
    agent = pb.fetch_agent(args.agent_id)
    org_s3 = agent.get("orgS3Folder")
    s3 = agent.get("s3Folder")
    if org_s3 and s3:
        # Often the output file name is whatever you configured in the Phantom UI.
        # If your result file name is "result" then:
        print("CSV download URL:", pb.build_result_download_url(org_s3, s3, filename="result", ext="csv"))
        print("JSON download URL:", pb.build_result_download_url(org_s3, s3, filename="result", ext="json"))
