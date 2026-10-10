# Deployment

The bot runs on a **self-hosted Linux box on a residential connection** —
a spare laptop or mini PC with Ubuntu Server is plenty. It was previously
deployed to an AWS `t4g.micro`; that path still works and is documented at the
bottom, but the move off it was not about cost.

## Why not a cloud VM

YouTube treats **datacenter IP ranges** (AWS, GCP, …) as suspect. From EC2 the
bot needed a cookies file exported from a logged-in account, and those cookies
expired every few weeks — a recurring chore with a dead bot at the end of it
whenever it was forgotten.

From a residential IP the same requests work **with no cookies at all**, and
time-to-first-byte measured 4.3 s against 9.1 s on EC2. A residential IP does not
*remove* YouTube's bot-checking (see the player-client note below), but combined
with the right client chain it removes the need for credentials.

The trade is that the host is now your problem: power, network, and the disk it
boots from. FFmpeg audio streaming is CPU-light and memory-frugal, so the machine
itself barely notices — the service is capped at 768 MB and rarely peaks past
half of that.

## 1. Provision the host

Any Ubuntu 24.04 or 26.04 machine works. **Clone** the repo — do not copy the files over.
A git checkout is what makes `deploy/update.sh` work later, and what lets the bot
report which commit it is running:

```bash
git clone https://github.com/Isma-L154/LoopifyBot.git ~/LoopifyBot
cd ~/LoopifyBot
bash deploy/setup.sh
```

`setup.sh` installs `ffmpeg` + Python 3.14 + Deno, builds a venv, and registers
two systemd units: the `loopify-bot` service and a daily `loopify-ytdlp-update`
timer. It is idempotent, so re-running it is safe.

Ubuntu 26.04 ships Python 3.14. On 24.04, whose system Python is 3.12,
`setup.sh` adds the [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa)
and installs `python3.14` next to the system Python, which stays as it is. It
also adds the PPA to `unattended-upgrades`
(`/etc/apt/apt.conf.d/52loopify-deadsnakes`), so the bot's interpreter gets
security patches like the rest of the system.

For an always-on box, also worth doing: ignore the lid if it is a laptop
(`logind.conf.d`), mask the suspend targets, and enable `unattended-upgrades`.

## 2. Add secrets and start

Secrets are **never** committed. Create the `.env` directly on the host:

```bash
cp .env.example .env
nano .env            # fill in DISCORD_TOKEN (GENIUS_TOKEN is an optional lyrics fallback)
sudo systemctl start loopify-bot
sudo journalctl -u loopify-bot -f      # look for "Logged in as ..."
```

Then, from the Discord account that owns the bot application, send `!sync` in
any server the bot is in. That registers the `/` commands with Discord; until
it runs, only the `!` commands exist. The bot does not do this on its own at
startup because Discord rate-limits it and the bot restarts unattended.

## 3. Updating later

```bash
cd ~/LoopifyBot && bash deploy/update.sh
```

`update.sh` pulls, reinstalls dependencies **only if `requirements.txt` changed**,
reinstalls the systemd units, restarts the service, and then verifies it actually
came back — printing recent logs and failing loudly if it did not.

The units are reinstalled every time on purpose: a pull can change how the bot is
*run* (sandboxing, resource caps, stop timeouts), and restarting alone would keep
the old configuration while the repo claimed otherwise.

If the update added, removed or changed a command, send `!sync` again so the
`/` menu matches.

`update.sh` never changes the Python version. When a pull moves the bot to a new
one (the `PYTHON` line in `setup.sh`), re-run `setup.sh`: it stops the bot and
rebuilds the venv on the new interpreter. Then start the bot and check the
version it logs:

```bash
bash deploy/setup.sh && sudo systemctl start loopify-bot
```

### If the host was deployed by copying files instead of cloning

`update.sh` refuses to run and tells you how to convert it in place:

```bash
git init -b main
git remote add origin https://github.com/Isma-L154/LoopifyBot.git
git fetch origin
git branch --set-upstream-to=origin/main main
git reset --hard origin/main     # discards local edits — check first
```

The `--set-upstream-to` line matters: without it `update.sh` has nothing to pull
from and stops with an explanation. `.env` and `cookies.txt` are gitignored, so
they survive untouched.

