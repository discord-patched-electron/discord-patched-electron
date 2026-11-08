"""Best-effort text redaction; opaque binary payloads must never enter the archive."""

import getpass
import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
from pathlib import Path

PRIVATE_KEY = re.compile(
    r"(?i)(?:authorization|proxy.authorization|cookie|set.cookie|.*token.*|.*password.*|"
    r".*secret.*|api.?key|credential.*|session(?:.?id)?|email|phone|username|user.?name|"
    r"display.?name|global.?name|hostname|machine.?id|device.?id|content|message.?content|"
    r"message.?body|(?:user|channel|guild|message|application|session|device).?id|"
    r"command.?line|cmdline|environment|environ|sdp|fingerprint|ice.?pwd|ice.?ufrag|"
    r"guild.?name|channel.?name|server.?name|real.?name|first.?name|last.?name|full.?name|"
    r"name|service.?name|title|id|address|location)"
)
ASSIGNMENT = re.compile(
    r"""(?im)(?<![\w-])(["']?(?:--)?(?:authorization|proxy-authorization|cookie|set-cookie|[\w-]*token[\w-]*|"""
    r"""password|passwd|secret|api[_-]?key|credentials?|session[_-]?id|email|phone|"""
    r"""username|user_name|display_name|global_name|hostname|machine_id|device_id|user|guild|channel|"""
    r"""content|message_content|message_body|cmdline|command_line|environ|environment|sdp|fingerprint|ice-pwd|ice-ufrag)["']?\s*[:=]\s*).*$"""
)


class Redactor:
    def __init__(self, profile):
        self.replacements = {}
        self.salt = secrets.token_bytes(32)
        home = str(Path.home())
        for value, label in [(str(profile), "[PROFILE]"), (home, "[HOME]")]:
            if len(value) > 1:
                self.replacements[value] = label
        for value in [getpass.getuser(), socket.gethostname()]:
            if value:
                self.replacements[value] = "[LOCAL_IDENTITY]"

    def pseudonym(self, kind, value):
        digest = hmac.new(
            self.salt, str(value).encode("utf-8", errors="replace"), hashlib.sha256
        ).hexdigest()[:12]
        return f"[{kind}-{digest}]"

    def text(self, value):
        for source, replacement in sorted(
            self.replacements.items(), key=lambda item: -len(item[0])
        ):
            value = re.sub(re.escape(source), replacement, value, flags=re.I)
        value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
        value = re.sub(r'(?i)\b(?:https?|wss?|ftp|file)://[^\s<>"\']+', "[URL]", value)
        value = ASSIGNMENT.sub(lambda match: match[1] + "[REDACTED]", value)
        value = re.sub(r"(?i)\b(?:bearer|basic)\s+[\w.+/=-]+", "[AUTH]", value)
        value = re.sub(
            r"\b(?:mfa\.[\w-]{20,}|[\w-]{20,}\.[\w-]{6,}\.[\w-]{20,})\b", "[TOKEN]", value
        )
        value = re.sub(r"(?<![\w.+%-])[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", value)
        value = re.sub(r"(?<!\d)\d{15,22}(?!\d)", "[DISCORD_ID]", value)
        value = re.sub(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "[UUID]", value)
        value = re.sub(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", "[MAC]", value)
        value = re.sub(r"(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?!\w)", "[IP]", value)

        def ipv6(match):
            try:
                ipaddress.IPv6Address(match[0].split("%", 1)[0])
                return "[IP]"
            except ValueError:
                return match[0]

        value = re.sub(r"(?i)(?<![\w:])[0-9a-f]*:[0-9a-f:.]+(?:%[\w-]+)?", ipv6, value)
        value = re.sub(
            r"""(?i)(?:/home/|/Users/|[A-Z]:\\Users\\)[^\s"'<>]+""", "[USER_PATH]", value
        )
        value = re.sub(r"(?<!\w)\+\d[\d ().-]{7,}\d", "[PHONE]", value)
        value = re.sub(r"(?<!\d)\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)", "[PHONE]", value)
        value = re.sub(r"\b[^\s#]+#\d{4}\b", "[DISCORD_USER]", value)
        value = re.sub(
            r"(?i)\b(?:discord\.gg|discord(?:app)?\.com/(?:invite|api/webhooks))/[^\s]+",
            "[DISCORD_LINK]",
            value,
        )
        # Also suppress unlabelled opaque credentials, including webhooks and session keys.
        value = re.sub(
            r"(?<![\w])[A-Za-z0-9_+/=-]{40,}(?![\w])",
            lambda match: self.pseudonym("OPAQUE_ID", match[0]),
            value,
        )
        return value

    def value(self, value):
        if isinstance(value, dict):
            return {
                self.text(str(key)): "[REDACTED]"
                if PRIVATE_KEY.fullmatch(str(key))
                else self.value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, int) and value >= 10**14:
            return "[ID]"
        if isinstance(value, str):
            return self.text(value)
        return value
