# FINAL main.py (always delete all events before sync)

import os
import json
import requests
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---- CONFIG ----
PLAYER_ID = 122377
CALENDAR_ID = os.environ["CALENDAR_ID"]
SESSION_COOKIE = os.environ["SESSION_COOKIE"]

# ---- GOOGLE SERVICE ACCOUNT AUTH ----
def authenticate_google_service():
    print("🔐 Authenticating with Google Service Account...")
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds)

# ---- DELETE ALL EVENTS ----
def delete_all_events(service, calendar_id):
    print("🧨 Deleting ALL events in the calendar...")
    page_token = None
    while True:
        events = service.events().list(
            calendarId=calendar_id,
            pageToken=page_token
        ).execute()

        for event in events.get('items', []):
            try:
                service.events().delete(calendarId=calendar_id, eventId=event['id']).execute()
                print(f"❌ Deleted: {event.get('summary')}")
            except Exception as e:
                print(f"⚠️ Could not delete event: {e}")

        page_token = events.get('nextPageToken')
        if not page_token:
            break

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

# ---- MAIN ----
def main():
    service = authenticate_google_service()
    session_cookie = SESSION_COOKIE

    # Delete all calendar events every time
    delete_all_events(service, CALENDAR_ID)

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
                )
            }

            created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            print(f"✅ Added: {summary} on {start_dt.strftime('%A, %b %d at %I:%M %p')}")

if __name__ == "__main__":
    main()
