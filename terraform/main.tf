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

    sa_id = yandex_iam_service_account.sa.id
  })
}

output "api_gateway" {
  value = yandex_api_gateway.api.domain
}
