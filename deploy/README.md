# Ubuntu VM deployment

The VM serves root-owned releases from
`/opt/mtg-card-bot/releases/<full-commit>`. The release helper uses Python
3.13.17 and uv 0.13.5, as pinned in [release](release). It copies the pinned
managed Python to a versioned, read-only directory under `/opt/mtg-card-bot/python`.
Each release gets an exact install from the existing `uv.lock`, runs Ruff,
Pyright, and pytest in a clean Git export, then removes development dependencies
with a second locked sync. Python imports the exported source directly.
Preparation runs without the production environment or a gateway connection.

Systemd runs [mtg-card-bot.service](mtg-card-bot.service) as the dedicated
`mtg-card-bot` user with state directory `/var/lib/mtg-card-bot`. The unit
carries the sandbox of the VM's web units (`systemd-analyze security` scores
it about 3.5), sees no home directory and reads nothing from this checkout.
The Discord token is the only secret: `MTG_DISCORD_TOKEN` in the root-only
`/etc/web-apps/mtg_card_bot/app.env` (directory `0700`, file `0600`, both
`root:root`), which systemd reads before it drops privileges. A `.env` in the
checkout is for local development only and must not hold the live token. The
helper manages `/etc/systemd/system/mtg-card-bot.service.d/60-release.conf`,
which records the active commit and points Python and its module search path
at the `current` symlink.

## Install or change the unit

Run this when [mtg-card-bot.service](mtg-card-bot.service) changes, from the
reviewed revision. The next activation, or a restart, applies it.

```sh
repo=/home/sawyer/github/mtg-card-bot
unit=$(mktemp)
git -C "$repo" show "$revision:deploy/mtg-card-bot.service" >"$unit"
sudo /usr/local/sbin/with-deploy-lock bash -c '
  install -m 0644 -o root -g root "$1" /etc/systemd/system/mtg-card-bot.service &&
  systemd-analyze verify /etc/systemd/system/mtg-card-bot.service &&
  systemctl daemon-reload' _ "$unit"
rm -f "$unit"
systemd-analyze security mtg-card-bot.service | tail -1
```

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

Use `none` as `EXPECTED_CURRENT_SHA` on the first deployment, when there is
no `current` link. With a release already active, use the full SHA from its
link. Keep the shared deployment lock helper and the existing account in
place.

Activation takes the VM deployment lock, checks the expected revision and
dedicated service identity, and stops the old cgroup before switching the
symlink. It starts the same unit and waits for gateway readiness from the new
invocation. If validation, startup, or readiness fails, it stops there and
says so: the new release stays selected, and the fix is a new commit (see
below).

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
the dedicated user, and the encrypted `/etc/web-apps/mtg_card_bot/app.env`
through the VM runbook.
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
