import os
import json
import ydb
import boto3

BUCKET_NAME = os.environ['BUCKET_NAME']
YDB_ENDPOINT = os.environ['YDB_ENDPOINT']
YDB_DATABASE = os.environ['YDB_DATABASE']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
TABLE_NAME = os.environ['TABLE_NAME']

def generate_presigned_pdf_url(pdf_key: str, name:str, expires_in=600):
    s3 = boto3.client(
        service_name='s3',
        endpoint_url='https://storage.yandexcloud.net',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )

    return s3.generate_presigned_url(
        ClientMethod='get_object',
        Params={
            'Bucket': BUCKET_NAME,
            'Key': pdf_key,
            'ResponseContentDisposition': f'attachment; filename="{name}.pdf"'
        },
        ExpiresIn=expires_in
    )

def get_tasks():
    driver_config = ydb.DriverConfig(
        endpoint=YDB_ENDPOINT,
        database=YDB_DATABASE,
        credentials=ydb.credentials_from_env_variables()
    )
    with ydb.Driver(driver_config) as driver:
        driver.wait(fail_fast=True, timeout=5)

        query = f"""
            SELECT id, name, created_at, url, status, pdf, error
            FROM `{TABLE_NAME}`
            ORDER BY created_at DESC;
        """

        with ydb.QuerySessionPool(driver) as pool:
            result_sets = pool.execute_with_retries(query)
            tasks = []
            for row in result_sets[0].rows:
                pdf_key = row.get("pdf")
                pdf_url = None
                name = row.get("name", "lecture")
                if pdf_key:
                    pdf_url = generate_presigned_pdf_url(pdf_key, name)
                tasks.append({
                    "id": str(row["id"]),
                    "name": name,
                    "url": row.get("url", ""),
                    "created_at": str(row.get("created_at", "")),
                    "status": row.get("status", ""),
                    "pdf": pdf_url,
                    "error": row.get("error", "")
                })
            return tasks

def handler(event, context):
    try:
        tasks = get_tasks()
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"tasks": tasks}, ensure_ascii=False)
        }
    except Exception as e:
        return {'statusCode': 500, 'message': str(e)}