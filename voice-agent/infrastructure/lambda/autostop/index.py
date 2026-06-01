"""Auto-stop the Aegis EC2 instance when it has been idle.

Triggered every 5 minutes by an EventBridge schedule. Looks at the last
IDLE_WINDOW_MIN of CloudWatch CPUUtilization datapoints. If the average is
below CPU_THRESHOLD, the instance is stopped (only if currently running).

Env vars (set by Terraform):
    INSTANCE_ID         the EC2 instance to babysit
    CPU_THRESHOLD       float, e.g. "5.0"  -> stop if avg < 5%
    IDLE_WINDOW_MIN     int, e.g. "30"     -> over the last 30 min
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import boto3

INSTANCE_ID = os.environ["INSTANCE_ID"]
CPU_THRESHOLD = float(os.environ["CPU_THRESHOLD"])
IDLE_WINDOW_MIN = int(os.environ["IDLE_WINDOW_MIN"])

ec2 = boto3.client("ec2")
cloudwatch = boto3.client("cloudwatch")


def _is_running() -> bool:
    res = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    state = res["Reservations"][0]["Instances"][0]["State"]["Name"]
    return state == "running"


def _avg_cpu() -> float | None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=IDLE_WINDOW_MIN)
    res = cloudwatch.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": INSTANCE_ID}],
        StartTime=start,
        EndTime=end,
        Period=300,
        Statistics=["Average"],
    )
    points = res.get("Datapoints", [])
    if not points:
        return None
    return sum(p["Average"] for p in points) / len(points)


def handler(event, context):
    if not _is_running():
        return {"action": "noop", "reason": "instance not running"}

    avg = _avg_cpu()
    if avg is None:
        return {"action": "noop", "reason": "no CPU datapoints yet"}

    if avg < CPU_THRESHOLD:
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        return {
            "action": "stopped",
            "avg_cpu": round(avg, 2),
            "threshold": CPU_THRESHOLD,
        }

    return {
        "action": "noop",
        "reason": "still busy",
        "avg_cpu": round(avg, 2),
        "threshold": CPU_THRESHOLD,
    }
