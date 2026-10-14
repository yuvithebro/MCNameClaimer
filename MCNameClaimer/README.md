# Minecraft Name Claimer

A small Windows desktop app that watches one Minecraft: Java Edition username and, when
Mojang's API reports it **AVAILABLE**, sends **one** name-change request for your own
account, then checks your profile to confirm it worked.

- Microsoft sign-in with a one-time code. The app never sees your password.
- Respects Mojang's documented rate limits, backs off on HTTP 429 and honours `Retry-After`.
- Simulation mode runs every code path without touching your real account.
- Logs every check and claim attempt. Tokens are never written to the log.

---

## Quick start (Simulation mode, no setup)

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>.
   Tick **"Add python.exe to PATH"** in the installer. (tkinter, used for the window,
   is included by default.)
2. Double-click **`Launch MC Name Claimer.bat`**.
   The first run creates a private `.venv` folder and installs `requests`, which takes
   about a minute. After that it opens straight away.
3. Leave **Mode** on *Simulation*, pick a scenario, type a username, press **Start**.

Simulation makes no network requests. It uses a fake account called `SimPlayer`.

---

## Live mode setup (one time)

Live mode talks to the real Microsoft and Minecraft services, so it has two prerequisites
that this app can't skip for you.

### 1. Register a free Azure app (gives you a *client ID*)

Microsoft only issues sign-in tokens to registered applications. You register your own.
App registrations are free.

1. Go to <https://portal.azure.com>, search for **App registrations**, click **New registration**.
2. **Name:** anything, e.g. `MC Name Claimer (personal)`.
3. **Supported account types:** *Personal Microsoft accounts only*
   (or *Accounts in any organizational directory and personal Microsoft accounts*).
4. **Redirect URI:** leave empty. Click **Register**.
5. Copy the **Application (client) ID** from the overview page.
6. Open **Authentication**, then **Advanced settings**. Set **Allow public client flows** to **Yes** and click **Save**.
7. Do **not** create a client secret. This app doesn't use one.

If the portal says you need a directory or tenant first, sign up for the free Azure account.
The app registration itself costs nothing.

### 2. Ask Mojang to approve your client ID

Newly registered Azure apps must be approved by Mojang before they can call the
Minecraft API. Submit your client ID with the form at **<https://aka.ms/mce-reviewappid>**.

Until it is approved, sign-in fails at the last step with:

> Minecraft Services refused this Azure app (HTTP 403) ...

That is expected. There is no legitimate way around it. This app deliberately does **not**
borrow another application's client ID (such as the official launcher's). Approval timing
is up to Mojang.

### 3. Sign in

1. Switch **Mode** to **Live**.
2. Paste the client ID and click **Sign in with Microsoft**.
3. A window shows a short code. Click **Copy code and open sign-in page**, paste the code
   at microsoft.com/link, and sign in there.
4. Back in the app, the account line shows something like:
   `Signed in as Steve | Java Edition: owned | Name change: allowed`

You stay signed in between launches. **Sign out** deletes the saved credentials.

### 4. Monitor

Type the target username, choose an interval (**minimum 20 seconds** in Live mode),
and press **Start**.

---

## What happens when you press Start

1. **Account check:** reads your Java profile, entitlements and name-change eligibility.
   Start is refused if you have no Java profile or are inside the 30-day name-change
   cooldown. If you already own the name, it stops immediately.
2. **Monitoring:** checks `GET /minecraft/profile/name/<name>/available` every N seconds.
3. **AVAILABLE:** sends exactly **one** `PUT /minecraft/profile/name/<name>`.
4. **Verification:** reads `GET /minecraft/profile` to confirm the new name.
5. **Done:** stops after a confirmed claim. Nothing else is sent.

The window shows the target, availability, last check time, next check time (with a
countdown), the claim result and the raw API response for any failure.

## Rate limits and retries

The community-maintained docs at <https://minecraft.wiki/w/Mojang_API> state:

- Name availability: **20 requests per 5 minutes, per account**
- Most other endpoints: 200 requests per 2 minutes, per IP

How the app stays inside those limits:

- **Minimum interval of 20 seconds in Live mode.** That's at most 15 checks per 5 minutes,
  75% of the limit.
- **A hard local budget of 16 checks per rolling 5 minutes.** It survives Stop/Start, so
  restarting can't cause a burst.

