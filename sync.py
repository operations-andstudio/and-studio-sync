"""
AND Studio Sync: Slack List item -> Float task + Dropbox folder.

Every item added to a client's Slack List after GO_LIVE, with a name and an Asset Type
and an empty "Dropbox Internal Folder", gets:
  1. a Float task with the same name, in "<Client> - <Month>", under the phase for its asset type
  2. a Dropbox folder with the same name, under the client's month and asset type folders
  3. the Dropbox folder link written back into "Dropbox Internal Folder"

The link column doubles as the "done" marker, so nothing is stored outside Slack and the
script can run from any machine. Running it twice does not duplicate anything.

    python sync.py            # dry run
    python sync.py --apply    # does it
"""
import argparse
import datetime as dt
import os
import re
import sys
from zoneinfo import ZoneInfo

import dropbox
import requests
from dropbox.common import PathRoot

NY = ZoneInfo("America/New_York")
FLOAT = "https://api.float.com/v3"
SLACK = "https://slack.com/api"

# Items created before GO_LIVE are ignored (they belong to the old process).
GO_LIVE = dt.datetime(2026, 10, 13, 14, 0, tzinfo=dt.timezone.utc)

# One entry per client list. Column and option ids come from each list's schema.
LISTS = {
    "F0BGJF9M0NT": {
        "client": "BODi",
        "dropbox_root": "/AND Clients [External]/BODi/2026",
        "cols": {
            "name": "Col0BGXRXTLEA",
            "asset_type": "Col0BH1PGRA57",
            "internal_folder": "Col0BGWH6TL69",
        },
        # select option id -> (label, Float phase, Dropbox folder)
        "asset_types": {
            "OptGENYZWS6": ("Video", "Video Assets", "Video Assets"),
            "OptKSLSKYOC": ("UGC", "Video Assets", "Video Assets"),
            "OptJCPD25BA": ("Static", "Static Assets", "Static Assets"),
            "OptBA1A580I": ("3D/CGI", "3D/CGI/AI Assets", "3D Assets"),
            "OptB622INAB": ("Motion Graphics", "Motion Graphics Assets", "Motion Graphic Assets"),
        },
    },
}


def need(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing secret/environment variable: {name}")
    return value


def slack(method, **payload):
    r = requests.post(f"{SLACK}/{method}", json=payload, timeout=30, headers={
        "Authorization": f"Bearer {need('SLACK_BOT_TOKEN')}", "Content-Type": "application/json; charset=utf-8"})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method}: {data.get('error')}")
    return data


def float_headers():
    return {"Authorization": f"Bearer {need('FLOAT_API_TOKEN')}",
            "User-Agent": os.environ.get("FLOAT_USER_AGENT", "AND Studio Sync"),
            "Content-Type": "application/json"}


def float_get_all(path, **params):
    out, page = [], 1
    while True:
        r = requests.get(f"{FLOAT}/{path}", headers=float_headers(), timeout=30,
                         params={**params, "page": page, "per-page": 200})
        r.raise_for_status()
        data = r.json()
        if not data:
            return out
        out += data
        if len(out) >= int(r.headers.get("x-pagination-total-count", len(data))):
            return out
        page += 1


def dropbox_client():
    dbx = dropbox.Dropbox(oauth2_refresh_token=need("DROPBOX_REFRESH_TOKEN"),
                          app_key=need("DROPBOX_APP_KEY"), app_secret=need("DROPBOX_APP_SECRET"))
    root = dbx.users_get_current_account().root_info
    if root.root_namespace_id != root.home_namespace_id:
        dbx = dbx.with_path_root(PathRoot.root(root.root_namespace_id))
    return dbx


def shared_link(dbx, path):
    try:
        return dbx.sharing_create_shared_link_with_settings(path).url
    except dropbox.exceptions.ApiError:
        links = dbx.sharing_list_shared_links(path=path, direct_only=True).links
        return links[0].url if links else ""


def list_items(list_id):
    items, cursor = [], None
    while True:
        body = {"list_id": list_id, "limit": 100}
        if cursor:
            body["cursor"] = cursor
        data = slack("slackLists.items.list", **body)
        items += data["items"]
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return items


def field(item, col_id):
    return next((f for f in item["fields"] if f["column_id"] == col_id), None)


def has_value(f):
    return bool(f and (f.get("link") or f.get("text") or f.get("select")))


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip()


def process(cfg, item, apply, dbx, float_cache):
    cols = cfg["cols"]
    name = ((field(item, cols["name"]) or {}).get("text") or "").strip()
    if not name:
        return "wait", "no name yet"
    select = (field(item, cols["asset_type"]) or {}).get("select") or []
    if not select or select[0] not in cfg["asset_types"]:
        return "wait", "Asset Type not set yet"
    _, phase_name, dbx_folder = cfg["asset_types"][select[0]]

    created = dt.datetime.fromtimestamp(int(item["date_created"]), tz=NY)
    project_name = f"{cfg['client']} - {created.strftime('%B')}"
    folder = f"{cfg['dropbox_root']}/{created.month:02d}-{created.strftime('%B')}/{dbx_folder}/{safe_name(name)}"
    plan = f"Float '{project_name}' > {phase_name} > {name} | Dropbox {folder}"

    if project_name not in float_cache:
        projects = [p for p in float_get_all("projects") if p["name"] == project_name and p.get("active") == 1]
        float_cache[project_name] = projects[0]["project_id"] if projects else None
    pid = float_cache[project_name]
    if not pid:
        return "error", f"Float project '{project_name}' not found or archived"
    key = (pid, "phases")
    if key not in float_cache:
        float_cache[key] = {p["name"]: p["phase_id"] for p in float_get_all("phases", project_id=pid) if p.get("active") == 1}
    if phase_name not in float_cache[key]:
        return "error", f"phase '{phase_name}' not found in '{project_name}'"
    existing = {t["task_name"] for t in float_get_all("project-tasks", project_id=pid)}

    if not apply:
        return "dry", plan
    steps = []
    if name not in existing:
        requests.post(f"{FLOAT}/project-tasks", headers=float_headers(), timeout=30, json={
            "project_id": pid, "phase_id": float_cache[key][phase_name], "task_name": name, "billable": 1}).raise_for_status()
        steps.append("Float task created")
    try:
        dbx.files_create_folder_v2(folder)
        steps.append("Dropbox folder created")
    except dropbox.exceptions.ApiError as e:
        if "conflict" not in str(e):
            raise
    link = shared_link(dbx, folder)
    if not link:
        return "error", "could not get the Dropbox link"
    slack("slackLists.items.update", list_id=item["list_id"], cells=[{
        "row_id": item["id"], "column_id": cols["internal_folder"], "link": [{"original_url": link}]}])
    steps.append("link written to Slack")
    return "done", f"{plan} | " + ", ".join(steps)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dbx = dropbox_client() if args.apply else None
    errors = 0
    float_cache = {}
    for list_id, cfg in LISTS.items():
        pending = [i for i in list_items(list_id)
                   if dt.datetime.fromtimestamp(int(i["date_created"]), tz=dt.timezone.utc) >= GO_LIVE
                   and not has_value(field(i, cfg["cols"]["internal_folder"]))]
        print(f"[{cfg['client']}] {len(pending)} item(s) to process")
        for item in pending:
            try:
                status, message = process(cfg, item, args.apply, dbx, float_cache)
            except Exception as e:
                status, message = "error", str(e)
            print(f"  {item['id']} -> {status}: {message}")
            errors += status == "error"
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
