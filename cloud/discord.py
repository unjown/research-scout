import os
import requests


def send_status(message):
    webhook = os.getenv('DISCORD_WEBHOOK_STATUS', '').strip()
    if not webhook:
        print('Status webhook not configured:', message)
        return
    r = requests.post(webhook, json={'content': message[:1900]}, timeout=20)
    r.raise_for_status()
