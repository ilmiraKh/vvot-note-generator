import os
import json
import ydb
import uuid

YDB_ENDPOINT = os.environ['YDB_ENDPOINT']
YDB_DATABASE = os.environ['YDB_DATABASE']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
TABLE_NAME = os.environ['TABLE_NAME']

def error(task_id, error):
    driver_config = ydb.DriverConfig(
        endpoint=YDB_ENDPOINT,
        database=YDB_DATABASE,
        credentials=ydb.credentials_from_env_variables()
    )
    with ydb.Driver(driver_config) as driver:
        driver.wait(fail_fast=True, timeout=5)

        query = f""" 
            UPDATE `{TABLE_NAME}`
            SET status = 'ошибка', error = $error
            WHERE id = $id AND status != 'ошибка';
        """
        params = {
            '$id': (uuid.UUID(task_id), ydb.PrimitiveType.UUID),
            '$error': (error, ydb.PrimitiveType.Utf8)
        }

        with ydb.QuerySessionPool(driver) as pool:
            pool.execute_with_retries(query, params)

def handler(event, context):
    try:
        message = json.loads(event['messages'][0]['details']['message']['body'])
        id = message['id']

        error(id, "Произошла ошибка при обработке видео")
        
        return {"message": "ok", 'statusCode': 200}
    except Exception as e:
        return {'statusCode': 500, 'message': str(e)}