import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo 
import boto3
import urllib.request

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
scheduler = boto3.client("scheduler")

TASKS_TABLE = os.environ["TASKS_TABLE"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
REMINDER_LAMBDA_ARN = os.environ["REMINDER_LAMBDA_ARN"]
NUDGE_LAMBDA_ARN = os.environ["NUDGE_LAMBDA_ARN"]
SCHEDULER_INVOKE_ROLE_ARN = os.environ["SCHEDULER_INVOKE_ROLE_ARN"]

NAME_TO_SLACK_ID = {
    "shao": "U047QD6FGD9",
}

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

def parse_due_datetime(due_str: str) -> datetime:
    """
    Interpret datetime-local input as America/New_York time, then return UTC-aware datetime.
    Accepts:
      - "YYYY-MM-DDTHH:mm" (from <input type="datetime-local">)
      - ISO with timezone (e.g., "2026-01-15T14:30:00-05:00" or "...Z")
    """
    # If user provides timezone explicitly, respect it
    try:
        dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    # Otherwise treat as NY local time
    local_naive = datetime.strptime(due_str, "%Y-%m-%dT%H:%M")
    local_aware = local_naive.replace(tzinfo=NY_TZ)
    return local_aware.astimezone(timezone.utc)

def parse_targets(target: str) -> list[str]:
    names = [t.strip().lower() for t in target.split(",") if t.strip()]
    ids = []
    unknown = []
    for n in names:
        # Handle special channel mentions
        if n in ("channel", "@channel"):
            ids.append("!channel")
        elif n in ("here", "@here"):
            ids.append("!here")
        elif n in ("everyone", "@everyone"):
            ids.append("!everyone")
        elif n in NAME_TO_SLACK_ID:
            ids.append(NAME_TO_SLACK_ID[n])
        else:
            unknown.append(n)
    if unknown:
        raise ValueError(f"Unknown target name(s): {', '.join(unknown)}")
    return ids

def handler(event, context):
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        payload = json.loads(body)

        task = payload["task"].strip()
        description = payload["description"].strip()
        due_date_raw = payload["dueDate"].strip()
        target_raw = payload["target"].strip()
        remindType = payload["remindType"]
        if remindType == "Custom":
            reminders = payload["reminders"]

        due_at = parse_due_datetime(due_date_raw)
        now = datetime.now(timezone.utc)
        if due_at <= now:
            return _resp(400, {"message": "dueDate must be in the future"})

        targets = parse_targets(target_raw)
        if not targets:
            return _resp(400, {"message": "target must include at least one Slack user ID"})

        task_id = str(uuid.uuid4())
        channel_id = os.environ["SLACK_CHANNEL_ID"]  # set this as env var, or choose based on form

        # Format mentions: special channel mentions start with !, regular users with @
        mention_str = " ".join([f"<!{u}>" if u.startswith("!") else f"<@{u}>" for u in targets])
        due_ny = due_at.astimezone(NY_TZ)

        text = (
            f"*New task:* {task}\n"
            f"*Description:* {description}\n"
            f"*Due:* {format_due_ny(due_at)}\n"
            f"*People:* {mention_str}\n\n"
            f"Please react with ✅ for completion."
        )

        slack_res = slack_api("chat.postMessage", {"channel": channel_id, "text": text})
        message_ts = slack_res["ts"]

        permalink = None
        try: 
            permalink = slack_api("chat.getPermalink", {
                "channel": channel_id,
                "message_ts": message_ts,
            })["permalink"]
        except Exception as e:
            print(f"Warning: failed to get permalink: {e}")
            permalink = ""
        table = dynamodb.Table(TASKS_TABLE)
        table.put_item(Item={
            "taskId": task_id,
            "task": task,
            "description": description,
            "dueAt": due_at.isoformat(),
            "channelId": channel_id,
            "messageTs": message_ts,
            "targets": targets,
            "createdAt": now.isoformat(),
            "permalink": permalink,
            # optional TTL: delete 30 days after due
            "ttl": int(due_at.timestamp()) + 30 * 24 * 3600,
        })

        if remindType == "Default":
            # Create schedules:
            # A) if due within 24h => recurring 5-min reminders until due
            seconds_until_due = (due_at - now).total_seconds()
            if seconds_until_due <= 24 * 3600:
                _create_or_update_schedule(
                    name=f"task-{task_id}-remind-5min",
                    schedule_expression="rate(5 minutes)",
                    start_time=now,
                    end_time=due_at,
                    target_arn=REMINDER_LAMBDA_ARN,
                    payload={"taskId": task_id, "mode": "fast"},
                )

            # B) one-time nudge check at due time (or due + 5 min grace)
            nudge_time = due_at
            _create_or_update_schedule(
                name=f"task-{task_id}-nudge",
                schedule_expression=f"at({nudge_time.strftime('%Y-%m-%dT%H:%M:%S')})",
                target_arn=NUDGE_LAMBDA_ARN,
                payload={"taskId": task_id},
            )

            return _resp(200, {"taskId": task_id, "messageTs": message_ts})

        elif remindType == "Custom":
            # Check unit for reminder
            for reminder in reminders:
                if reminder["unit"] == "minutes":
                    remindTime = due_ny - timedelta(minutes=reminder["amount"])
                elif reminder["unit"] == "hours":
                    remindTime = due_ny - timedelta(hours=reminder["amount"])
                elif reminder["unit"] == "days":
                    remindTime = due_ny - timedelta(days=reminder["amount"])
                elif reminder["unit"] == "weeks":
                    remindTime = due_ny - timedelta(weeks=reminder["amount"])

                atTime = f"at({remindTime.strftime('%Y-%m-%dT%H:%M:%S')})"

                _create_or_update_schedule(
                    name=f"task-{task_id}-remind-{reminder['unit']}-{reminder['amount']}",
                    schedule_expression=atTime,
                    target_arn=REMINDER_LAMBDA_ARN,
                    payload={"taskId": task_id},
                )
            return _resp(200, {"taskId": task_id, "messageTs": message_ts})



    except KeyError as e:
        return _resp(400, {"message": f"Missing field: {str(e)}"})
    except Exception as e:
        return _resp(500, {"message": str(e)})

def _create_or_update_schedule(name: str, schedule_expression: str, target_arn: str, payload: dict,
                               start_time: datetime | None = None, end_time: datetime | None = None):
    kwargs = {
        "Name": name,
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "ScheduleExpression": schedule_expression,
        "Target": {
            "Arn": target_arn,
            "RoleArn": SCHEDULER_INVOKE_ROLE_ARN,
            "Input": json.dumps(payload),
        },
        "State": "ENABLED",
    }
    if start_time:
        kwargs["StartDate"] = start_time
    if end_time:
        kwargs["EndDate"] = end_time

    try:
        scheduler.create_schedule(**kwargs)
    except ClientError as ce:
        if ce.response["Error"]["Code"] in ("ConflictException",):
            scheduler.update_schedule(**kwargs)
        else:
            raise

def _resp(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
