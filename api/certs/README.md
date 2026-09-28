# Local trust anchors

**This directory is empty in CI and in production, and must stay that way.**

It exists for one situation: a development machine whose antivirus, VPN or corporate
proxy performs **TLS interception** — terminating HTTPS and re-signing it with a root
of its own. Windows (or macOS) trusts that root, so browsers are fine; containers
carry their own CA bundle and do not, so every outbound HTTPS request from a
container fails certificate verification.

The symptom is confusing, because it is not obviously about certificates:

- `pip install` during the image build: `Could not find a version that satisfies the
  requirement setuptools>=75 (from versions: none)`
- Telegram delivery at runtime: `CERTIFICATE_VERIFY_FAILED: unable to get local
  issuer certificate`
- `apk` / `apt` in any container: fails to fetch

Confirm it is interception rather than a network fault by looking at who signed the
certificate the container is actually served:

```bash
docker compose exec -T api python -c "import socket,ssl;from cryptography import x509;ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE;s=socket.create_connection(('api.telegram.org',443),timeout=10);t=ctx.wrap_socket(s,server_hostname='api.telegram.org');print(x509.load_der_x509_certificate(t.getpeercert(True)).issuer.rfc4514_string())"
```

A real CA (Let's Encrypt, GTS, DigiCert) means something else is wrong. Your own
antivirus or proxy means this directory is the fix.

## Using it

Drop the interceptor's **root** certificate here as a PEM file ending in `.crt`. On
Windows, export it from the certificate store:

```powershell
$c = Get-ChildItem Cert:\LocalMachine\Root |
     Where-Object { $_.Subject -match '<your interceptor>' } | Select-Object -First 1
$pem = "-----BEGIN CERTIFICATE-----`n" +
       [Convert]::ToBase64String($c.RawData,'InsertLineBreaks') +
       "`n-----END CERTIFICATE-----`n"
[IO.File]::WriteAllText("D:\Tracelet\api\certs\local-tls-inspection.crt",
                        ($pem -replace "`r`n","`n"))
```

Then `docker compose build api`. The Dockerfile installs anything it finds here into
the system trust store **and** appends it to `certifi`'s bundle, because Python's
HTTP clients do not read the system store — `httpx` and `pip` both use `certifi`, and
installing the certificate system-wide alone fixes `apt` while leaving Telegram
broken in a way that looks unrelated.

## Why `*.crt` is git-ignored

A TLS-interception root is specific to one machine, and committing one would be
actively harmful: every other developer, CI, and the production box would silently
trust a CA that has nothing to do with them. Only this README and `.gitkeep` are
tracked.

## Preferred fix

This is a workaround, not the right answer. TLS interception means every encrypted
connection is decrypted and re-encrypted inside another process, which is a real
trade independent of Docker. Turning off the interception — or excluding Docker from
it — is better where that is an option.

The contents of this directory affect **nothing** in CI or production: no file, no
behaviour change, no image-size cost.
