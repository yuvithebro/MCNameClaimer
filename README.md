# MCNameClaimer

**A Windows desktop app that watches one Minecraft: Java Edition username and automatically
claims it for your own account when Mojang reports it available.**

> [!IMPORTANT]
> **Disclosure: this program performs automatic username claiming.** When the Minecraft
> Services API reports the watched name as `AVAILABLE`, the app sends a name-change request
> for the signed-in player's **own** account without further confirmation.
>
> Real (Live mode) claiming only works after Mojang approves the Azure application (client)
> ID through its AppID review (<https://aka.ms/mce-reviewappid>). Until then, only Simulation
> mode works, and it never contacts Microsoft or Mojang.

---

## Purpose

When a Java Edition username you want becomes free, it can be claimed by whoever asks first.
MCNameClaimer lets one player:

1. sign in to their own account,
2. pick one username to watch,
3. check it at a slow, rate-limit-safe pace,
4. claim it once, the moment the API says it's available,
5. confirm the result on their profile.

It runs locally on the player's PC. It is not a hosted service, and it never handles other
people's accounts.

## Features

- **Automatic claiming.** It sends exactly one name-change request per `AVAILABLE` result
  (at most 3 per run), then verifies the new name on the profile.
- **Secure sign-in.** It uses the official Microsoft OAuth 2.0 device-code flow. The player
  types their password only on Microsoft's website; the app never sees it.
- **Account checks before monitoring.** It confirms there is a Java Edition profile, shows
  ownership, and refuses to start during the 30-day name-change cooldown.
- **Rate-limit protection.**
  - Minimum 20-second interval.
  - Hard budget of 16 availability checks per 5 minutes.
  - Exponential backoff that honours `Retry-After` on HTTP 429.
  - Conservative handling of errors.
- **Simulation mode.** It exercises the entire program, including claims, rate limits and
  failures, against a built-in fake API. No real account is touched.
- **Desktop GUI.** Shows the target username, availability, last and next check time (with a
  countdown) and the claim result, plus Start and Stop buttons.
- **Activity log.** Every check and claim attempt goes on screen and to a rotating log file.
- **Credential safety.**
  - Tokens are encrypted at rest with Windows DPAPI.
  - Tokens are never written to logs.
  - No telemetry.

## For the Mojang AppID review team

| | |
|---|---|
| **What it is** | Open-source desktop application (Python and tkinter) that runs on the player's own Windows PC |
| **Who uses it** | A single player, acting on their own Minecraft: Java Edition account |
| **Accounts** | One Microsoft account per running app. No account pooling, no alt accounts, no proxies |
| **Sign-in** | Microsoft identity platform, OAuth 2.0 device authorization grant, `consumers` tenant |
| **Scopes** | `XboxLive.signin`, `offline_access` |
| **Client secret** | None (public client). No client ID is bundled. The user enters their approved client ID in the app |
| **What it changes** | Only the signed-in player's own profile name, via `PUT /minecraft/profile/name/{name}` |
| **Automation** | Yes. When the watched name is `AVAILABLE`, one name-change request is sent automatically |

**Every request the app makes:**

| Purpose | Request | How often |
|---|---|---|
| Microsoft sign-in | `POST login.microsoftonline.com/consumers/oauth2/v2.0/devicecode` and `/token` | Once per sign-in. Token refresh about every 24 h |
| Xbox Live | `POST user.auth.xboxlive.com/user/authenticate`, `POST xsts.auth.xboxlive.com/xsts/authorize` | At each sign-in or token refresh |
| Minecraft token | `POST api.minecraftservices.com/authentication/login_with_xbox` | At each sign-in or token refresh (token lasts 24 h) |
| Account check | `GET /minecraft/profile`, `GET /minecraft/profile/namechange`, `GET /entitlements/license` | After sign-in and each time monitoring starts |
| Availability | `GET /minecraft/profile/name/{name}/available` | Every N seconds, **N ≥ 20**. Never more than **16 per rolling 5 minutes** |
| Claim | `PUT /minecraft/profile/name/{name}` | **Once per `AVAILABLE` result**, at most **3 per monitoring run** |
| Verify claim | `GET /minecraft/profile` | Up to 3 times after a claim attempt |

**What it does not do:** no proxies, no IP or account rotation, no CAPTCHA solving, no
parallel requests, no retry loops on the name-change endpoint, and no bypassing of any
rate limit or Mojang policy.

---

## Requirements

- Windows 10 or 11
- Python 3.10 or newer from <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"**
  in the installer. tkinter is included by default.
- Only one Python package, [`requests`](https://pypi.org/project/requests/). The launcher installs it automatically.

## Quick start (Simulation mode, no setup needed)

1. Download or clone this repository.
2. Double-click **`Launch MC Name Claimer.bat`**.
   The first run creates a private `.venv` folder and installs `requests`, which takes about
   a minute. Later launches open straight away.
3. Leave **Mode** on *Simulation*, pick a scenario, type a username and press **Start**.

## Simulation mode

Simulation mode swaps the real API for a built-in fake one with the same behaviour and the
same error types. The monitor, claim logic, backoff and GUI all run exactly the code they
use in Live mode. It makes **no network requests** and uses a fake account called `SimPlayer`.

| Scenario | What it shows |
|---|---|
| Name frees up, claim succeeds | Taken for 3 checks, then available. One claim, verified on the profile |
| Rate limited (HTTP 429), then success | `Retry-After` is honoured, then the claim succeeds |
| Someone else claims it first | The claim gets 403 `DUPLICATE`. The response is shown and monitoring continues |
| Server error, but the change went through | The claim gets 503. The app reads the profile instead of re-sending |
| Blocked name (NOT_ALLOWED) | Monitoring stops with an explanation |
| Account on 30-day name-change cooldown | Start is refused before any availability check |
| Name never frees up | Shows the schedule, countdown and Stop button |

## Live mode setup (one time)

### 1. Register an Azure app to get a client ID

Microsoft only issues sign-in tokens to registered applications. App registrations are free.

1. Go to <https://portal.azure.com>, search **App registrations**, then click **New registration**.
2. **Name:** anything, for example `MCNameClaimer`.
3. **Supported account types:** *Personal Microsoft accounts only*
   (or *Accounts in any organizational directory and personal Microsoft accounts*).
4. **Redirect URI:** leave empty. Click **Register**.
5. Copy the **Application (client) ID**.
6. Open **Authentication**, then **Advanced settings**. Set **Allow public client flows** to **Yes**, then click **Save**.
7. Don't create a client secret. The app doesn't use one.

### 2. Get the client ID approved by Mojang

New Azure apps must be approved before they can call the Minecraft Services API. Submit the
client ID at <https://aka.ms/mce-reviewappid>.

Until it's approved, sign-in stops at the last step with *"Minecraft Services refused this
Azure app (HTTP 403)"*. That is expected. The app does **not** work around it, for example by
borrowing another application's client ID.

### 3. Sign in

1. Switch **Mode** to **Live**, paste the client ID, then click **Sign in with Microsoft**.
2. A dialog shows a short code. Click **Copy code and open sign-in page**, paste the code at
   microsoft.com/link, and sign in there.
3. The account line then shows, for example:
   `Signed in as Steve | Java Edition: owned | Name change: allowed`

### 4. Monitor

Enter the username (3–16 characters: letters, digits, underscore), choose an interval of at
least 20 seconds, and press **Start**. Press **Stop** at any time.

## Authentication

The app uses the standard Minecraft: Java Edition sign-in chain:

1. **Microsoft:** OAuth 2.0 device-code flow at `login.microsoftonline.com/consumers`,
   scope `XboxLive.signin offline_access`. The app polls at the interval Microsoft asks for
   and slows down when told to.
2. **Xbox Live user token:** from `user.auth.xboxlive.com`.
3. **XSTS token:** for relying party `rp://api.minecraftservices.com/`. Xbox errors such as
   "no Xbox profile", child accounts or unsupported regions are explained in plain language.
4. **Minecraft access token:** from `login_with_xbox`. It lasts 24 hours and is refreshed
   automatically 5 minutes before expiry, or after an HTTP 401.

Saved credentials are the Microsoft refresh token and the current Minecraft access token.
They are encrypted with **Windows DPAPI**, so only the same Windows user on the same PC can
decrypt them. **Sign out** deletes them. If Microsoft revokes or expires the refresh token,
the app signs out and asks you to sign in again.

## How a claim works

1. **Account check:** confirms a Java profile exists, shows ownership, and checks
   `nameChangeAllowed`. If you already own the name, the app stops immediately.
2. **Monitor:** calls `GET .../name/{name}/available` every N seconds.
3. **Claim:** on `AVAILABLE`, sends one `PUT .../name/{name}`.
4. **Verify:** reads `GET /minecraft/profile` to confirm the new name.
5. **Stop:** after a confirmed claim, nothing else is sent.

## Rate-limit protections

Mojang doesn't publish official third-party documentation for these endpoints. The
community-maintained documentation at <https://minecraft.wiki/w/Mojang_API> gives these
limits:

- Name availability: **20 requests per 5 minutes, per account**
- Most other endpoints: 200 requests per 2 minutes, per IP

The app stays below them:

- **Minimum interval of 20 seconds** in Live mode: at most 15 checks per 5 minutes, 75% of the limit.
- **Hard local budget of 16 checks per rolling 5 minutes.** It applies across Stop and Start, so restarting can't cause a burst.

| Situation | What the app does |
|---|---|
| `DUPLICATE` (taken) | Waits one interval |
| HTTP 429 | Waits the longer of `Retry-After` and an exponential backoff (2x, 4x, ... the interval, capped at 15 min). A longer `Retry-After` is always honoured |
| Timeout, network error or 5xx on a check | Exponential backoff, as above |
| `NOT_ALLOWED` | Stops: the name is blocked or reserved |
| `AVAILABLE` | Sends **one** name-change request |
| Claim lost to someone else (403 `DUPLICATE`) | Shows the response, waits at least 60 s, keeps monitoring |
| Claim refused (other 403, or 400) | Shows the response and **stops** |
| Claim timed out or got a 5xx (outcome unknown) | Reads the profile up to 3 times. **Never** re-sends blindly |
| HTTP 401 | Refreshes the token once. Two in a row stops monitoring |
| 3 claim attempts in one run | Stops, so it can never loop on name changes |

## Security and privacy

All runtime data is stored outside the project folder, in `%LOCALAPPDATA%\MCNameClaimer\`:

| File | Contents |
|---|---|
| `credentials.bin` | Refresh token and Minecraft access token, **DPAPI-encrypted** |
| `settings.json` | Client ID (public by design, not a secret), interval, last username, mode |
| `logs\claimer.log` | Checks and claim attempts. Rotates at 1 MB and keeps 5 files |

- Tokens are never passed to the logger. A redaction filter also scrubs anything
  token-shaped from the log file and the on-screen log, as a second line of defence.
- The app only contacts the Microsoft, Xbox Live and Minecraft Services endpoints listed above.
  There is no telemetry and no other network traffic.
- Nothing secret is stored in this repository. `.gitignore` excludes virtual environments,
  caches, logs, settings and credential files.

## Limitations

- **Unofficial endpoints.** They follow community documentation and Mojang may change them
  without notice.
- **Mojang approval is required** for Live mode.
- **`AVAILABLE` is not a reservation.** Another player can claim the name between the check
  and the request.
- **Released names aren't instant.** When someone changes away from a name, Mojang typically
  holds it for a period (historically about 37 days) before anyone else can take it.
- **Name-change cooldown:** one change per 30 days on your own account.
- **Xbox Game Pass:** the entitlement list can be empty for Game Pass players. The app accepts
  any account that has a Java profile.
- **The PC must stay awake** while monitoring.
- Use it only on your own account. Mojang's EULA doesn't allow selling or transferring accounts.

## Running the tests

Double-click **`Run Tests.bat`**, or run:

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The suite makes **no network requests**. It covers:

- username validation
- `Retry-After` parsing, backoff and the local rate budget
- the API client's error mapping
- the full Microsoft, Xbox and Minecraft sign-in chain, including XSTS errors and the 403 for an unapproved app
- token refresh and rotation
- every monitor and claim branch, and all simulation scenarios
- DPAPI encryption
- a test that no token ever reaches the log file
- the real window in Simulation mode, from Start to Claimed

The tokens in the test files are fake fixtures used to test redaction.

## Troubleshooting

| Message | Fix |
|---|---|
| *Python 3.10 or newer (with tkinter) was not found* | Install Python from python.org with "Add python.exe to PATH" ticked |
| *Allow public client flows* | Azure portal: your app, then **Authentication**. Set *Allow public client flows* to **Yes** |
| *The client ID wasn't found for personal accounts* | Check the ID. *Supported account types* must include personal accounts |
| *Minecraft Services refused this Azure app (HTTP 403)* | The client ID isn't approved yet. See Live mode step 2 |
| *This Microsoft account has no Xbox profile yet* | Sign in once at xbox.com or in the Minecraft Launcher |
| *Name change: NOT allowed yet* | The name was changed less than 30 days ago |
| Window doesn't appear | Run `Launch MC Name Claimer.bat` from a Command Prompt to see errors, and check `%LOCALAPPDATA%\MCNameClaimer\logs\claimer.log` |

## Project layout

```
Launch MC Name Claimer.bat   double-click to run (sets up .venv on first run)
Run Tests.bat                runs the test suite
setup.bat                    shared venv + requirements bootstrap
requirements.txt             runtime dependency: requests
requirements-dev.txt         + pytest
mcclaimer/
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

## Disclaimer

MCNameClaimer is an independent project. It is not affiliated with, endorsed by, or
supported by Mojang Studios or Microsoft. Minecraft is a trademark of Mojang Synergies AB.
