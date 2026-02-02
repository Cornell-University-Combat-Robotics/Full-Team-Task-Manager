import json, os, urllib.request
import boto3

dynamodb = boto3.resource("dynamodb")
TASKS_TABLE = os.environ["TASKS_TABLE"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

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

def dm_user(user_id: str, text: str):
    conv = slack_api("conversations.open", {
        "users": user_id
    })
    channel_id = conv["channel"]["id"]

    slack_api("chat.postMessage", {
        "channel": channel_id,
        "text": text
    })

def handler(event, context):
    task_id = event["taskId"]
    table = dynamodb.Table(TASKS_TABLE)
    item = table.get_item(Key={"taskId": task_id}).get("Item")
    if not item:
        return {"ok": True, "skipped": "task not found"}

    ## Post in the original channel
    # Format mentions: channel mentions start with !, regular users don't
    mention_str = " ".join([f"<{u}>" if u.startswith("!") else f"<@{u}>" for u in item["targets"]])
    link = item.get("permalink", "")
    text = f"Reminder: please complete and react ✅ for task *{item['task']}* {mention_str}\n{link}"

    slack_api("chat.postMessage", {
        "channel": item["channelId"],
        "text": text
    })
    return {"ok": True}
