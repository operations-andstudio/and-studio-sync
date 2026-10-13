# AND Studio Sync

Runs every 10 minutes on GitHub Actions. For each new item in a client's Slack List
(with a name and an Asset Type), it creates the Float task and the Dropbox folder with
the same name, and writes the Dropbox link back into "Dropbox Internal Folder".

- **Add a client:** add an entry to `LISTS` in `sync.py` (list id, column ids, asset type options, Dropbox root).
- **Run it by hand:** Actions tab > "Slack Lists sync" > Run workflow (untick "apply" for a dry run).
- **See what happened:** Actions tab > open a run > "Run the sync".
- **If it fails:** GitHub emails the repo admins. Common causes: a Float project for the month does not exist
  (create "<Client> - <Month>" first), an expired Dropbox token, or an item without Asset Type (it waits and retries).

Secrets to set under Settings > Secrets and variables > Actions:
`SLACK_BOT_TOKEN`, `FLOAT_API_TOKEN`, `FLOAT_USER_AGENT`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`.
