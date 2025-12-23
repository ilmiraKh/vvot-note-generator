terraform {
  required_providers {
    yandex = {
      source = "yandex-cloud/yandex"
    }
  }

  required_version = ">= 0.13"
}

provider "yandex" {
  zone      = "ru-central1-d"
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
}

resource "yandex_iam_service_account" "sa" {
  name = "serviceaccount"
  folder_id = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "sa_roles" {
  for_each = toset([
    "functions.editor",
    "storage.editor",
    "ymq.admin",
    "ydb.editor",
    "api-gateway.editor"
  ])

  folder_id = var.folder_id
  role      = each.key
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

resource "yandex_iam_service_account_static_access_key" "sa_static_key" {
  service_account_id = yandex_iam_service_account.sa.id
}

resource "yandex_ydb_database_serverless" "ydb" {
  name      = "${var.prefix}-ydb"
  folder_id = var.folder_id
}

resource "yandex_ydb_table" "tasks_table" {
  path              = "${var.prefix}-tasks"
  connection_string = yandex_ydb_database_serverless.ydb.ydb_full_endpoint

  column {
    name     = "created_at"
    type     = "Timestamp"
    not_null = true
  }
  column {
    name     = "id"
    type     = "UUID"
    not_null = true
  }
  column {
    name     = "name"
    type     = "Utf8"
    not_null = false
  }
  column {
    name     = "url"
    type     = "Utf8"
    not_null = false
  }
  column {
    name     = "status"
    type     = "Utf8"
    not_null = true
  }
  column {
    name     = "pdf"
    type     = "Utf8"
    not_null = false
  }
  column {
    name     = "error"
    type     = "Utf8"
    not_null = false
  }
  primary_key = ["id"]
}

resource "yandex_storage_bucket" "bucket" {
  bucket     = "${var.prefix}-bucket"
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
  depends_on = [yandex_resourcemanager_folder_iam_member.sa_roles]

  lifecycle_rule {
    id      = "clean"
    enabled = true

    expiration {
      days = 1
    }
    
    filter {
      prefix = "tmp/"
    }
  }

  force_destroy = true
}

resource "yandex_storage_object" "form" {
  bucket       = yandex_storage_bucket.bucket.bucket
  key          = "form.html"
  source       = "../src/html/form.html"
  content_type = "text/html"
}

resource "yandex_storage_object" "tasks" {
  bucket       = yandex_storage_bucket.bucket.bucket
  key          = "tasks.html"
  source       = "../src/html/tasks.html"
  content_type = "text/html"
}

resource "yandex_api_gateway" "api" {
  name      = "${var.prefix}-api-gateway"
  folder_id = var.folder_id

  spec = templatefile("./api.yaml", {
    api_name = "${var.prefix}-api"

    bucket      = yandex_storage_bucket.bucket.bucket
    form_object_key = yandex_storage_object.form.key
    tasks_object_key = yandex_storage_object.tasks.key

    create_function_id = yandex_function.create_func.id

    sa_id = yandex_iam_service_account.sa.id
  })
}

output "api_gateway" {
  value = yandex_api_gateway.api.domain
}

resource "yandex_message_queue" "dlq" {
  name = "${var.prefix}-dlq"
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}

data "archive_file" "dlq_func_zip" {
  type        = "zip"
  output_path = "dlq_func.zip"
  source_dir  = "../src/error"
}

resource "yandex_function_trigger" "dlq_trigger" {
  name      = "${var.prefix}-dlq-trigger"
  folder_id = var.folder_id

  message_queue {
    queue_id           = yandex_message_queue.dlq.arn
    batch_cutoff       = 2
    batch_size         = 1
    service_account_id = yandex_iam_service_account.sa.id
  }

  function {
    id                 = yandex_function.dlq_func.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

resource "yandex_function" "dlq_func" {
  name               = "${var.prefix}-dlq"
  user_hash          = data.archive_file.dlq_func_zip.output_sha256
  runtime            = "python311"
  entrypoint         = "main.handler"
  memory             = 128
  execution_timeout  = 30
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id

  environment = {
    YDB_ENDPOINT          = "grpcs://${yandex_ydb_database_serverless.ydb.ydb_api_endpoint}"
    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    TABLE_NAME = yandex_ydb_table.tasks_table.path

    AWS_ACCESS_KEY_ID = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
  }

  content {
    zip_filename = data.archive_file.dlq_func_zip.output_path
  }
}

data "archive_file" "create_zip" {
  type        = "zip"
  output_path = "create.zip"
  source_dir  = "../src/create"
}

resource "yandex_function" "create_func" {
  name               = "${var.prefix}-create"
  user_hash          = data.archive_file.create_zip.output_sha256
  runtime            = "python311"
  entrypoint         = "main.handler"
  memory             = 512
  execution_timeout  = 60
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id

  environment = {
    AWS_ACCESS_KEY_ID = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    YDB_ENDPOINT          = "grpcs://${yandex_ydb_database_serverless.ydb.ydb_api_endpoint}"
    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    QUEUE = data.yandex_message_queue.download_lecture_queue.url
    TABLE_NAME = yandex_ydb_table.tasks_table.path
  }

  content {
    zip_filename = data.archive_file.create_zip.output_path
  }
}

resource "yandex_message_queue" "download_lecture_queue" {
  name                       = "${var.prefix}-download-lecture"
  visibility_timeout_seconds = 3600
  receive_wait_time_seconds  = 20
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key

  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

data "yandex_message_queue" "download_lecture_queue" {
  name       = yandex_message_queue.download_lecture_queue.name
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}

resource "yandex_function_trigger" "download_lecture_trigger" {
  name      = "${var.prefix}-download-lecture"
  folder_id = var.folder_id

  message_queue {
    queue_id           = yandex_message_queue.download_lecture_queue.arn
    batch_cutoff       = 2
    batch_size         = 1
    service_account_id = yandex_iam_service_account.sa.id
  }

  function {
    id                 = yandex_function.download_lecture_func.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

data "archive_file" "download_lecture_zip" {
  type        = "zip"
  output_path = "download_lecture.zip"
  source_dir  = "../src/download_lecture"
}

resource "yandex_function" "download_lecture_func" {
  name               = "${var.prefix}-download-lecture"
  user_hash          = data.archive_file.download_lecture_zip.output_sha256
  runtime            = "python311"
  entrypoint         = "main.handler"
  memory             = 512
  execution_timeout  = 60
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id

  environment = {
    BUCKET_NAME = yandex_storage_bucket.bucket.bucket

    AWS_ACCESS_KEY_ID = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    
    YDB_ENDPOINT          = "grpcs://${yandex_ydb_database_serverless.ydb.ydb_api_endpoint}"
    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    TABLE_NAME = yandex_ydb_table.tasks_table.path

    QUEUE = data.yandex_message_queue.extract_audio_queue.url
  }

  content {
    zip_filename = data.archive_file.download_lecture_zip.output_path
  }
}

resource "yandex_message_queue" "extract_audio_queue" {
  name                       = "${var.prefix}-extract-audio"
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key

  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

data "yandex_message_queue" "extract_audio_queue" {
  name       = yandex_message_queue.extract_audio_queue.name
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}

resource "yandex_function_trigger" "extract_audio_trigger" {
  name      = "${var.prefix}-extract-audio"
  folder_id = var.folder_id

  message_queue {
    queue_id           = yandex_message_queue.extract_audio_queue.arn
    batch_cutoff       = 2
    batch_size         = 1
    service_account_id = yandex_iam_service_account.sa.id
  }

  function {
    id                 = yandex_function.extract_audio_func.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

data "archive_file" "extract_audio_zip" {
  type        = "zip"
  output_path = "extract_audio.zip"
  source_dir  = "../src/extract_audio"
}

resource "yandex_storage_object" "extract_audio_zip_object" {
  bucket = yandex_storage_bucket.bucket.bucket
  key    = "extract_audio.zip"
  source = data.archive_file.extract_audio_zip.output_path
}

resource "yandex_function" "extract_audio_func" {
  name               = "${var.prefix}-extract-audio"
  user_hash          = data.archive_file.extract_audio_zip.output_sha256
  runtime            = "bash-2204"
  entrypoint         = "main.sh"
  memory             = 512
  execution_timeout  = 60
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id

  environment = {
    QUEUE = data.yandex_message_queue.recognize_audio_queue.url

    AWS_ACCESS_KEY_ID = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    
    BUCKET_NAME = yandex_storage_bucket.bucket.bucket

    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    TABLE_NAME = yandex_ydb_table.tasks_table.path
  }

  package {
    bucket_name = yandex_storage_bucket.bucket.bucket
    object_name = yandex_storage_object.extract_audio_zip_object.key
  }
}

resource "yandex_message_queue" "recognize_audio_queue" {
  name                       = "${var.prefix}-recognize-audio"
  visibility_timeout_seconds = 3600
  receive_wait_time_seconds  = 20
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key

  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

data "yandex_message_queue" "recognize_audio_queue" {
  name       = yandex_message_queue.recognize_audio_queue.name
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}
