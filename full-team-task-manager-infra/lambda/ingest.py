import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import boto3
import urllib.request
import urllib.parse
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
scheduler = boto3.client("scheduler")

TASKS_TABLE = os.environ["TASKS_TABLE"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
REMINDER_LAMBDA_ARN = os.environ["REMINDER_LAMBDA_ARN"]
NUDGE_LAMBDA_ARN = os.environ["NUDGE_LAMBDA_ARN"]
SCHEDULER_INVOKE_ROLE_ARN = os.environ["SCHEDULER_INVOKE_ROLE_ARN"]

table = dynamodb.Table(TASKS_TABLE)

NAME_TO_SLACK_ID = {
    "shao": "U047QD6FGD9",
}

NY_TZ = ZoneInfo("America/New_York")

def format_due_ny(due_utc: datetime) -> str:
    due_ny = due_utc.astimezone(NY_TZ)
    return due_ny.strftime("%b %d, %Y at %I:%M %p %Z")

def slack_api(method: str, payload: dict) -> dict:
    # URL parameters are required for 'get' style methods
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

def parse_due_datetime(due_str: str) -> datetime:
    try:
        dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    local_naive = datetime.strptime(due_str, "%Y-%m-%dT%H:%M")
    local_aware = local_naive.replace(tzinfo=NY_TZ)
    return local_aware.astimezone(timezone.utc)

def parse_targets(target: str) -> list[str]:
    names = [t.strip().lower() for t in target.split(",") if t.strip()]
    ids = []
    unknown = []
    for n in names:
        if n in ("channel", "@channel"): ids.append("!channel")
        elif n in ("here", "@here"): ids.append("!here")
        elif n in ("everyone", "@everyone"): ids.append("!everyone")
        elif n in NAME_TO_SLACK_ID: ids.append(NAME_TO_SLACK_ID[n])
        else: unknown.append(n)
    if unknown: raise ValueError(f"Unknown target name(s): {', '.join(unknown)}")
    return ids

def handler(event, context):
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        payload = json.loads(body)

        # Extraction and Defaults
        task = payload["task"].strip()
        description = payload["description"].strip()
        due_at = parse_due_datetime(payload["dueDate"].strip())
        target_raw = payload["target"].strip()
        estimated_time = float(payload.get("estimatedTime") or 1.0)
        comment = payload.get("comment", "").strip()
        link_url = payload.get("linkUrl", "").strip()
        link_text = payload.get("linkText", "").strip()
        remindType = payload.get("remindType", "default").lower()
       
        now = datetime.now(timezone.utc)
        if due_at <= now:
            return _resp(400, {"message": "dueDate must be in the future"})

        targets = parse_targets(target_raw)
        channel_id = os.environ["SLACK_CHANNEL_ID"]
        mention_str = " ".join([f"<{u}>" if u.startswith("!") else f"<@{u}>" for u in targets])
       
        link_line = f"<{link_url}|{link_text}>\n" if link_url and link_text else f"<{link_url}>\n" if link_url else ""
        text = f"{mention_str}\n*Task:* {task} {link_line}\n*Description:* {description}\n*Due:* {format_due_ny(due_at)}\n*Comment:* {comment}\n\nReact with ✅ when done."

        slack_res = slack_api("chat.postMessage", {"channel": channel_id, "text": text})
        message_ts = slack_res["ts"]
        task_id = str(uuid.uuid4())

        table.put_item(Item={
            "taskId": task_id, "task": task, "description": description, "dueAt": due_at.isoformat(),
            "channelId": channel_id, "messageTs": str(message_ts), "targets": targets,
            "createdAt": now.isoformat(), "ttl": int(due_at.timestamp()) + (30 * 86400)
        })

        # Nudge and Reminder Logic
        due_ny = due_at.astimezone(NY_TZ)
        nudge_time = due_ny - timedelta(hours=estimated_time)
       
        # Schedule Nudge
        _create_or_update_schedule(
            name=f"task-{task_id}-nudge",
            schedule_expression=f"at({nudge_time.strftime('%Y-%m-%dT%H:%M:%S')})",
            time_zone='America/New_York',
            target_arn=NUDGE_LAMBDA_ARN,
            payload={"taskId": task_id}
        )

        # Handle Default/Custom reminders
        if remindType == "default":
            # 10 minute repeat if due within 24h
            # if (due_at - now).total_seconds() <= 86400:
            #     _create_or_update_schedule(
            #         name=f"task-{task_id}-remind-10min",
            #         schedule_expression="rate(10 minutes)",
            #         time_zone='America/New_York',
            #         start_time=now, end_time=due_at,
            #         target_arn=REMINDER_LAMBDA_ARN,
            #         payload={"taskId": task_id, "mode": "fast"}
            #     )
            def get_7pm_ny(base_dt):
                return base_dt.replace(hour=19, minute=0, second=0, microsecond=0)

            # 1. 7 PM the Day Before
            day_before_7pm = get_7pm_ny(due_at - timedelta(days=2))
            if now < day_before_7pm < due_at:
                _create_or_update_schedule(
                    name=f"task-{task_id}-remind-7pm-before",
                    schedule_expression=f"at({day_before_7pm.strftime('%Y-%m-%dT%H:%M:%S')})",
                    time_zone='America/New_York',
                    target_arn=REMINDER_LAMBDA_ARN,
                    payload={"taskId": task_id}
                )

            # 2. 7 PM the Day Of
            day_of_7pm = get_7pm_ny(due_at - timedelta(days=1))
            if now < day_of_7pm < due_at:
                _create_or_update_schedule(
                    name=f"task-{task_id}-remind-7pm-of",
                    schedule_expression=f"at({day_of_7pm.strftime('%Y-%m-%dT%H:%M:%S')})",
                    time_zone='America/New_York',
                    target_arn=REMINDER_LAMBDA_ARN,
                    payload={"taskId": task_id}
                )

            # 3. 50% Time Mark with 12am-8am logic
            total_duration = due_at - now
            halfway_time = now + (total_duration / 2)
            halfway_ny = halfway_time

            # If 50% mark falls between 12:00 AM and 7:59 AM, shift to 8:00 AM
            if 0 <= halfway_ny.hour < 8:
                halfway_ny = halfway_ny.replace(hour=8, minute=0, second=0, microsecond=0)
           
            if now < halfway_ny < due_at:
                _create_or_update_schedule(
                    name=f"task-{task_id}-remind-halfway",
                    schedule_expression=f"at({halfway_ny.strftime('%Y-%m-%dT%H:%M:%S')})",
                    time_zone='America/New_York',
                    target_arn=REMINDER_LAMBDA_ARN,
                    payload={"taskId": task_id}
                )
        elif remindType == "custom":
            for rem in payload.get("reminders", []):
                amt = int(rem["amount"])
                unit = rem["unit"]
                delta = timedelta(minutes=amt) if unit == "minutes" else timedelta(hours=amt) if unit == "hours" else timedelta(days=amt)
                rem_time_ny = (due_at - delta)
                if 0 <= rem_time_ny.hour < 8:
                    rem_time_ny = rem_time_ny.replace(hour=8, minute=0, second=0, microsecond=0)
               
                if now < rem_time_ny < due_at:
                    _create_or_update_schedule(
                        name=f"task-{task_id}-remind-{unit}-{amt}",
                        schedule_expression=f"at({rem_time_ny.strftime('%Y-%m-%dT%H:%M:%S')})",
                        time_zone='America/New_York',
                        target_arn=REMINDER_LAMBDA_ARN,
                        payload={"taskId": task_id}
                    )

        return _resp(200, {"taskId": task_id, "messageTs": message_ts})
    except Exception as e:
        return _resp(500, {"message": str(e)})

def _create_or_update_schedule(name, schedule_expression, time_zone, target_arn, payload, start_time=None, end_time=None):
    kwargs = {
        "Name": name, "FlexibleTimeWindow": {"Mode": "OFF"},
        "ScheduleExpression": schedule_expression, "ScheduleExpressionTimezone": time_zone,
        "Target": {"Arn": target_arn, "RoleArn": SCHEDULER_INVOKE_ROLE_ARN, "Input": json.dumps(payload)},
        "State": "ENABLED"
    }
    if start_time: kwargs["StartDate"] = start_time
    if end_time: kwargs["EndDate"] = end_time
    try:
        scheduler.create_schedule(**kwargs)
    except ClientError as ce:
        if ce.response["Error"]["Code"] == "ConflictException": scheduler.update_schedule(**kwargs)
        else: raise

def _resp(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}, "body": json.dumps(body)}