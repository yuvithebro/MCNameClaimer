# Security Policy

**NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.**

## Supported versions

Only the latest version on the `main` branch receives fixes.

## Reporting a vulnerability

Please report security problems privately. Do not open a public issue for them.

- Use GitHub's private vulnerability reporting: on the repository's **Security** tab, choose
  **Report a vulnerability**. If that option isn't shown, email <dhillonsyuvraj@gmail.com>.
- Never include real tokens, the contents of `credentials.bin`, or log files that show account
  details. A description and the steps to reproduce are enough.

Reports are acknowledged as soon as possible. If an issue affects how the app connects to
Microsoft APIs, it will also be reported to the Microsoft Security Response Center
(<secure@microsoft.com>), as the
[Microsoft APIs Terms of Use](https://learn.microsoft.com/en-us/legal/microsoft-apis/terms-of-use)
(§4) require.

## Security design

- **No secrets in the code.** The app is a public client with no client secret. The Azure client
  ID is a public identifier, entered by the user, and no client ID is bundled.
- **Passwords:** never handled. Sign-in uses the Microsoft OAuth 2.0 device authorization grant.
- **Saved tokens:** encrypted with Windows DPAPI, so only the same Windows user on the same PC
  can decrypt them.
- **Logs:** tokens are never logged, and a redaction filter scrubs anything that looks like one.
- **Network:** HTTPS with certificate verification only. It contacts the Microsoft, Xbox Live and
  Minecraft Services endpoints listed in the README and nothing else.
- **Unapproved client IDs:** Live mode stays locked until the user confirms their client ID is
  approved. A refusal from Minecraft Services (HTTP 403) deletes the saved sign-in and locks
  Live mode again.
