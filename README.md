# MCNameClaimer — Username Utility for Minecraft: Java Edition

**NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.**

MCNameClaimer is an independently developed, third-party Windows desktop utility. It watches
one Minecraft: Java Edition username for the person running it. When Minecraft Services
reports that name as available, it can **automatically send one name-change request for that
person's own account**.

> [!IMPORTANT]
> **Automatic claiming and approval status**
>
> - **Automatic claiming:** when the watched name is reported `AVAILABLE`, the app sends a
>   name-change request (`PUT /minecraft/profile/name/{name}`) for the signed-in player's
>   own account. It does not ask for confirmation at that moment.
> - **Live mode needs Mojang's approval:** it only works with an Azure application (client)
>   ID that Mojang has approved for Minecraft API access through its AppID review. The
>   developer's client ID is **not currently approved**. Approval is at Mojang's discretion
>   and is not guaranteed.
> - **Possible restrictions:** automated claiming may be restricted. Mojang has not reviewed
>   or endorsed this code.
> - **Live mode is locked by default:** it stays locked in the app until the user confirms
>   their own client ID has been approved. It locks itself again if Minecraft Services
>   refuses that ID.
> - **Simulation mode** needs no sign-in, contacts neither Microsoft nor Mojang, and is the
>   way to try the app.

---

## What it does

1. The player signs in with their own Microsoft account using Microsoft OAuth. The app never
   sees their password.
2. It checks the account: is there a Java Edition profile, and does Minecraft Services
   currently allow a name change?
3. It checks one chosen username at a slow, fixed pace: never faster than every 20 seconds
   and never more than 16 times in 5 minutes.
4. When the name is reported `AVAILABLE`, it sends **one** name-change request for the
   player's own profile.
5. It reads the profile to confirm the result, then stops.

It runs locally on the player's PC. It is not a hosted service, it does not act for other
people's accounts, and it is not sold.

## Compliance and limitations

### Approval and restrictions

- **Mojang AppID approval is required for the current Live mode implementation.** Minecraft
  Services rejects Azure client IDs that Mojang has not approved with HTTP 403 at
  `login_with_xbox`. Reviews are requested at <https://aka.ms/mce-reviewappid>.
- **The app treats that 403 as final.** It does not retry or use another application's
  client ID. It deletes the saved sign-in and locks Live mode again.
- **Approval is not guaranteed.** Mojang has not said whether automated name-change requests
  are eligible for approval.

### What Minecraft Services can and can't tell you

- **You must own Minecraft: Java Edition** (an Xbox Game Pass Java profile also counts). The
  app refuses to start if the account has no Java Edition profile.
- **Minecraft enforces a username-change cooldown**, widely reported as 30 days. The app reads
  the server's `nameChangeAllowed` flag and refuses to start while it is `false`. It never
  works around the cooldown.
- **`AVAILABLE` does not guarantee you can claim the name.** Another player can take it between
  the check and the request. Mojang can also refuse a change for reasons the availability
  check doesn't report. The app shows the server's response either way.
- **A name someone gives up may not become available straight away.** Mojang does not publish
  the timing.
