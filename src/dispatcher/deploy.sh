#!/bin/bash

# Run from within directory

gcloud functions deploy dispatcher \
    --source . \
    --region us-east1 \
    --runtime python314 \
    --trigger-http \
    --allow-unauthenticated \
    --env-vars-file env.yaml \
    --entry-point event