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

Copy the project to the instance (excluding secrets/venv) and run the setup
script, which installs `ffmpeg` + Python, builds a venv, and installs the
`loopify-bot` systemd service:

```bash
# from your laptop, in the repo root
rsync -av --exclude .venv --exclude .git --exclude __pycache__ \
      -e "ssh -i <key>.pem" ./ ubuntu@<EC2_IP>:~/LoopifyBot/

ssh -i <key>.pem ubuntu@<EC2_IP>
cd ~/LoopifyBot
bash deploy/setup.sh
```

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
cd ~/LoopifyBot && git pull            # or rsync again
sudo systemctl restart loopify-bot
```

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
