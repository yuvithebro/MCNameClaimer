# Privacy Statement: MCNameClaimer

**NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.**

*Last updated: 2026-10-14*

MCNameClaimer runs entirely on your own Windows PC. The developer runs no server and
receives no data from the app: no telemetry, analytics, advertising or crash reports.

## What the app sends, and to whom

The app only makes requests in **Live mode**, after you have unlocked it and signed in.
**Simulation mode** sends nothing to anyone.

| Recipient | What is sent | Why |
|---|---|---|
| Microsoft (`login.microsoftonline.com`) | The app's client ID, the device-code sign-in requests and, when renewing, your refresh token | To sign you in with Microsoft OAuth |
| Xbox Live (`user.auth.xboxlive.com`, `xsts.auth.xboxlive.com`) | Your Microsoft access token, then your Xbox token | To get a Minecraft token, the standard Java Edition sign-in |
| Minecraft Services (`api.minecraftservices.com`) | Your Minecraft access token, the username you watch, and the name-change request | Account checks, availability checks and the name change you asked for |

You type your password only on Microsoft's website. The app never sees it.

## What is stored on your PC

Everything is kept in `%LOCALAPPDATA%\MCNameClaimer\`:

| File | Contents |
|---|---|
| `credentials.bin` | Your Microsoft refresh token and current Minecraft access token, encrypted with Windows DPAPI for your Windows user account |
| `settings.json` | Client ID, approval confirmation, target username, check interval and mode |
| `logs\claimer.log` (up to 5 rotated files of 1 MB each) | Timestamps, availability results, claim results and your Minecraft profile name. Tokens are never logged, and anything that looks like one is redacted |

Data from Microsoft, Xbox Live and Minecraft Services is used only to:

- show your account status
- carry out the actions you start

It is not shared, sold or used for advertising.

## Keeping or deleting your data

- **Sign out** in the app deletes `credentials.bin`.
- Saved credentials are also deleted automatically when either of these happens:
  - Microsoft revokes or expires your sign-in.
  - Minecraft Services refuses the app's client ID.
- **To remove everything:**
  1. Sign out and close the app.
  2. Delete the `%LOCALAPPDATA%\MCNameClaimer` folder.
  3. Delete the project folder.
- **To revoke the app's access to your Microsoft account** at any time, go to
  <https://account.live.com/consent/Manage>. That is Microsoft's site, not this app.

## Microsoft and Mojang

Requests to Microsoft, Xbox Live and Minecraft Services are handled under their own terms and
the [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement). This
statement covers only what MCNameClaimer itself does.

## Changes

Any change to this statement is made in this file, and the repository's history shows every
revision.

## Contact

See [Publisher and contact](README.md#publisher-and-contact) in the README.
