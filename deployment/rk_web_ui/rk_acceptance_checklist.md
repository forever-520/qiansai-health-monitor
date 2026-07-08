# RK Web UI Acceptance Checklist

Use this checklist when the RK board is available.

## 1. Copy Package

```bash
scp -r rk_web_ui cat@RK_IP:/home/cat/rk_web_ui
```

Or unzip `rk_web_ui.zip` on the RK board.

```bash
sha256sum -c rk_web_ui.zip.sha256
unzip rk_web_ui.zip
```

```bash
cd /home/cat/rk_web_ui
python3 package_check.py
```

## 2. Start With Password

```bash
cd /home/cat/rk_web_ui
chmod +x start_web.sh start_web_secure.sh
WEB_USER=admin WEB_PASS=change-this-password ./start_web_secure.sh
```

Keep this terminal running.

## 3. Run Self Check On RK

Open another RK terminal:

```bash
cd /home/cat/rk_web_ui
CHECK_USER=admin CHECK_PASS=change-this-password python3 self_check.py
```

Pass criteria:

```text
HTTP /: status=200 OK
HTTP /healthz: status=200 OK
WebSocket /ws: first_type=vital_signs OK
```

## 4. Optional: Install Boot Service

Use this after the manual start passes.

```bash
cd /home/cat/rk_web_ui
cp rk_web_ui.env.example rk_web_ui.env
nano rk_web_ui.env
chmod +x install_systemd.sh uninstall_systemd.sh
sudo ./install_systemd.sh
systemctl status rk_web_ui
```

Pass criteria:

```text
systemctl shows active (running).
After reboot, http://RK_IP:8081 still opens.
```

## 5. Verify LAN Access

From another device on the same network:

```text
http://RK_IP:8081
```

Pass criteria:

```text
Browser shows the Web login page.
After login, the Web UI opens.
Logout returns to the Web login page.
Heart rate, breath rate, frame count, and waveforms update.
```

## 6. Prepare External Access

Only continue after LAN access passes.

Choose one:

```text
Cloudflare Tunnel: public HTTPS link, recommended when domain/account is available.
Tailscale: private remote access, recommended when all viewers can install Tailscale.
frp: public link through a cloud server.
Router port forwarding: only when router has public IP.
```

For Cloudflare Tunnel or frp, start from `external_access_templates.md`.

Pass criteria:

```text
External URL opens the same Web UI.
Web login is required before the page and WebSocket data load.
```

## 7. Record Result

```text
RK IP:
LAN URL:
External URL:
Service enabled:
Username:
Password location:
Date:
Tester:
```
