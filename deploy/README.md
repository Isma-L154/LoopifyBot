# Deployment — AWS EC2 (t4g.micro, ARM)

This bot is designed to run on the cheapest always-on AWS instance: a
**t4g.micro** (ARM Graviton, 1 GB RAM, free-tier eligible). FFmpeg audio
streaming is CPU-light and memory-frugal thanks to lazy stream resolution, so
1 GB is enough for a single-guild-at-a-time music bot.

## What gets created

| Resource        | Value                                            |
|-----------------|--------------------------------------------------|
| Instance type   | `t4g.micro` (ARM)                                |
| AMI             | Ubuntu 24.04 LTS (arm64)                          |
| Disk            | 8 GB gp3                                          |
| Security group  | inbound SSH (22) from your IP only; all outbound |
| Service         | `loopify-bot` (systemd, auto-restart, boot-start)|

The bot makes only **outbound** connections (Discord, YouTube, SoundCloud), so no
inbound ports beyond SSH are required.

## 1. Launch the instance

The exact AWS CLI commands used to launch and tag the instance live in
[`launch_ec2.sh`](launch_ec2.sh). Run it from a machine with the AWS CLI
configured, or follow it step by step.

## 2. Provision it

**Clone** the repo on the instance — do not copy the files over. A git checkout
is what makes `deploy/update.sh` work later and lets the bot report which commit
it is running:

```bash
ssh -i <key>.pem ubuntu@<EC2_IP>

git clone https://github.com/Isma-L154/LoopifyBot.git ~/LoopifyBot
cd ~/LoopifyBot
bash deploy/setup.sh
```

`setup.sh` installs `ffmpeg` + Python + Deno, builds a venv, and registers two
systemd units: the `loopify-bot` service and a daily `loopify-ytdlp-update`
timer. It is idempotent, so re-running it is safe.

## 3. Add secrets and start

Secrets are **never** committed. Create the `.env` directly on the instance:

```bash
cp .env.example .env
nano .env            # fill in DISCORD_TOKEN (and Spotify/Genius if used)
sudo systemctl start loopify-bot
sudo systemctl status loopify-bot
sudo journalctl -u loopify-bot -f      # live logs — look for "Logged in as ..."
```

## Updating the bot later

```bash
ssh -i <key>.pem ubuntu@<EC2_IP>
cd ~/LoopifyBot && bash deploy/update.sh
```

`update.sh` pulls, reinstalls dependencies **only if `requirements.txt`
changed**, restarts the service, and then verifies it actually came back up —
printing recent logs and failing loudly if it did not.

### If the host was deployed by copying files instead of cloning

`update.sh` refuses to run and tells you how to convert it in place. The short
version, from the app directory:

```bash
git init
git remote add origin https://github.com/Isma-L154/LoopifyBot.git
git fetch origin
git reset --hard origin/main     # discards local edits — check first
```

`.env` and `cookies.txt` are gitignored, so they survive this untouched.

### Knowing what is actually running

The bot logs its versions at startup, so `journalctl` answers this directly:

```
Running commit 0a90877 — yt-dlp 2026.08.19, FFmpeg 6.1.1-3ubuntu5, Python 3.12.3
```

```bash
sudo journalctl -u loopify-bot | grep "Running commit" | tail -1
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

## 🎬 YouTube from cloud IPs — how it's made to work

YouTube fights bots on **datacenter IPs** (AWS, GCP…) on two fronts, and the bot
handles both so playback works from EC2:

1. **"Sign in to confirm you're not a bot"** → defeated with **cookies** from a
   logged-in account (`COOKIES_PATH`).
2. **JS signature ("nsig") challenge** on the web player → solved with **Deno**,
   which `setup.sh` installs automatically.
3. **Session-bound stream URLs** (which 403 if FFmpeg fetches them directly) →
   avoided by having **yt-dlp stream the audio and pipe it into FFmpeg**, so
   yt-dlp (with the cookies/session) does the fetching. This is built into the bot.

### Keeping YouTube working: refresh the cookies

Cookies are the one thing that expires. When YouTube starts getting blocked,
export fresh ones and drop them in:

```bash
# Export from a browser logged into a THROWAWAY YouTube account, Netscape format
# (e.g. the "Get cookies.txt LOCALLY" extension), then:
scp -i <key>.pem cookies.txt ubuntu@<EC2_IP>:~/LoopifyBot/cookies.txt
ssh -i <key>.pem ubuntu@<EC2_IP> "chmod 600 ~/LoopifyBot/cookies.txt && sudo systemctl restart loopify-bot"
```

Use a throwaway account — cookies grant access to it. Refresh every few weeks.

### Always-available fallback: SoundCloud

SoundCloud has none of these blocks and needs no cookies: `!play sc: <song>` or
paste a SoundCloud track/set URL. If YouTube ever blocks a track, the bot doesn't
crash — it posts a message suggesting SoundCloud and moves to the next track.

## Cost & teardown

- ~**$6/month** on-demand (or free under the 12-month free tier: 750 h/month).
- To stop billing entirely, terminate the instance:
  ```bash
  aws ec2 terminate-instances --instance-ids <id>
  ```
