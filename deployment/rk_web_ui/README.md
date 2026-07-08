# RK Web UI Deployment Package

This package runs the Qt-style Web UI on a PC first, then on the RK board.

## Files

```text
rk_web_ui/
  README.md
  cloudflared_config.yml.example
  external_access_templates.md
  frpc.ini.example
  install_systemd.sh
  package_check.py
  rk_acceptance_checklist.md
  rk_web_ui.env.example
  rk_web_ui.service
  server.py
  self_check.py
  start_web.sh
  start_web_secure.sh
  uninstall_systemd.sh
  start_web_pc.bat
  start_web_pc_lan.bat
  start_web_pc_secure.bat
  ui/
    index.html
    styles.css
    app.js
    bedside_display_ref.png
    bedside_imaging_ref.png
```

## Run On PC

From this folder:

```bat
start_web_pc.bat
```

Open:

```text
http://127.0.0.1:8081
```

For LAN testing on PC:

```bat
start_web_pc_lan.bat
```

Find the PC IPv4 address:

```bat
ipconfig
```

Open from another device on the same LAN:

```text
http://PC_IP:8081
```

Optional password on Windows:

```bat
set WEB_USER=admin
set WEB_PASS=123456
start_web_pc.bat
```

When `WEB_PASS` is set, the browser shows the built-in Web login page.
Use the `Logout` button in the top-right corner to return to the login page.

Safer LAN/public start on Windows:

```bat
set WEB_USER=admin
set WEB_PASS=123456
start_web_pc_secure.bat
```

## Self Check

Before starting the server, verify the package files:

```bash
python package_check.py
```

After the server starts, open another terminal and run:

```bash
python self_check.py
```

For a custom port:

```bash
CHECK_PORT=8082 python self_check.py
```

If password is enabled:

```bash
CHECK_USER=admin CHECK_PASS=123456 python self_check.py
```

Expected output:

```text
HTTP /: status=200 OK
HTTP /healthz: status=200 OK
WebSocket /ws: first_type=vital_signs OK
```

## Copy To RK

Replace `cat@RK_IP` with the RK username and address:

```bash
scp -r rk_web_ui cat@RK_IP:/home/cat/rk_web_ui
```

Or copy `rk_web_ui.zip` and `rk_web_ui.zip.sha256` to the RK board, then verify and unzip:

```bash
sha256sum -c rk_web_ui.zip.sha256
unzip rk_web_ui.zip
```

## Run On RK

```bash
cd /home/cat/rk_web_ui
chmod +x start_web.sh
./start_web.sh
```

Open from another device on the same LAN:

```text
http://RK_IP:8081
```

Optional password on RK:

```bash
WEB_USER=admin WEB_PASS=123456 ./start_web.sh
```

Safer LAN/public start on RK:

```bash
WEB_USER=admin WEB_PASS=123456 ./start_web_secure.sh
```

Change port:

```bash
PORT=8082 ./start_web.sh
```

## Run On RK At Boot

Create the environment file:

```bash
cd /home/cat/rk_web_ui
cp rk_web_ui.env.example rk_web_ui.env
nano rk_web_ui.env
```

Set a real password in `rk_web_ui.env`, then install the service:

```bash
chmod +x install_systemd.sh uninstall_systemd.sh
sudo ./install_systemd.sh
```

Check status and logs:

```bash
systemctl status rk_web_ui
journalctl -u rk_web_ui -f
```

Check health:

```bash
curl http://127.0.0.1:8081/healthz
```

Remove the service:

```bash
sudo ./uninstall_systemd.sh
```

## External Access

Finish LAN access first. After `python3 self_check.py` passes on RK and another LAN device can open `http://RK_IP:8081`, choose one external access method.

Recommended options:

```text
Cloudflare Tunnel: good public HTTPS link, no router port forwarding, needs a Cloudflare account/domain.
Tailscale: safest private remote access, no public link, every viewer needs Tailscale.
frp: good public link through your own cloud server, needs a public server.
Router port forwarding: simple only if the router has a public IP; expose only with WEB_PASS enabled.
```

Minimum rule for public access:

```bash
WEB_USER=admin WEB_PASS=strong-password ./start_web_secure.sh
```

Do not expose the server to the internet with an empty `WEB_PASS`.
Public access uses the same Web login page; scripts can still use Basic Auth for self-checks.

For ready-to-edit tunnel templates, see:

```text
external_access_templates.md
cloudflared_config.yml.example
frpc.ini.example
```

## Data Backend

The UI connects to the same server:

```text
ws://current-host:PORT/ws
```

`server.py` sends simulated vital-sign data on `/ws` for PC/RK bring-up.
When real radar data is ready, keep the same JSON message format:

```json
{"type":"vital_signs","data":{"hr":78.5,"br":16.2,"motion":35,"presence":"occupied","stability":"stable"}}
{"type":"stats","frame_count":28267,"parser_err":0,"crc_err":0,"online":true}
{"type":"waveform","heart":[128,132],"breath":[128,130]}
```

## RK Shared Data Stack

Use this mode when Qt UI and Web UI must share the same LubanCat data on the RK board.

```text
LubanCat -> RK gateway :8000/:8001 -> Qt UI + Web UI
```

The gateway connects to LubanCat once. Qt reads `http://127.0.0.1:8000/radar/raw`.
Web reads the same gateway through the Web server `/ws` proxy and uses `/camera/capture` for camera capture.

Install the WebSocket dependency on RK once:

```bash
cd /home/cat/rk_web_ui
python3 -m pip install -r requirements-rk.txt
```

Create the stack environment file:

```bash
cp rk_stack.env.example rk_stack.env
nano rk_stack.env
```

Recommended settings:

```bash
WEB_USER=admin
WEB_PASS=strong-password
DATA_MODE=gateway
DATA_HTTP_URL=http://127.0.0.1:8000
DATA_WS_URL=ws://127.0.0.1:8001/ws
LUBANCAT_HOST=lubancat.local
LUBANCAT_PORT=9001
GATEWAY_HTTP_PORT=8000
GATEWAY_WS_PORT=8001
```

Start the shared stack:

```bash
chmod +x start_rk_stack.sh
./start_rk_stack.sh
```

Open Web UI:

```text
http://RK_IP:8081
```

Check gateway data on RK:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/radar/raw
curl -X POST http://127.0.0.1:8000/camera/capture --output capture.jpg
```

Start Qt UI against the same gateway:

```bash
cd /home/cat/radar_qt
RADAR_REMOTE_URL=http://127.0.0.1:8000/radar/raw \
LUBANCAT_CAPTURE_HOST=127.0.0.1 \
LUBANCAT_CAPTURE_PORT=8000 \
./run_board.sh
```

`run_board.sh` now uses these same default values, so the explicit environment variables are only needed when overriding.

### Avoid Fixed LubanCat IP

Prefer a stable hostname:

```bash
LUBANCAT_HOST=lubancat.local
```

After changing WiFi, keep Qt and Web unchanged. Only the RK gateway needs to resolve `lubancat.local`.
If hostname discovery is unavailable on the current router, temporarily set:

```bash
LUBANCAT_HOST=192.168.x.x
```
