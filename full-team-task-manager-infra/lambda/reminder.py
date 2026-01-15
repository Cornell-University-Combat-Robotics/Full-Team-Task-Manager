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

    mention_str = " ".join([f"<@{u}>" for u in item["targets"]])
    text = f"Reminder: please complete and reaction ✅ for task *{item['task']}* {mention_str}"

    slack_api("chat.postMessage", {
        "channel": item["channelId"],
        "thread_ts": item["messageTs"],
        "text": text
    })
    return {"ok": True}
