import os
import re
import json
import base64
import pickle
import requests
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

# ---- CONFIG ----
PLAYER_ID = 122377
CALENDAR_ID = os.environ["CALENDAR_ID"]
USERNAME = os.environ["USERNAME"]
PASSWORD = os.environ["PASSWORD"]

# ---- SERVICE ACCOUNT AUTH ----
def authenticate_google_service():
    print("🔐 Authenticating with Google Service Account...")
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds)

# ---- LOGIN TO LASN ----

def get_session_cookie():
    print("🧪 Logging in via Playwright...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://register.lasportsnet.com/Account/Login")

        page.fill('input[name="Email"]', USERNAME)
        page.fill('input[name="Password"]', PASSWORD)
        page.click('button[type="submit"]')

        try:
            page.wait_for_url("**/Dashboard", timeout=10000)
            print("✅ Login redirect success")
        except Exception:
            print("⚠️ Timeout waiting for redirect — checking current URL manually:")
            print("📍 Current URL:", page.url)
            print("🧾 Page HTML:")
            print(page.content()[:500])  # print first 500 chars of page
            raise Exception("❌ Login failed or redirected to unexpected page")

        cookies = page.context.cookies()
        session_cookie = next(
            (c["value"] for c in cookies if c["name"] == ".AspNet.ApplicationCookie"),
            None
        )

        browser.close()

        if not session_cookie:
            raise Exception("❌ Failed to retrieve session cookie via Playwright.")

        print("✅ Session cookie acquired via Playwright")
        return session_cookie

# ---- FETCH TEAMS ----
def fetch_teams(player_id, session_cookie):
    url = f"https://register.lasportsnet.com/api/PlayerTeams/GetPlayerTeams?playerID={player_id}&showActiveOnly=true"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": f".AspNet.ApplicationCookie={session_cookie}"
    }
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
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": f".AspNet.ApplicationCookie={session_cookie}"
    }

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
    service = authenticate_google_service()
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
