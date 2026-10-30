# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities.

Report them privately:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability** (GitHub Security Advisories).
3. Describe the issue; we will respond within a few days.

For sensitive matters you may also contact the maintainer listed in
[CODEOWNERS](.github/CODEOWNERS).

## Local UI and image downloads

The Web UI binds to `127.0.0.1` by default. It will not listen on another
address unless `INKSTONE_UI_ALLOW_LAN=1`, `INKSTONE_UI_TOKEN`, and
`INKSTONE_UI_ORIGIN` are set together. Requests whose `Host` is not a loopback
name are rejected on a loopback bind, so a DNS name that rebinds to
`127.0.0.1` cannot call the API or read files. `/files/` serves generated
images, PDF/CBZ, and `reader.html` only.

Image downloads accept `https` URLs whose addresses are public. Redirects are
checked again. Set `INKSTONE_IMAGE_DOWNLOAD_HOSTS` when a compat gateway
should be limited to known CDN names.
