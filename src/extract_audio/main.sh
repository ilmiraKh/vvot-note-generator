#!/bin/bash

set -e

event=$(cat)
message=$(echo "$event" | jq -r '.messages[0].details.message.body')
id=$(echo "$message" | jq -r '.id')
object_name=$(echo "$message" | jq -r '.object_name')

mkdir -p /tmp/video /tmp/audio

video="/tmp/video/$id"
yc storage s3api get-object \
    --bucket "$BUCKET_NAME" \
    --key "$object_name" \
    "$video" 

audio="/tmp/audio/$id"
ffmpeg -i "$video" -vn -f mpeg -c:a libmp3lame -q:a 6 "$audio"

audio_object_name="tmp/audio/$id"
yc storage s3api put-object \
    --body "$audio" \
    --bucket "$BUCKET_NAME" \
    --key "$audio_object_name" \
    --content-type "audio/mpeg" 

rm -f "$video" "$audio"

message="{\"id\":\"$id\",\"object_name\":\"$audio_object_name\"}"

curl \
    --request POST \
    --header 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'Action=SendMessage' \
    --data-urlencode "MessageBody=$message" \
    --data-urlencode "QueueUrl=$QUEUE" \
    --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
    --aws-sigv4 'aws:amz:ru-central1:sqs' \
    https://message-queue.api.cloud.yandex.net/

echo "Message sent to queue"