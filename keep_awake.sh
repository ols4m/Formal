#!/bin/bash
# Prevents the Codespace from going idle by touching a file every 4 minutes
echo "Keep-awake started. Press Ctrl+C to stop."
while true; do
    touch /tmp/.keepalive
    sleep 240
done