### Knowing what is actually running

The bot logs its versions at startup, so `journalctl` answers this directly:

```bash
sudo journalctl -u loopify-bot | grep "Running commit" | tail -1
```

```
Running commit 1ea2431 — yt-dlp 2026.08.19, FFmpeg 6.1.1-3ubuntu5, Python 3.14.8
```

## Keeping yt-dlp current — automatically

`yt-dlp` is the only dependency deliberately left unpinned. YouTube changes its
player and extractors constantly, so a stale build starts failing to resolve
videos within weeks and fails outright within months — a `2026.3.3` build
returned `HTTP 403` on **every** YouTube URL until it was updated.

`setup.sh` installs a timer that handles this:

```bash
systemctl list-timers 'loopify-ytdlp-update*'      # when it next runs
sudo systemctl start loopify-ytdlp-update.service  # force a refresh now
sudo journalctl -u loopify-ytdlp-update -n 20      # what it did last time
```

It runs daily with a randomised delay, restarts the bot **only when the version
actually changed**, and leaves the working version installed if the upgrade
fails — a newer dependency is never worth trading a running bot for.
`Persistent=true` means it catches up after downtime rather than silently
skipping, which matters on a machine that is not on 24/7.

## 🎬 What actually makes YouTube work

Four things, in order of how much they matter:

1. **The player-client chain.** yt-dlp can impersonate several YouTube clients,
   and most of them are bot-checked. Measured from a residential IP against the
   same video: `web_embedded` works (~3.2 s), `mweb` works but is slow (~9.3 s),
   and `default`, `web`, `android_vr`, `tv`, `ios` and `android_music` are **all**
   bot-checked. The chain the bot uses is `web_embedded,mweb,tv_embedded` —
   getting this right is what removed the need for cookies.
2. **Deno**, for the JS signature (`nsig`) challenge on the web player.
   `setup.sh` installs it.
3. **yt-dlp does the fetching**, streaming the audio to stdout and piping it into
   FFmpeg. Handing a `googlevideo` URL straight to FFmpeg gets a 403, because
   those URLs are bound to the session that requested them.
4. **A residential IP**, which reduces the bot-checking but does not remove it.

### Cookies: supported, no longer needed

`COOKIES_PATH` still works if you want it, and helps with age- or region-gated
videos. It is no longer part of normal operation. If you do use one, export it
from a **throwaway** account — cookies grant access to it — and `chmod 600` it.

### Always-available fallback: SoundCloud

SoundCloud has none of these blocks and needs no credentials: `!play sc: <song>`,
or paste a track/set URL. If YouTube ever blocks a track the bot does not crash —
it posts a message suggesting SoundCloud and moves on to the next track.

## Operational notes

**Daily upgrades restart the bot.** With `unattended-upgrades` enabled,
`needrestart` restarts the service whenever it upgrades something the bot links
against. If that upgrade is glibc, the DNS resolver is being replaced at the same
moment, so a login can land on a resolver that is briefly unavailable. The bot
retries the login with backoff rather than exiting (see `utils/startup.py`).

**Stop timeouts are coupled to the voice timeout.** `TimeoutStopSec` must stay
above `cogs.music.VOICE_CONNECT_TIMEOUT`, because discord.py reuses the voice
*connect* timeout as the deadline for Discord to confirm a *departure* while
closing. With the two the wrong way round, systemd SIGKILLs a shutdown that was
going to finish — and a SIGKILL skips the reaping of the FFmpeg and yt-dlp
children. `tests/test_shutdown.py` enforces the ordering.

## Running on a cloud VM instead

Still supported, with the caveat above: from a datacenter IP you will probably
need a cookies file, and it will expire.

[`launch_ec2.sh`](launch_ec2.sh) holds the AWS CLI commands to launch a
`t4g.micro` (ARM Graviton, 1 GB RAM, free-tier eligible) with a security group
allowing inbound SSH from your IP only. The bot makes only **outbound**
connections, so nothing else needs opening. Then provision it exactly as above.

Cost is roughly **$6/month** on-demand, or free for 12 months under the free tier
(750 h/month). To stop billing entirely, terminate it:

```bash
aws ec2 terminate-instances --instance-ids <id>
```