- **The endpoints are unofficial.** Mojang publishes no third-party reference for them. The app
  follows community documentation (<https://minecraft.wiki/w/Mojang_API>), and the endpoints
  may change without notice.

### Rate limits

The rate limits come from **community documentation, not an official Mojang source**:

- 20 availability checks per 5 minutes, per account
- about 200 requests per 2 minutes, per IP, for most other endpoints

The real limits may differ. The app stays well below these figures, and whatever the server
says wins: HTTP 429 always pauses the app, and `Retry-After` is always obeyed. See
[Rate-limit protections](#rate-limit-protections).

### What the app does not do

- No proxies, IP rotation or VPN switching.
- No multiple accounts or account pooling.
- No parallel requests.
- No CAPTCHA solving.
- No retry loops on the name-change endpoint.
- No borrowed or impersonated client IDs. The `User-Agent` honestly identifies the app as
  `MCNameClaimer/<version>`.
- No game code, game assets, Minecraft or Mojang logos, textures or fonts.
- No telemetry and no other network traffic.

### Policies this project follows

| Policy | What it asks | How this project handles it |
|---|---|---|
| [Minecraft Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines), *Essential guidelines* | Don't appear "official or approved by, endorsed by, associated with, supported by, or connected to" Mojang or Microsoft. Prominently include a disclaimer like "NOT AN OFFICIAL MINECRAFT [PRODUCT/SERVICE/EVENT/etc.]. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT". Make clear who is responsible and whom to contact | The disclaimer is at the top of this README, in [PRIVACY.md](PRIVACY.md) and in the app window. See [Publisher and contact](#publisher-and-contact) |
| Minecraft Usage Guidelines, *Naming guidelines* | "You may not use the Minecraft name as the primary or dominant name or title"; it may appear in a secondary title | The name is **MCNameClaimer**. "for Minecraft: Java Edition" appears only in the descriptive subtitle |
| [Minecraft EULA](https://www.minecraft.net/en-us/eula), *Summary* | "You may develop tools, plug-ins and services as long as they do not seem official or approved by us, such as by using our logos" | No logos, no game assets, and the disclaimer everywhere |
| [Microsoft Services Agreement](https://www.microsoft.com/en-us/servicesagreement), §3(a)(vi) and §8(b)(vii) | Don't "circumvent any restrictions on access to, usage, or availability of the Services". Don't "enable access to the Services … by unauthorized third-party applications" | Live mode is locked unless the user confirms an approved client ID, and a 403 locks it again. No rate-limit evasion |
| [Microsoft APIs Terms of Use](https://learn.microsoft.com/en-us/legal/microsoft-apis/terms-of-use), §3 and §5 | Request only the minimum permissions. Don't circumvent limits. Publish a privacy statement | Scopes are `XboxLive.signin` and `offline_access` only. The local request budget. [PRIVACY.md](PRIVACY.md) |

### Questions only Mojang can answer

- Is an app that automatically sends name-change requests eligible for AppID approval?
- Is the "MC" prefix in the name acceptable? The Usage Guidelines also cover names
  "confusingly similar to our name".
- What are the actual rate limits for these endpoints?

---

## For reviewers

| | |
|---|---|
| **What it is** | Open-source desktop application (Python and tkinter) that runs on the player's own Windows PC |
| **Who uses it** | A single player, acting on their own Minecraft: Java Edition account |
| **Accounts** | One Microsoft account per running app. No account pooling, no alternate accounts, no proxies |
| **Sign-in** | Microsoft identity platform, OAuth 2.0 device authorization grant, `consumers` tenant |
| **Scopes** | `XboxLive.signin`, `offline_access` |
| **Client secret** | None (public client). No client ID is bundled. The user enters their own approved client ID |
| **What it changes** | Only the signed-in player's own profile name, via `PUT /minecraft/profile/name/{name}` |
| **Automation** | Yes. When the watched name is `AVAILABLE`, one name-change request is sent automatically |
| **Unapproved client ID** | Live mode is locked. A 403 from `login_with_xbox` deletes the saved sign-in and locks it again |

**Every request the app makes (Live mode only; Simulation mode makes none):**

| Purpose | Request | How often |
|---|---|---|
| Microsoft sign-in | `POST login.microsoftonline.com/consumers/oauth2/v2.0/devicecode` and `/token` | Once per sign-in. Token refresh about every 24 h |
| Xbox Live | `POST user.auth.xboxlive.com/user/authenticate`, `POST xsts.auth.xboxlive.com/xsts/authorize` | At each sign-in or token refresh |
| Minecraft token | `POST api.minecraftservices.com/authentication/login_with_xbox` | At each sign-in or token refresh (token lasts 24 h) |
| Account check | `GET /minecraft/profile`, `GET /minecraft/profile/namechange`, `GET /entitlements/license` | After sign-in and each time monitoring starts |
| Availability | `GET /minecraft/profile/name/{name}/available` | Every N seconds, **N ≥ 20**. Never more than **16 per rolling 5 minutes** |
| Claim | `PUT /minecraft/profile/name/{name}` | **Once per `AVAILABLE` result**, at most **3 per monitoring run** |
| Verify claim | `GET /minecraft/profile` | Up to 3 times after a claim attempt |

---

## Requirements

- Windows 10 or 11
- Python 3.10 or newer from <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"**
  in the installer. tkinter is included by default.
- One Python package, [`requests`](https://pypi.org/project/requests/). The launcher installs it
  automatically.
- **Live mode only:** a Microsoft account that owns Minecraft: Java Edition, and an Azure client
  ID approved by Mojang.

## Quick start (Simulation mode, no setup needed)

1. Download or clone this repository.
2. Double-click **`Launch MC Name Claimer.bat`**.
   The first run creates a private `.venv` folder and installs `requests`, which takes about a
   minute. Later launches open straight away.
3. Leave **Mode** on *Simulation*, pick a scenario, type a username and press **Start**.

## Simulation mode

Simulation mode replaces the real API with a built-in fake that has the same behaviour and
error types. The monitor, claim logic, backoff and window all run the same code they use in
Live mode. It needs **no Microsoft sign-in and no Mojang approval**, makes **no network
requests**, and uses a fake account called `SimPlayer`.

| Scenario | What it shows |
|---|---|
| Name frees up, claim succeeds | Taken for 3 checks, then available. One claim, verified on the profile |
| Rate limited (HTTP 429), then success | `Retry-After` is obeyed, then the claim succeeds |
| Someone else claims it first | The claim gets 403 `DUPLICATE`. The response is shown and monitoring continues |
| Server error, but the change went through | The claim gets 503. The app reads the profile instead of sending the request again |
| Blocked name (NOT_ALLOWED) | Monitoring stops with an explanation |
| Account on 30-day name-change cooldown | Start is refused before any availability check |
| Name never frees up | Shows the schedule, countdown and Stop button |

## Live mode setup

> [!WARNING]
> Live mode does not work without Mojang's approval of your client ID, and that approval is not
> guaranteed. Don't use another application's client ID to get around this.

### 1. Register an Azure app to get a client ID

Microsoft only issues sign-in tokens to registered applications. Registration is free.

1. Go to <https://portal.azure.com>, search **App registrations**, then click **New registration**.
2. **Name:** for example `MCNameClaimer`. People see this name on Microsoft's sign-in screen, so
   don't include "Minecraft", "Mojang", "Xbox" or "Microsoft" in it.
3. **Supported account types:** *Personal Microsoft accounts only*.
4. **Redirect URI:** leave empty. Click **Register**.
5. Copy the **Application (client) ID**. It is a public identifier, not a secret.
6. Open **Authentication**, then **Advanced settings**. Set **Allow public client flows** to
   **Yes**, then click **Save**.
7. Don't create a client secret. The app doesn't use one.
8. Optional but recommended: under **Branding & properties**, set the privacy statement URL to
   this repository's [PRIVACY.md](PRIVACY.md).

### 2. Request Mojang's approval

Submit the client ID at <https://aka.ms/mce-reviewappid>. Approval is at Mojang's discretion.
Without it, Minecraft Services answers the sign-in with HTTP 403 and Live mode can't be used.

### 3. Unlock Live mode (only after approval)

Switch **Mode** to **Live** and paste the client ID. Then tick **"Mojang has approved this
client ID for Minecraft API access"**.

Until you tick that box, the app makes no sign-in or Minecraft requests in Live mode. The
confirmation covers only that exact client ID: editing the ID removes it. If Minecraft
Services refuses the ID, the app deletes the saved sign-in and unticks the box.

### 4. Sign in

1. Click **Sign in with Microsoft**.
2. A dialog shows a short code. Click **Copy code and open sign-in page**, paste the code at
   microsoft.com/link, and sign in there.
3. The account line then shows, for example:
   `Signed in as Steve | Java Edition: owned | Name change: allowed`

### 5. Monitor

Enter the username (3–16 characters: letters, digits, underscore). Choose an interval of at
least 20 seconds and press **Start**. Press **Stop** at any time.

## Authentication

The app uses the standard Minecraft: Java Edition sign-in chain:

1. **Microsoft:** OAuth 2.0 device authorization grant at `login.microsoftonline.com/consumers`,
   with scope `XboxLive.signin offline_access`. The app polls at the interval Microsoft asks for
   and slows down when told to.
2. **Xbox Live user token:** from `user.auth.xboxlive.com`.
3. **XSTS token:** for relying party `rp://api.minecraftservices.com/`. Xbox errors are
   explained in plain language, for example "no Xbox profile", child accounts or unsupported
   regions.
4. **Minecraft access token:** from `login_with_xbox`. It lasts 24 hours and is refreshed
   automatically 5 minutes before it expires, or after an HTTP 401. A 403 here means the client
   ID isn't approved (see [Approval and restrictions](#approval-and-restrictions)).

The app saves the Microsoft refresh token and the current Minecraft access token. Both are
encrypted with **Windows DPAPI**, so only the same Windows user on the same PC can decrypt them.

They are deleted when:

- you click **Sign out**
- Microsoft revokes or expires the refresh token
- Minecraft Services refuses the client ID

## How a claim works

1. **Account check:** confirms a Java profile exists, shows ownership, and checks
   `nameChangeAllowed`. If you already own the name, the app stops immediately.
2. **Monitor:** calls `GET .../name/{name}/available` every N seconds.
3. **Claim:** on `AVAILABLE`, sends one `PUT .../name/{name}`.
4. **Verify:** reads `GET /minecraft/profile` to confirm the new name.
5. **Stop:** after a confirmed claim, nothing else is sent.

## Rate-limit protections

- **Minimum interval of 20 seconds** in Live mode: at most 15 checks per 5 minutes, 75% of the
  community-documented figure.
- **Hard local budget of 16 checks per rolling 5 minutes.** It applies across Stop and Start, so
  restarting can't cause a burst.

| Situation | What the app does |
|---|---|
| `DUPLICATE` (taken) | Waits one interval |
| HTTP 429 | Waits the longer of `Retry-After` and an exponential backoff (2x, 4x and so on of the interval, capped at 15 min). A longer `Retry-After` is always obeyed |
| Timeout, network error or 5xx on a check | Exponential backoff, as above |
| HTTP 403 at sign-in (client ID not approved) | Stops, deletes the saved sign-in and locks Live mode |
| HTTP 403 or 400 on an availability check | Stops and shows the response |
| `NOT_ALLOWED` | Stops: the name is blocked or reserved |
| `AVAILABLE` | Sends **one** name-change request |
| Claim lost to someone else (403 `DUPLICATE`) | Shows the response, waits at least 60 s, keeps monitoring |
| Claim refused (any other 403, or 400, for example the cooldown) | Shows the response and **stops** |
| Claim timed out or got a 5xx (outcome unknown) | Reads the profile up to 3 times. **Never** sends the request again without checking |
| HTTP 401 | Refreshes the token once. A second 401 in a row stops monitoring |
| 3 claim attempts in one run | Stops, so it can never loop on name changes |

## Security and privacy

The full privacy statement, including how to delete your data and revoke access, is in
[PRIVACY.md](PRIVACY.md). To report a vulnerability, see [SECURITY.md](SECURITY.md).

All runtime data is stored outside the project folder, in `%LOCALAPPDATA%\MCNameClaimer\`:

| File | Contents |
|---|---|
| `credentials.bin` | Refresh token and Minecraft access token, **DPAPI-encrypted** |
| `settings.json` | Client ID (a public identifier, not a secret), approval confirmation, interval, last username, mode |
| `logs\claimer.log` | Checks and claim attempts. Rotates at 1 MB and keeps 5 files |

- Your password is typed only on Microsoft's website.
- Tokens are never passed to the logger. A redaction filter also removes anything that looks
  like a token from the log file and the on-screen log, as a second safeguard.
- The app contacts only the Microsoft, Xbox Live and Minecraft Services endpoints listed above.
- This repository contains no secrets. `.gitignore` excludes virtual environments, caches, logs,
  settings and credential files.

## Running the tests

Double-click **`Run Tests.bat`**, or run:

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The suite makes **no network requests** and never changes a real username. It covers:

- **Usernames:** validation.
- **Rate limits:** `Retry-After` parsing, backoff and the local rate budget.
- **API client:** how each HTTP response is turned into an error.
- **Sign-in:** the full Microsoft, Xbox and Minecraft chain, including XSTS errors.
- **Unapproved client ID:** the 403 that locks Live mode.
- **Tokens:** refresh and rotation.
- **Monitor and claims:** every branch, and every simulation scenario.
- **Encryption:** DPAPI storage.
- **Logging:** a check that no token ever reaches the log file.
- **Window:** the real window in Simulation mode, from Start to Claimed, plus the Live mode lock,
  the window title and the disclaimer.

The tokens in the test files are fake values used to test redaction.

## Troubleshooting

| Message | Fix |
|---|---|
| *Python 3.10 or newer (with tkinter) was not found* | Install Python from python.org with "Add python.exe to PATH" ticked |
| *Live mode is locked* | Expected until Mojang approves your client ID. See [Live mode setup](#live-mode-setup) |
| *Allow public client flows* | Azure portal: open your app, then **Authentication**. Set *Allow public client flows* to **Yes** |
| *The client ID wasn't found for personal accounts* | Check the ID. *Supported account types* must include personal accounts |
| *Minecraft Services refused this Azure app (HTTP 403)* | Mojang hasn't approved this client ID. Live mode stays locked |
| *This Microsoft account has no Xbox profile yet* | Sign in once at xbox.com or in the Minecraft Launcher |
| *Name change: NOT allowed yet* | The account is in its name-change cooldown |
| Window doesn't appear | Run `Launch MC Name Claimer.bat` from a Command Prompt to see errors, and check `%LOCALAPPDATA%\MCNameClaimer\logs\claimer.log` |

## Project layout

```
Launch MC Name Claimer.bat   double-click to run (sets up .venv on first run)
Run Tests.bat                runs the test suite
setup.bat                    shared venv + requirements bootstrap
requirements.txt             runtime dependency: requests
requirements-dev.txt         + pytest
LICENSE                      MIT License
PRIVACY.md                   privacy statement
SECURITY.md                  how to report a vulnerability
mcclaimer/
  __init__.py                version, public title and disclaimer
  __main__.py                entry point (python -m mcclaimer)
  gui.py                     tkinter window; all network work on background threads
  monitor.py                 check / claim / verify loop and every safety rule
  api.py                     Minecraft Services client (one request per call, typed errors)
  auth.py                    Microsoft device code, then Xbox Live, XSTS and Minecraft; token refresh
  account.py                 ownership and name-change eligibility checks
  ratelimit.py               Retry-After parsing, backoff, sliding-window budget
  secure_store.py            DPAPI-encrypted credential file
  simulation.py              offline fake of the API with 7 scenarios
  logging_setup.py           rotating log file + secret redaction
  config.py, paths.py, validation.py, errors.py
tests/                       pytest suite (no network)
```

## Publisher and contact

MCNameClaimer is developed and published independently by GitHub user
[yuvithebro](https://github.com/yuvithebro), who is solely responsible for it. Mojang and
Microsoft are not responsible for it.

- **Email:** <dhillonsyuvraj@gmail.com> for questions, policy or trademark concerns, and takedown
  requests.
- **Bug reports:** <https://github.com/yuvithebro/MCNameClaimer/issues>.
- **Security problems:** follow [SECURITY.md](SECURITY.md).

## License

Released under the [MIT License](LICENSE). The license covers this project's own code and
documentation only. It grants no rights to the Minecraft, Mojang or Microsoft names,
trademarks or other property.

## Trademarks and disclaimer

NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.

"Minecraft" and "Mojang" are trademarks of Microsoft Corporation and its affiliates. They are
used here only to describe what this utility works with. This project uses no Minecraft or
Mojang logos, artwork, fonts or game assets.

The software is provided as is, without warranty. You are responsible for following the
[Minecraft EULA](https://www.minecraft.net/en-us/eula), the
[Microsoft Services Agreement](https://www.microsoft.com/en-us/servicesagreement) and any
other terms that apply to your account.