| Situation | What the app does |
|---|---|
| `DUPLICATE` (taken) | Waits one interval |
| HTTP 429 | Waits the longer of `Retry-After` and an exponential backoff (2x, 4x, ... the interval, capped at 15 min). A longer `Retry-After` is always honoured |
| Timeout, network error or 5xx on a check | Exponential backoff, same as above |
| `NOT_ALLOWED` | Stops: the name is blocked or reserved |
| `AVAILABLE` | Sends **one** name-change request |
| Claim lost to someone else (403 `DUPLICATE`) | Shows the response, waits at least 60 s, keeps monitoring |
| Claim refused (other 403, 400) | Shows the response and **stops** |
| Claim got a timeout or 5xx (outcome unknown) | Reads your profile up to 3 times. **Never** re-sends blindly |
| HTTP 401 | Refreshes the token once. Two in a row stops monitoring |
| 3 claim attempts in one run | Stops, so it can never loop on name changes |

No proxies, no extra accounts, no CAPTCHA handling, no rate-limit evasion of any kind.

## Security and where files live

All app data is stored in `%LOCALAPPDATA%\MCNameClaimer\`:

| File | Contents |
|---|---|
| `credentials.bin` | Microsoft refresh token and current Minecraft access token, **encrypted with Windows DPAPI**. Only your Windows user on this PC can decrypt it. |
| `settings.json` | Client ID (public by design, not a secret), interval, last username, mode |
| `logs\claimer.log` | Every check and claim attempt. Rotates at 1 MB and keeps 5 files |

- Your password is typed only on Microsoft's website (OAuth 2.0 device-code flow).
- Tokens are never passed to the logger. A redaction filter also scrubs anything
  token-shaped from the log file and the on-screen log, as a second line of defence.
- The Minecraft access token lasts 24 hours and is refreshed automatically.

## Limitations

- **Unofficial endpoints.** Mojang doesn't publish third-party documentation for these
  Minecraft Services endpoints. The app follows the community docs, and Mojang can change
  them without notice.
- **Azure approval is required** for Live mode (see above).
- **`AVAILABLE` is not a reservation.** Another player can claim the name between the
  check and your request. Within the rules, nothing prevents that.
- **Released names aren't instant.** When someone changes away from a name, Mojang
  typically holds it for a period (historically about 37 days) before anyone else can take it.
- **Name-change cooldown:** one change per 30 days on your own account.
- **Xbox Game Pass:** the entitlement list can be empty for Game Pass players. The app
  accepts the account as long as it has a Java profile.
- **The PC must stay awake.** Sleep pauses monitoring. If your Microsoft sign-in expires
  or is revoked, the app stops and asks you to sign in again.
- Use it on your own account. Mojang's EULA doesn't allow selling or transferring accounts.

## Simulation scenarios

| Scenario | What it exercises |
|---|---|
| Name frees up, claim succeeds | Normal path: taken x3, available, claim, verify |
| Rate limited (HTTP 429), then success | `Retry-After` handling and backoff |
| Someone else claims it first | 403 `DUPLICATE` on the claim, then monitoring resumes |
| Server error, but the change went through | 503 on the claim, resolved by reading the profile |
| Blocked name (NOT_ALLOWED) | Stops with an explanation |
| Account on 30-day name-change cooldown | Start refused before any check |
| Name never frees up | Schedule, countdown and the Stop button |

## Tests

Double-click **`Run Tests.bat`**, or run:

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The suite makes no network requests. It covers username validation, Retry-After parsing
and backoff, the local rate budget, the API client's error mapping, the full Microsoft,
Xbox and Minecraft sign-in chain (including XSTS errors and the unapproved-app 403),
token refresh and rotation, every monitor and claim branch, all simulation scenarios,
DPAPI encryption, and a check that no token ever reaches the log file. It also drives the
real window in Simulation mode, from Start through to Claimed.

## Troubleshooting

| Message | Fix |
|---|---|
| *Python 3.10 or newer (with tkinter) was not found* | Install Python from python.org with "Add python.exe to PATH" ticked |
| *Allow public client flows* | Azure portal: your app, then **Authentication**. Set *Allow public client flows* to **Yes** |
| *The client ID wasn't found for personal accounts* | Check the ID. *Supported account types* must include personal accounts |
| *Minecraft Services refused this Azure app (HTTP 403)* | Your client ID isn't approved yet. See Live mode step 2 |
| *This Microsoft account has no Xbox profile yet* | Sign in once at xbox.com or in the Minecraft Launcher |
| *Name change: NOT allowed yet* | You changed your name less than 30 days ago |
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
  account.py                 ownership + name-change eligibility checks
  ratelimit.py               Retry-After parsing, backoff, sliding-window budget
  secure_store.py            DPAPI-encrypted credential file
  simulation.py              offline fake of the API with 7 scenarios
  logging_setup.py           rotating log file + secret redaction
  config.py, paths.py, validation.py, errors.py
tests/                       pytest suite (no network)
```
