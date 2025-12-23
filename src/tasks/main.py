import os
import json
import ydb

YDB_ENDPOINT = os.environ['YDB_ENDPOINT']
YDB_DATABASE = os.environ['YDB_DATABASE']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
TABLE_NAME = os.environ['TABLE_NAME']

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
                tasks.append({
                    "id": str(row["id"]),
                    "name": row.get("name", ""),
                    "url": row.get("url", ""),
                    "created_at": str(row.get("created_at", "")),
                    "status": row.get("status", ""),
                    "pdf": row.get("pdf", ""),
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