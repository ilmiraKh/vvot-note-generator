import json
import ydb
import os
import uuid
from datetime import datetime, timezone
import boto3
from urllib.parse import parse_qs

YDB_ENDPOINT = os.environ['YDB_ENDPOINT']
YDB_DATABASE = os.environ['YDB_DATABASE']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
QUEUE = os.environ['QUEUE']
TABLE_NAME = os.environ['TABLE_NAME']

def create(name, video_url):
    driver_config = ydb.DriverConfig(
        endpoint=YDB_ENDPOINT,
        database=YDB_DATABASE,
        credentials=ydb.credentials_from_env_variables()
    )
    with ydb.Driver(driver_config) as driver:
        driver.wait(fail_fast=True, timeout=5)

        query = f""" 
            UPSERT INTO `{TABLE_NAME}` (id, created_at, name, url, status, pdf, error)
            VALUES ($id, $created_at, $name, $url, $status, NULL, NULL);
        """

        task_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)
        params = {
            '$id': (task_id, ydb.PrimitiveType.UUID),
            '$created_at': (created_at, ydb.PrimitiveType.Timestamp), 
            '$name': (name, ydb.PrimitiveType.Utf8),
            '$url': (video_url, ydb.PrimitiveType.Utf8),
            '$status': ('в очереди', ydb.PrimitiveType.Utf8)
        }

        with ydb.QuerySessionPool(driver) as pool:
            pool.execute_with_retries(query, params)

    return task_id

def send_message_to_queue(id, video_url):
    s3_client = boto3.client(
        service_name='sqs',
        endpoint_url='https://message-queue.api.cloud.yandex.net',
        region_name='ru-central1',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    s3_client.send_message(
        QueueUrl=QUEUE,
        MessageBody=json.dumps({"id": str(id), "video_url": video_url})
    )

def handler(event, context):
    try:
        body = event.get("body", "")

        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")

        data = parse_qs(body)
        data = {k: v[0] for k, v in data.items()}
    except Exception:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Некорректные данные"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            }
        }

    name = data.get('name', '').strip()
    video_url = data.get('url', '').strip()

    if not name:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Поле 'name' обязательно"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            }
        }

    if not video_url:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Поле 'url' обязательно"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            }
        }

    try:
        task_id = create(name, video_url)
        send_message_to_queue(task_id, video_url)
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Ошибка при создании задачи", "message": str(e),}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            }
        }

    return {
        "statusCode": 302,
        "headers": {
            "Location": "/tasks"
        }
    }