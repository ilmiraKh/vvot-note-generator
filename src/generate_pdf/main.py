import boto3
import os
import ydb
import json
import io
import uuid
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BUCKET_NAME = os.environ['BUCKET_NAME']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
YDB_ENDPOINT = os.environ['YDB_ENDPOINT']
YDB_DATABASE = os.environ['YDB_DATABASE']
TABLE_NAME = os.environ['TABLE_NAME']

def get_name(id):
    driver_config = ydb.DriverConfig(
        endpoint=YDB_ENDPOINT,
        database=YDB_DATABASE,
        credentials=ydb.credentials_from_env_variables()
    )

    with ydb.Driver(driver_config) as driver:
        driver.wait(fail_fast=True, timeout=5)

        query = f"""
            SELECT name
            FROM `{TABLE_NAME}`
            WHERE id = $id;
        """

        params = {
            '$id': (uuid.UUID(id), ydb.PrimitiveType.UUID),
        }

        with ydb.QuerySessionPool(driver) as pool:
            result = pool.execute_with_retries(query, params)

        if result and result[0].rows:
            return result[0].rows[0]['name']

    raise Exception(f"Lecture name not found for id={id}")

def save_pdf(object_name, id):
    s3 = boto3.client(
        service_name='s3',
        endpoint_url="https://storage.yandexcloud.net",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )

    resp = s3.get_object(Bucket=BUCKET_NAME, Key=object_name)
    text = resp["Body"].read().decode("utf-8")

    name = get_name(id)

    pdf_bytes_io = io.BytesIO()

    pdfmetrics.registerFont(TTFont('DejaVuSans','DejaVuSans.ttf', 'UTF-8'))
    styles = getSampleStyleSheet() 
    styles['Normal'].fontName='DejaVuSans'
    styles['Heading1'].fontName='DejaVuSans'

    
    doc = SimpleDocTemplate(pdf_bytes_io, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []
    
    story.append(Paragraph(name, styles['Heading1']))
    story.append(Spacer(1, 6 * mm))

    for line in text.splitlines():
        if line.strip():
            story.append(Paragraph(line, styles['Normal']))
            story.append(Spacer(1, 2*mm)) 

    doc.build(story)
    pdf_bytes_io.seek(0)

    pdf_object_name = f'{id}.pdf'
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=pdf_object_name,
        Body=pdf_bytes_io.read(),
        ContentType='application/pdf'
    )
    return pdf_object_name

def insert_data(task_id: str, status: str, pdf: str | None = None, error: str | None = None):
    driver_config = ydb.DriverConfig(
        endpoint=YDB_ENDPOINT,
        database=YDB_DATABASE,
        credentials=ydb.credentials_from_env_variables()
    )
    with ydb.Driver(driver_config) as driver:
        driver.wait(fail_fast=True, timeout=5)

        set_parts = ["status = $status"]
        params = {
            '$id': (uuid.UUID(task_id), ydb.PrimitiveType.UUID),
            '$status': (status, ydb.PrimitiveType.Utf8),
        }

        if pdf is not None:
            set_parts.append("pdf = $pdf")
            params['$pdf'] = (pdf, ydb.PrimitiveType.Utf8)

        if error is not None:
            set_parts.append("error = $error")
            params['$error'] = (error[:1000], ydb.PrimitiveType.Utf8)

        query = f"""
            UPDATE `{TABLE_NAME}`
            SET {", ".join(set_parts)}
            WHERE id = $id;
        """

        with ydb.QuerySessionPool(driver) as pool:
            pool.execute_with_retries(query, params)

def handler(event, context):
    message = json.loads(event['messages'][0]['details']['message']['body'])
    task_id = message['id']
    object_name = message['object_name']

    try:
        pdf_object_name = save_pdf(object_name, task_id)
        insert_data(task_id, 'успешно', pdf=pdf_object_name)
    except Exception as e:
        insert_data(task_id, 'ошибка', error='Произошла ошибка при создании PDF-конспекта')