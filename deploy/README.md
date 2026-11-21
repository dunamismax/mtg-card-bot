# Ubuntu VM deployment

The VM serves root-owned releases from
`/opt/mtg-card-bot/releases/<full-commit>`. The release helper uses Python
3.13.17 and uv 0.13.5, as pinned in [release](release). It copies the pinned
managed Python to a versioned, read-only directory under `/opt/mtg-card-bot/python`.
Each release gets an exact install from the existing `uv.lock`, runs Ruff,
Pyright, and pytest in a clean Git export, then removes development dependencies
with a second locked sync. Python imports the exported source directly.
Preparation runs without the production environment or a gateway connection.

Systemd uses the existing dedicated `mtg-card-bot` user, state directory
`/var/lib/mtg-card-bot`, and root-read environment file
`/home/sawyer/github/mtg-card-bot/.env`. The helper manages
`/etc/systemd/system/mtg-card-bot.service.d/60-release.conf`, which points Python
and its module search path at the `current` symlink.

## Prepare and activate

Run these commands on the VM. Fetch as `sawyer` without changing the working tree.
Use the full SHA of the
reviewed commit. Install the helper from the reviewed Git revision on the first
deployment or when the helper changes.

```sh
repo=/home/sawyer/github/mtg-card-bot
git -C "$repo" fetch origin main
revision=$(git -C "$repo" rev-parse origin/main)
git -C "$repo" show "$revision:deploy/release" # review before installing
helper=$(mktemp)
git -C "$repo" show "$revision:deploy/release" >"$helper" &&
  sudo install -m 0755 "$helper" /usr/local/sbin/mtg-card-bot-release
rm -f "$helper"

sudo mtg-card-bot-release prepare "$revision"
readlink /opt/mtg-card-bot/current
sudo mtg-card-bot-release activate "$revision" EXPECTED_CURRENT_SHA
```

Use `legacy` as `EXPECTED_CURRENT_SHA` on the first deployment, when there is
no `current` link. With a release already active, use the full SHA from its
link. Keep the shared deployment lock helper and the existing systemd unit
and account in place.

Activation takes the VM deployment lock, checks the expected revision and
dedicated service identity, and stops the old cgroup before switching the
symlink. It starts the same unit and waits for gateway readiness from the new
invocation. If validation, startup, or readiness fails, it restores the prior
selection and restarts it if it was running. Both releases remain on disk.

## Verify

```sh
systemctl show mtg-card-bot -p ActiveState -p User -p MainPID -p NRestarts
cat /opt/mtg-card-bot/current/.revision
invocation=$(systemctl show mtg-card-bot -p InvocationID --value)
sudo journalctl "_SYSTEMD_INVOCATION_ID=$invocation" -o cat --no-pager
```

Tests exercise a mocked Discord message through lookup and embed formatting,
including multi-card messages and the image-grid fallback. Verify live Scryfall
separately through the installed client with a normal card-name request.
Use a live Discord lookup only with an approved test identity in a documented
private bot-test channel.

## If a release fails

There is no rollback procedure (owner decision, 2026-11-21). If an activated
release misbehaves, fix the cause, prepare the fixed commit and activate it.

## Restore on a rebuilt VM

The VM backup collects the helper and the systemd drop-in. The drop-in's
`# Release:` line records the exact Git commit. Restore the unit, its drop-ins,
the dedicated user, and the encrypted environment file through the VM runbook.
Install the pinned uv version and run `uv python install 3.13.17` as `sawyer`.
Fetch the recorded commit into the source checkout, prepare it, then recreate
a missing `current` link before starting the service. Run this on the rebuilt
VM before enabling the bot.

```sh
revision=$(sed -n 's/^# Release: //p' /etc/systemd/system/mtg-card-bot.service.d/60-release.conf)
sudo mtg-card-bot-release prepare "$revision"
sudo /usr/local/sbin/with-deploy-lock ln -s "releases/$revision" /opt/mtg-card-bot/current
sudo systemctl daemon-reload
sudo mtg-card-bot-release activate "$revision" "$revision"
```
