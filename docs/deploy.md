# Deployment

The review app runs locally with `cutter serve` (see the README). To share cuts with reviewers outside your
network, publish it on a server with HTTPS. This page also covers GPUs.

## A review server

What the server needs: Python 3.9 or newer (standard library only), nginx, systemd, and SSH access for you. No Docker,
no Python packages. A small machine such as a Raspberry Pi is enough.

**1. Settings.** Copy `deploy.env.example` to `deploy.env` – in the project folder or in the cutter checkout; it is
git-ignored – and fill it in:

```sh
TARGET_HOST=user@review.example.org   # ssh target (keys from ~/.ssh or the ssh agent)
TARGET_DIR=/home/user/cutter          # app/, data/, server/ and htpasswd live here
DOMAIN=review.example.org             # nginx site name
PORT=8089                             # local port of the service (nginx proxies to it)
SERVICE=cutter-review                 # systemd unit
```

**2. Upload the app and the server files, once:**

```sh
cutter deploy app server --dry-run    # shows every ssh/rsync command first
cutter deploy app server
ssh -t user@review.example.org sudo bash /home/user/cutter/server/install.sh
```

`install.sh` (generated from `src/cutter/templates/server/`) installs the systemd unit (the service listens on
127.0.0.1 only, with a read-only file system except `data/`), the nginx site, and reloads nginx only if `nginx -t`
accepts the configuration. It needs `sudo`: an agent asks before running it. Then add HTTPS, for example with
`sudo certbot --nginx -d review.example.org`; `install.sh` leaves a site that certbot has extended unchanged.

**3. Passwords** are set on the server, one per reviewer:
`ssh -t user@review.example.org python3 /home/user/cutter/app/set_password.py alice` (stores only a scrypt hash in
`htpasswd`).

**4. Publish cuts:**

```sh
cutter review-data cut3
cutter deploy cut3                    # video and thumbnails first, timeline.json last
```

A cut appears in the app only when it is complete. `cutter deploy app` also restarts the service so that the
reviewers' apps load the new version; comments (`data/feedback.sqlite`) and passwords are never touched.

**Feedback** from the server: `cutter feedback fetch` and `cutter feedback reply` work over ssh when a `deploy.env`
is found (project folder first, then the cutter checkout); otherwise they use the local database. `--env` names
another file.

### Docker on the server

The image runs on arm64 as well, so a server with Docker can run the review app from it instead of systemd + Python:

```sh
docker run -d --name cutter-review --restart unless-stopped -p 127.0.0.1:8089:8089 \
  -v /srv/film:/srv/film -w /srv/film ghcr.io/aaronblankenburg/cutter:0.1.0 serve --bind 0.0.0.0 --port 8089
```

with nginx in front as above. This variant is documented only and has not been tested on a server.

## Sharing in your own network

`cutter serve` publishes the app on all interfaces (`--bind 127.0.0.1` keeps it on this computer). It speaks
plain HTTP: anyone in the same network could read passwords and watch the film. Share it only in networks you trust
(your home Wi-Fi, not a café or an office). On macOS the firewall asks once whether to accept incoming connections.

## GPU

The image runs everything on the CPU: Whisper with int8, FilmCraft with its CPU renderer. On Linux with an NVIDIA
GPU, transcription can be several times faster with CUDA. This is not part of the image and has not been tested:

- **faster-whisper with CUDA** needs the NVIDIA driver on the host, the NVIDIA Container Toolkit
  (`docker run --gpus all`) and the CUDA 12 libraries cuBLAS and cuDNN 9 in the image (for example
  `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` in a derived image, with `LD_LIBRARY_PATH` pointing to them).
  Then run `cutter analyze --device cuda`.
- **FilmCraft** decides about GPU rendering itself (`export --gpu-rendering auto|off`); in the container it renders
  on the CPU.
- **macOS:** Docker cannot use the GPU; everything runs on the CPU.
