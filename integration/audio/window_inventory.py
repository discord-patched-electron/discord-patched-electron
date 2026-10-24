"""Read compositor window metadata for the explicit audio picker only.

Window titles are labels, never capture authorization or an automatic match.
No focus changes, shell Eval, extension installation, or persistent title logs.
"""
import json
import os
import subprocess
from pathlib import Path


def desktop_note():
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    if 'kde' in desktop and not Path('/usr/bin/kdotool').is_file():
        return 'NOTE: KDE Plasma requires kdotool for window detection. Audio selection remains manual.'
    if 'gnome' in desktop:
        return 'NOTE: GNOME requires the Window Calls extension for window detection. Audio selection remains manual.'
    if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') or os.environ.get('SWAYSOCK'):
        return 'NOTE: This desktop exposes window names, but not an exact shared-window audio mapping. Select audio below.'
    return ''


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=2, check=True)
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError('Compositor reply too large')
    return result.stdout


def normalize(rows, backend):
    result = []
    for row in rows:
        pid = row.get('pid')
        if type(pid) is not int or pid <= 1:
            continue
        title = row.get('title', row.get('name', ''))
        app_id = row.get('app_id', row.get('class', row.get('wm_class', '')))
        if isinstance(title, str) and isinstance(app_id, str):
            result.append({'pid': pid, 'title': ''.join(c for c in title if c.isprintable())[:160],
                           'app_id': app_id[:160], 'backend': backend})
    return result


def sway_windows(tree):
    rows, pending = [], [tree]
    while pending and len(rows) + len(pending) < 10000:
        item = pending.pop()
        if item.get('type') in ('con', 'floating_con') and item.get('pid'):
            rows.append({**item, 'app_id': item.get('app_id') or item.get('window_properties', {}).get('class', '')})
        pending.extend(item.get('nodes', []))
        pending.extend(item.get('floating_nodes', []))
    return normalize(rows, 'sway')


def windows():
    try:
        if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            return normalize(json.loads(run(['/usr/bin/hyprctl', '-j', 'clients'])), 'hyprland')
        if os.environ.get('SWAYSOCK'):
            return sway_windows(json.loads(run(['/usr/bin/swaymsg', '-r', '-t', 'get_tree'])))
        desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
        if 'kde' in desktop:
            # Optional kdotool API: a single read-only query, no window mutation.
            script = 'output_result(JSON.stringify(workspace.windowList().map(w=>({pid:w.pid,title:w.caption,app_id:w.resourceClass}))));'
            return normalize(json.loads(run(['/usr/bin/kdotool', 'kwinscript', '--inline', script])), 'kwin')
        if 'gnome' in desktop:
            # Only works if the user already has Window Calls. Do not enable
            # GNOME's unsafe Eval mode or install an extension behind their back.
            reply = json.loads(run(['/usr/bin/busctl', '--user', '--json=short', 'call',
                'org.gnome.Shell', '/org/gnome/Shell/Extensions/Windows',
                'org.gnome.Shell.Extensions.Windows', 'List']))
            if reply.get('type') == 's' and len(reply.get('data', [])) == 1:
                return normalize(json.loads(reply['data'][0]), 'gnome-window-calls')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    return []
