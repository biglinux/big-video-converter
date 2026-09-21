#!/usr/bin/env python3
"""Materialize the focused noise UI patch on the audited source, failing closed.

This is a review applicator, not application runtime. The resulting source and
ordinary git diff are exported by the review workflow. It never edits settings
or media. Production application is through that patch, not this script.
"""
from pathlib import Path
import ast
import hashlib

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'big-video-converter/usr/share/big-video-converter/ui/noise_dialog.py'
EXPECTED_BLOB = '794fdaece5cba0c0f25cf23e551883c091b9d740'
MARKER = '# Contextual controls: inactive operations retain values, not active knobs.'


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Expected exactly one review anchor: {old[:100]!r}')
    return source.replace(old, new, 1)


def main():
    raw = PATH.read_bytes()
    source = raw.decode('utf-8')
    if MARKER in source:
        print('Noise UI patch already applied')
        return
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != EXPECTED_BLOB:
        raise SystemExit(f'Refusing an unaudited noise_dialog.py: {blob}')
    source = replace_once(source,
        'from utils.signal_connections import SignalConnections\n',
        'from utils.signal_connections import SignalConnections\n'
        'from utils.contextual_controls import bind_details_to_switch\n')
    source = replace_once(source, '    dialog.set_content_height(920)\n',
                          '    dialog.set_content_height(680)\n')
    source = replace_once(source,
        '    dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)\n',
        '    dialog.set_presentation_mode(Adw.DialogPresentationMode.AUTO)\n')
    source = replace_once(source,
        '    scale.set_hexpand(True)\n    scale.set_valign(Gtk.Align.CENTER)\n',
        '    scale.set_hexpand(True)\n    scale.set_valign(Gtk.Align.CENTER)\n'
        '    scale.set_size_request(-1, 44)\n'
        '    scale.update_property([Gtk.AccessibleProperty.LABEL], [label])\n'
        '    lbl.set_mnemonic_widget(scale)\n')
    source = replace_once(source,
        '    control.set_valign(Gtk.Align.CENTER)\n    row.append(control)\n',
        '    control.set_valign(Gtk.Align.CENTER)\n'
        '    control.update_property([Gtk.AccessibleProperty.LABEL], [title])\n'
        '    row.append(control)\n')
    source = replace_once(source,
        '    scroll.set_child(content)\n',
        '    ' + MARKER + '\n'
        '    for card, switch in ((card1, nr_switch), (card2, gate_switch),\n'
        '                         (card3, hpf_switch), (card6, comp_switch)):\n'
        '        header = card.get_first_child()\n'
        '        child = header.get_next_sibling()\n'
        '        details = []\n'
        '        while child is not None:\n'
        '            details.append(child)\n'
        '            child = child.get_next_sibling()\n'
        '        bind_details_to_switch(switch, details, connections)\n'
        '\n    scroll.set_child(content)\n')
    ast.parse(source, filename=str(PATH))
    PATH.write_text(source, encoding='utf-8')
    print('Applied contextual visibility, native adaptive presentation and control names')


if __name__ == '__main__':
    main()
