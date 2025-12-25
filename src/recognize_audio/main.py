import json
import os
import requests
import boto3
import io

BUCKET_NAME = os.environ['BUCKET_NAME']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
CUR_QUEUE = os.environ['CUR_QUEUE']
FOLDER_ID = os.environ['FOLDER_ID']
API_KEY = os.environ['API_KEY']
NEXT_QUEUE = os.environ['NEXT_QUEUE']

def start_recognition(object_name):
    object_url = f"https://storage.yandexcloud.net/{BUCKET_NAME}/{object_name}"
    api_url = 'https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync'
    params = {
        "uri": object_url,
        "recognitionModel": {
            "model": "general",
            "audioFormat": {
                "containerAudio": {
                    "containerAudioType": "MP3"
                }
            },
            "textNormalization": {
                "textNormalization": "TEXT_NORMALIZATION_ENABLED",
                "profanityFilter": False,
                "literatureText": True,
                "phoneFormattingMode": "PHONE_FORMATTING_MODE_DISABLED"
            },
            "languageRestriction": {
                "restrictionType": "WHITELIST",
                "languageCode": [
                    "ru-RU"
                ]
            },
        },
        "summarization": {
            "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/rc",
            "properties": [
            {
                "instruction": """У тебя есть текст транскрипта лекции. 
                Сделай по нему подробный конспект, соблюдая следующие правила: 
                1. Конспект должен быть структурирован: разделы, подпункты.
                2. Выделяй ключевые идеи, важные факты и определения.
                3. Если есть примеры или пояснения, укажи их кратко в скобках""",
                "jsonObject": True,
            }
            ]
        }
    }

    headers = {
        "Authorization": f"Api-key {API_KEY}",
        "x-folder-id": FOLDER_ID
    }

    response = requests.post(api_url, headers=headers, json=params)
    response.raise_for_status()
    result = response.json()
    return result.get('id')

def json_to_md(data, level=1):
    md = []
    for k, v in data.items():
        md.append("#" * level + " " + k)
        if isinstance(v, dict):
            md.append(json_to_md(v, level + 1))
        else:
            md.append(str(v))
    return "\n".join(md)

def check_recognition(operation_id):
    url = f"https://stt.api.cloud.yandex.net/stt/v3/getRecognition"

    params = {
        "operationId": operation_id
    }

    headers = {
        "Authorization": f"Api-key {API_KEY}",
        "x-folder-id": FOLDER_ID
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        if not response.content:
            return {"done": False}
        
        result = json.loads(response.text.splitlines()[-1])
        summary_str = result["result"]["summarization"]["results"][0]["response"]
        summary_json = json.loads(summary_str)

        return {"done":True, 'text': json_to_md(summary_json)}

    except requests.HTTPError as e:
        if response.status_code == 404:
            return {"done": False}
        else:
            raise

def send_message_to_queue(message, queue, delay, delay_bool):
    s3_client = boto3.client(
        service_name='sqs',
        endpoint_url='https://message-queue.api.cloud.yandex.net',
        region_name='ru-central1',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    if delay_bool:
        s3_client.send_message(
            QueueUrl=queue,
            MessageBody=json.dumps(message),
            DelaySeconds=int(min(delay, 15 * 60))
        )
    else:
        s3_client.send_message(
            QueueUrl=queue,
            MessageBody=json.dumps(message),
        )

def save_text(text, id):
    object_name = f"tmp/raw_text/{id}"
    s3 = boto3.client(
        service_name='s3',
        endpoint_url="https://storage.yandexcloud.net",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )

    file_obj = io.BytesIO(text.encode('utf-8'))

    s3.upload_fileobj(
        file_obj,
        BUCKET_NAME,
        object_name,
        ExtraArgs={'ContentType': 'text/markdown'}
    )

    return object_name

def parse_duration(duration_str):
    h, m, s = duration_str.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def handler(event, context):
    try:
        message = json.loads(event['messages'][0]['details']['message']['body'])
        id = message['id']
        object_name = message['object_name']
        operation_id = message.get('operation_id')

        duration = parse_duration(message.get('duration'))
        delay = duration // 6 + 30

        if not operation_id:
            operation_id = start_recognition(object_name)
            message['operation_id'] = operation_id

        result = check_recognition(operation_id)
        if not result.get('done', False):
            send_message_to_queue(message, CUR_QUEUE, delay, True)
            return {"statusCode": 200, "message": "Recognition pending"}

        text = result['text']
        object_name = save_text(text, id)
        message = {'id': id, 'object_name': object_name}
        send_message_to_queue(message, NEXT_QUEUE, 0, False)

        return {"message": "ok", 'statusCode': 200}
    except Exception as e:
        print(str(e))
        return {'statusCode': 500, 'message': str(e)}