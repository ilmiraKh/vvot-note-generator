import requests
import boto3
import os
import json
import ydb
import uuid

YDB_ENDPOINT = os.environ['YDB_ENDPOINT']
YDB_DATABASE = os.environ['YDB_DATABASE']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
BUCKET_NAME = os.environ['BUCKET_NAME']
QUEUE = os.environ['QUEUE']
TABLE_NAME = os.environ['TABLE_NAME']

def valid_ya_disk_video_url(video_url: str) -> bool:
    url = "https://cloud-api.yandex.net/v1/disk/public/resources"
    params = {"public_key": video_url}
    try:
        resp = requests.get(url, params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('type') == 'file' and data.get('mime_type', '').startswith('video/')
        else:
            return False
    except Exception:
        return False
    
def download_video(task_id: str, video_url: str) -> str:
    object_name = f"tmp/video/{task_id}.mp4"
    
    url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
    params = {'public_key': video_url}
    response = requests.get(url, params, timeout=10)
    response.raise_for_status()

    link = response.json()['href']

    response = requests.get(link, stream=True, timeout=60)
    response.raise_for_status()
    
    session = boto3.session.Session()
    s3 = session.client(
        service_name='s3',
        endpoint_url="https://storage.yandexcloud.net",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )
    
    s3.upload_fileobj(
        response.raw,
        BUCKET_NAME,
        object_name,
        ExtraArgs={'ContentType': response.headers.get('content-type', 'video/mp4')}
    )

    return object_name

def insert_data(task_id, error=None):
    driver_config = ydb.DriverConfig(
        endpoint=YDB_ENDPOINT,
        database=YDB_DATABASE,
        credentials=ydb.credentials_from_env_variables()
    )
    with ydb.Driver(driver_config) as driver:
        driver.wait(fail_fast=True, timeout=5)

        query = f""" 
            UPDATE `{TABLE_NAME}`
            SET status = $status, error = $error
            WHERE id = $id;
        """
        params = {
            '$id': (uuid.UUID(task_id), ydb.PrimitiveType.UUID),
            '$status': ('ошибка' if error is not None else 'в обработке', ydb.PrimitiveType.Utf8),
            '$error': (error, ydb.OptionalType(ydb.PrimitiveType.Utf8))
        }

        with ydb.QuerySessionPool(driver) as pool:
            pool.execute_with_retries(query, params)


def send_message_to_queue(id, object_name):
    s3_client = boto3.client(
        service_name='sqs',
        endpoint_url='https://message-queue.api.cloud.yandex.net',
        region_name='ru-central1',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    s3_client.send_message(
        QueueUrl=QUEUE,
        MessageBody=json.dumps({"id": str(id), "object_name": object_name})
    )

def handler(event, context):
    try:
        message = json.loads(event['messages'][0]['details']['message']['body'])
        id = message['id']
        video_url = message['video_url']

        if valid_ya_disk_video_url(video_url):
            insert_data(id)
        else:
            insert_data(id, "Невалидная ссылка для скачивания видео")
            return {"message": "ok", 'statusCode': 200}

        object_name = download_video(id, video_url)
        send_message_to_queue(id, object_name)

        return {"message": "ok", 'statusCode': 200}
    except Exception as e:
        return {'statusCode': 500, 'message': str(e)}
    