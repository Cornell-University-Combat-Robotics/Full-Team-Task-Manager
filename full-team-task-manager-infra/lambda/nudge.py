import json, os, urllib.request
import boto3
from zoneinfo import ZoneInfo
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
TASKS_TABLE = os.environ["TASKS_TABLE"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

NY_TZ = ZoneInfo("America/New_York")

def format_due_ny(due_utc: datetime) -> str:
    due_ny = due_utc.astimezone(NY_TZ)
    return due_ny.strftime("%b %d, %Y at %I:%M %p %Z")

def slack_api(method: str, payload: dict) -> dict:
    url = f"https://slack.com/api/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {SLACK_BOT_TOKEN}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
    out = json.loads(body)
    if not out.get("ok"):
        raise RuntimeError(f"Slack API error {method}: {out}")
    return out

def handler(event, context):
    task_id = event["taskId"]
    table = dynamodb.Table(TASKS_TABLE)
    item = table.get_item(Key={"taskId": task_id}).get("Item")
    if not item:
        return {"ok": True, "skipped": "task not found"}

    # Fetch reactions on the original message
    msg = slack_api("reactions.get", {
        "channel": item["channelId"],
        "timestamp": item["messageTs"],
        "full": True,
    })

    reacted_users = set()
    message = msg.get("message", {})
    for r in message.get("reactions", []):
        if r.get("name") == "white_check_mark":
            reacted_users.update(r.get("users", []))

    # Filter out channel mentions (!channel, !here, !everyone) as they can't react
    user_targets = [u for u in item["targets"] if not u.startswith("!")]
    missing = [u for u in user_targets if u not in reacted_users]

    due_at = datetime.fromisoformat(item["dueAt"]) 
    for u in missing:
        slack_api("chat.postMessage", {
            "channel": u,  # DM by user ID works if bot has permission; otherwise open a conversation
            "text": f"You haven’t completed ✅ task *{item['task']}* due {format_due_ny(due_at)}. Please complete the task ASAP."
        })

    return {"ok": True, "missing": missing}
