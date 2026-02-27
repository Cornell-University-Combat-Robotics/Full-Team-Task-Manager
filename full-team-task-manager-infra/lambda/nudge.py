import json, os, urllib.request, urllib.parse
import boto3
from zoneinfo import ZoneInfo
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
TASKS_TABLE = os.environ["TASKS_TABLE"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

table = dynamodb.Table(TASKS_TABLE)
NY_TZ = ZoneInfo("America/New_York")

def format_due_ny(due_iso: str) -> str:
    dt = datetime.fromisoformat(due_iso).astimezone(NY_TZ)
    return dt.strftime("%b %d, %Y at %I:%M %p %Z")

def slack_api(method: str, payload: dict) -> dict:
    if method in ["reactions.get", "chat.getPermalink", "conversations.open"]:
        params = urllib.parse.urlencode(payload)
        url = f"https://slack.com/api/{method}?{params}"
        data = None
        content_type = "application/x-www-form-urlencoded"
    else:
        url = f"https://slack.com/api/{method}"
        data = json.dumps(payload).encode("utf-8")
        content_type = "application/json; charset=utf-8"

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {SLACK_BOT_TOKEN}")
    req.add_header("Content-Type", content_type)
    
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
    out = json.loads(body)
    if not out.get("ok"):
        raise RuntimeError(f"Slack API error {method}: {out}")
    return out

def dm_user(user_id: str, text: str):
    conv = slack_api("conversations.open", {"users": user_id})
    channel_id = conv["channel"]["id"]
    slack_api("chat.postMessage", {"channel": channel_id, "text": text})

def handler(event, context):
    task_id = event["taskId"]
    item = table.get_item(Key={"taskId": task_id}).get("Item")
    if not item:
        return {"ok": True, "skipped": "task not found"}
    
    # Fix Timestamp format (ensure decimal dot exists)
    ts = str(item["messageTs"])
    formatted_ts = f"{ts[:10]}.{ts[10:]}" if "." not in ts else ts

    # Get current reactions
    try:
        msg_data = slack_api("reactions.get", {
            "channel": item["channelId"],
            "timestamp": formatted_ts,
            "full": True
        })
    except Exception as e:
        print(f"Failed to get reactions: {e}")
        return {"ok": False, "error": str(e)}

    reacted_users = set()
    reactions = msg_data.get("message", {}).get("reactions", [])
    for r in reactions:
        if r.get("name") == "white_check_mark":
            reacted_users.update(r.get("users", []))

    # Identify missing users (exclude channel wide mentions like !channel)
    # targets = item.get("targets", [])
    targets = ['U048E6QP8C8', 'U047HT1JFAA', 'U009RRJMTG6S', 'U0622T7AY3Y', 'U0806AWTK42', 'U0629DTASNP', 'U062N61J125', 'U0806AX3ANN', 'U09SMUVBRQQ', 'U09RM870LH1', 'U0626JXR23X', 'U0629GL6B26', 'U09S6H8RLFK', 'U0808RWA0GL', 'U047HT1KUVC',  'U0629GKTWJW', 'U080K1N1801', 'U07VDN683K8', 'U09S6H7BM4HU', 'U047QD6FGD9',  'U07V5QHS18FU', 'U07VDN6310EU', 'U09RRJP2C78', 'U0808RWEEDS', 'U0629DV3STV', 'U0806AWQX1QU', 'U0803FC1G8MU', 'U0629DUCJAF', 'U0626JWR52RU', 'U047MGYRZ61', 'U047SV1E5C4', 'U062N63DNL9', 'U061V0VDMHVU', 'U062YGF5Q0YU', 'U047QD6DNJX',  'U08068A2T43', 'U0479UV8J3HU', 'U080V4Z3MR6', 'U09RTL1RPRQU', 'U09RC6UMF2TU', 'U09RQ6NKE2ZU', 'U09RM877WUT', 'U09RRJRD7N2', 'U09RC6NTEQPU', 'U09RM89FAEPU', 'U09S6H5EDA5', 'U09S6H8LATBU', 'U09RRJVHUB0U', 'U09RX74D276']
    missing = [u for u in targets if u not in reacted_users and not u.startswith("!")]

    if not missing:
        return {"ok": True, "message": "All assigned users have reacted."}

    # Nudge missing users
    due_str = format_due_ny(item["dueAt"])
    for u in missing:
        try:
            dm_user(u, f"Final Reminder! You haven't completed ✅ task: *{item['task']}*.\nIt is due: {due_str}\n\nPlease ignore this messsge if you have completed the task and forget to react with ✅")
        except Exception as e:
            print(f"Failed to DM {u}: {e}")

    return {"ok": True, "nudge_sent_to": missing}