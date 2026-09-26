# HTTPS, or: why the browser will not install the app

If you serve the Controller over plain `http://` to a LAN address, three things
are missing and **nothing tells you so**:

| | |
|---|---|
| service worker | `navigator.serviceWorker` is **undefined** |
| PWA install prompt | never offered |
| screen wake lock | `navigator.wakeLock` is **undefined** |

🔴 **They fail by absence, not by error.** Nothing throws, nothing logs, no
console warning. `register().catch(() => {})` swallows it and you conclude the
feature simply does not work on your phone.

The rule: only **`https://`** and **`localhost`** are *secure contexts*. On the
machine running the Controller, `http://localhost:8787` is fine and everything
works - which is exactly why this is so easy to miss. From your phone, the same
server over `http://192.168.x.x:8787` is not.

`shopcam doctor` reports this, and Settings › About in the UI reports which case
**that particular device** is in.

## Fix 1 - the Chrome flag (two minutes, per device)

Good enough for a private workshop LAN and a phone you own.

1. On the phone, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
2. Paste your origin into the text box: `http://192.168.1.50:8787`
   (exact scheme, host and port - no trailing slash)
3. Set the dropdown to **Enabled**, then **Relaunch**
4. Load the Controller → menu → **Install app**

You now get an icon in the launcher, no URL bar, and a screen that stays awake.

⚠️ It is called "unsafely" for a reason: you are telling Chrome to trust an
unauthenticated origin. Only do it for an address on a network you control, and
never for a public one.

## Fix 2 - actually serve TLS (the right answer for anyone else)

A self-signed certificate **is not enough on its own** - Chrome treats a
cert-error origin as insecure too, so you get none of the features back. The
certificate has to be genuinely trusted by the device.

The short version, with [mkcert](https://github.com/FiloSottile/mkcert):

```bash
mkcert -install                       # creates a local CA and trusts it here
mkcert 192.168.1.50 localhost         # cert + key for your rig's address
```

Then run the Controller behind it:

```bash
uvicorn --factory shopcam2000.server:create_app \
        --host 0.0.0.0 --port 8787 \
        --ssl-certfile ./192.168.1.50+1.pem \
        --ssl-keyfile  ./192.168.1.50+1-key.pem
```

Install the mkcert **root CA** on every device that will use the Controller
(`mkcert -CAROOT` shows where it is; Android: Settings → Security → Encryption &
credentials → Install a certificate → CA certificate).

> 💡 A certificate bound to an **IP address** works but is awkward to renew. If
> you have a local DNS name for the rig, use that instead.
