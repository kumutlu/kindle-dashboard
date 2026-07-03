#!/bin/bash
cd /home/user/kindle4-weather-display
export PIHOLE_BASE="http://192.168.68.167"
export PIHOLE_PASSWORD='dERfaVhi'
python3 weather_image.py
