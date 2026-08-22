#!/bin/bash
if [ "$UPDATE_ON_START" = "1" ]; then
    python3 update.py
fi
exec python3 -m bot
