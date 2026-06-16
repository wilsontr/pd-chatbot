#!/bin/bash
set -e
cd /Users/trevorwilson/git/pd-chatbot
venv/bin/python chunk.py > /tmp/chunk_run.log 2>&1
echo "exit: $?" >> /tmp/chunk_run.log
