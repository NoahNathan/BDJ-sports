import os
import pickle
import requests
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---- CONFIG ----
PLAYER_ID = 122377
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = os.environ["CALENDAR_ID"]

# ---- LOGIN & COOKIE RETRIEVAL ----
def get_session_cookie():
    session = requests.Session()
    login_url = "https://register.lasportsnet.com/Account/Login"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    # Step 1: Get CSRF token
    resp = session.get(login_url, headers=headers)
    if "__RequestVerificationToken" not in resp.text:
        raise Exception("❌ Unable to load login page")

    import re
    token_match = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', resp.text)
    if not token_match:
        raise Exception("❌ CSRF token not found")
    csrf_token = token_match.group(1)

    # Step 2: Submit login form
    payload = {
        "__RequestVerificationToken": csrf_token,
        "Email": os.environ["USERNAME"],
        "Password": os.environ["PASSWORD"],
        "RememberMe": "false"
    }
    resp = session.post(login_url, data=payload, headers=headers, allow_redirects=True)

    if "Dashboard" not in resp.url:
        raise Exception("❌ Login failed. Check credentials.")

    cookie = session.cookies.get(".AspNet.ApplicationCookie")
    if not cookie:
        raise Exception("❌ Session cookie not found after login")

    return cookie

# ---- GOOGLE CALENDAR AUTH ----
def authenticate_google():
    creds = None
    if os.path.exists('token.pkl'):
        with open('token.pkl', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES, redirect_uri='http://localhost'
            )
            auth_url, _ = flow.authorization_url(prompt='consent')
            print("🔗 Go to this link in your browser:")
            print(auth_url)
            code = input("📥 Paste the auth code here: ")
            flow.fetch_token(code=code)
            creds = flow.credentials
        with open('token.pkl', 'wb') as token:
            pickle.dump(creds, token)
    return creds

# ---- FETCH TEAMS ----
def fetch_teams(player_id, session_cookie):
    url = f"https://register.lasportsnet.com/api/PlayerTeams/GetPlayerTeams?playerID={player_id}&showActiveOnly=true"
    headers = {"User-Agent": "Mozilla/5.0", "Cookie": f".AspNet.ApplicationCookie={session_cookie}"}
    response = requests.get(url, headers=headers)
    teams = []

    if response.ok:
        data = response.json()
        for team in data:
            team_id = team["TeamID"]
            team_name = team["TeamName"]
            season_url = f"https://register.lasportsnet.com/api/teams?id={team_id}"
            season_resp = requests.get(season_url, headers=headers)
            if season_resp.ok:
                try:
                    season_data = season_resp.json()
                    season_id = season_data.get("SeasonID")
                    if season_id:
                        print(f"✅ Found seasonID={season_id} for team '{team_name}'")
                        teams.append({
                            "team_name": team_name,
                            "team_id": team_id,
                            "season_id": season_id
                        })
                except Exception as e:
                    print(f"❌ Error parsing season info for '{team_name}':", e)
    return teams

# ---- FETCH SCHEDULE ----
def fetch_full_schedule(team_id, season_id, session_cookie):
    url = f"https://register.lasportsnet.com/api/games?seasonID={season_id}&teamID={team_id}&isSchedule=true"
    headers = {"User-Agent": "Mozilla/5.0", "Cookie": f".AspNet.ApplicationCookie={session_cookie}"}
    response = requests.get(url, headers=headers)
    games = []
    if response.ok:
        data = response.json()
        for week in data["data"]:
            for game in week["GamesForWeek"]:
                if game["GameDate"] and game["GameTime"]:
                    dt = datetime.strptime(f"{game['GameDate']} {game['GameTime']}", "%m/%d/%Y %I:%M %p")
                    opponent = game["TeamBName"] if game["TeamAID"] == team_id else game["TeamAName"]
                    games.append({
                        "datetime": dt,
                        "opponent": opponent or "TBD",
                        "field": game["Field"],
                        "is_playoff": game["IsPlayoff"],
                        "is_championship": game["IsChampionship"]
                    })
    return games

# ---- CHECK FOR DUPLICATES ----
def event_exists(service, calendar_id, summary, start_time):
    time_min = start_time.isoformat() + "Z"
    time_max = (start_time + timedelta(minutes=1)).isoformat() + "Z"

    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True
        ).execute()

        for event in events_result.get("items", []):
            if summary.lower() in event.get("summary", "").lower():
                return True
        return False
    except Exception as e:
        print("❌ Error checking duplicates:", e)
        return False

# ---- MAIN ----
def main():
    print("🔐 Authenticating with Google...")
    creds = authenticate_google()
    service = build("calendar", "v3", credentials=creds)

    print("🔑 Logging into LASN...")
    session_cookie = get_session_cookie()

    print("📆 Fetching your teams...")
    teams = fetch_teams(PLAYER_ID, session_cookie)
    if not teams:
        print("❌ No active teams found.")
        return

    for team in teams:
        print(f"📅 Getting schedule for {team['team_name']}...")
        games = fetch_full_schedule(team["team_id"], team["season_id"], session_cookie)

        for game in games:
            summary = f"{team['team_name']} vs {game['opponent']}"
            start_dt = game["datetime"]
            end_dt = start_dt + timedelta(hours=1)

            if event_exists(service, CALENDAR_ID, summary, start_dt):
                print(f"⏭️ Skipping duplicate: {summary} on {start_dt}")
                continue

            event = {
                "summary": summary,
                "location": f"Field {game['field']}" if game["field"] else "TBD",
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": "America/Los_Angeles",
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": "America/Los_Angeles",
                },
                "description": "Playoff Game" if game["is_playoff"] else (
                    "Championship Game" if game["is_championship"] else "Regular Season Game"
                ),
            }

            created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            print(f"✅ Added: {summary} on {start_dt.strftime('%A, %b %d at %I:%M %p')}")

if __name__ == "__main__":
    main()
