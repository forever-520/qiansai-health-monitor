# External Access Templates

Use these only after LAN access works and `WEB_PASS` is set.

## Option A: Cloudflare Tunnel

Best when you can use a Cloudflare account and a domain.

1. Start the Web UI securely:

```bash
cd /home/cat/rk_web_ui
WEB_USER=admin WEB_PASS=strong-password ./start_web_secure.sh
```

2. Install and login to `cloudflared` on the RK board.

3. Create a tunnel and copy `cloudflared_config.yml.example`:

```bash
mkdir -p ~/.cloudflared
cp cloudflared_config.yml.example ~/.cloudflared/config.yml
nano ~/.cloudflared/config.yml
```

4. Replace:

```text
RK_TUNNEL_ID
your-domain.example.com
/home/cat/.cloudflared/RK_TUNNEL_ID.json
```

5. Run:

```bash
cloudflared tunnel run RK_TUNNEL_ID
```

Pass criteria:

```text
https://your-domain.example.com opens the Web UI.
The browser asks for username/password first.
```

## Option B: frp

Best when you have a public cloud server.

1. On the public server, run `frps` with a known `token`.

2. On RK, copy and edit the client template:

```bash
cd /home/cat/rk_web_ui
cp frpc.ini.example frpc.ini
nano frpc.ini
```

3. Replace:

```text
YOUR_PUBLIC_SERVER_IP
CHANGE_THIS_TOKEN
your-domain.example.com
```

4. Run:

```bash
frpc -c frpc.ini
```

Pass criteria:

```text
http://your-domain.example.com opens the Web UI.
The browser asks for username/password first.
```

## Do Not Skip

```bash
WEB_USER=admin WEB_PASS=strong-password ./start_web_secure.sh
python3 self_check.py
```

Do not expose the service with an empty `WEB_PASS`.
