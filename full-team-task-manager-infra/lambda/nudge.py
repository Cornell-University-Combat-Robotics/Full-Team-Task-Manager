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

    missing = [u for u in item["targets"] if u not in reacted_users]
    for u in missing:
        slack_api("chat.postMessage", {
            "channel": u,  # DM by user ID works if bot has permission; otherwise open a conversation
            "text": f"You haven’t completed ✅ task *{item['task']}* due {item['dueAt']}. Please complete the task ASAP."
        })

    return {"ok": True, "missing": missing}
