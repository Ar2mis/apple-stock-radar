"""Notification adapters. Dry runs never build or transmit secret-bearing requests."""
import re
from .config import RadarError
from .http import https_url

REQUIRED = {
    'bark': ('BARK_KEY',), 'telegram': ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'),
    'pushover': ('PUSHOVER_API_TOKEN', 'PUSHOVER_USER_KEY'),
    'discord': ('DISCORD_WEBHOOK_URL',), 'ntfy': ('NTFY_TOPIC',),
}


class Notifier:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client

    def validate(self):
        if not self.settings.channels:
            raise RadarError('Choose at least one notification channel')
        for channel in self.settings.channels:
            for name in REQUIRED[channel]:
                if not self.settings.env.get(name):
                    raise RadarError(f'Missing {name}')
        e = self.settings.env
        if 'discord' in self.settings.channels:
            https_url(e['DISCORD_WEBHOOK_URL'])
            if not re.fullmatch(r'https://(?:discord.com|discordapp.com)/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+', e['DISCORD_WEBHOOK_URL']):
                raise RadarError('DISCORD_WEBHOOK_URL must be an official Discord webhook URL')
        if 'bark' in self.settings.channels:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', e['BARK_KEY']):
                raise RadarError('BARK_KEY must contain only the device key, not a URL')
            https_url(e.get('BARK_SERVER') or 'https://api.day.app')
        if 'ntfy' in self.settings.channels:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', e['NTFY_TOPIC']):
                raise RadarError('NTFY_TOPIC must be a topic name, not a URL')
            https_url(e.get('NTFY_SERVER') or 'https://ntfy.sh')

    def send(self, channel, title, body, url, dry_run=False):
        if dry_run:
            return {'channel': channel, 'dry_run': True, 'title': title}
        e = self.settings.env
        if channel == 'bark':
            result = self.client.request((e.get('BARK_SERVER') or 'https://api.day.app').rstrip('/') + '/push',
                {'device_key': e['BARK_KEY'], 'title': title, 'body': body, 'url': url, 'group': 'Apple Stock Radar'})
            ok = isinstance(result, dict) and result.get('code') == 200
        elif channel == 'telegram':
            result = self.client.request('https://api.telegram.org/bot' + e['TELEGRAM_BOT_TOKEN'] + '/sendMessage',
                {'chat_id': e['TELEGRAM_CHAT_ID'], 'text': f'{title}\n{body}\n{url}', 'disable_web_page_preview': True})
            ok = isinstance(result, dict) and result.get('ok') is True
        elif channel == 'pushover':
            result = self.client.request('https://api.pushover.net/1/messages.json',
                {'token': e['PUSHOVER_API_TOKEN'], 'user': e['PUSHOVER_USER_KEY'], 'title': title[:250],
                 'message': body[:1024], 'url': url}, form=True)
            ok = isinstance(result, dict) and result.get('status') == 1
        elif channel == 'discord':
            result = self.client.request(e['DISCORD_WEBHOOK_URL'] + '?wait=true',
                {'content': f'**{title}**\n{body}\n{url}'[:2000], 'allowed_mentions': {'parse': []}})
            ok = isinstance(result, dict) and bool(result.get('id'))
        elif channel == 'ntfy':
            headers = {'Authorization': 'Bearer ' + e['NTFY_TOKEN']} if e.get('NTFY_TOKEN') else {}
            result = self.client.request((e.get('NTFY_SERVER') or 'https://ntfy.sh').rstrip('/'),
                {'topic': e['NTFY_TOPIC'], 'title': title, 'message': body, 'click': url}, headers=headers)
            ok = isinstance(result, dict) and bool(result.get('id'))
        else:
            raise RadarError('Unsupported notification channel')
        if not ok:
            raise RadarError(f'{channel} did not confirm notification acceptance')
        return {'channel': channel, 'accepted': True}
