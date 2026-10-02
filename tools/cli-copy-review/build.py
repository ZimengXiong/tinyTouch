#!/usr/bin/env python3
"""Build the manual copy-review catalog without connecting to a device."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ENTRIES: list[dict] = []
COVERAGE: list[dict] = []


def add(text: str, group: str, context: str, source: str = '', kind: str = 'copy') -> None:
    # Extract occurrences first; the sentence catalog below combines duplicates.
    for index, line in enumerate(text.split('\n'), 1):
        if not line.strip():
            continue
        # Display terminal control bytes as escapes rather than executing them in exports.
        line = line.replace('\x1b', r'\x1b').replace('\r', r'\r').replace('\x00', r'\x00')
        ENTRIES.append(dict(id=f'TT-{len(ENTRIES) + 1:04d}', original=line,
                            group=group, context=context + (f' · line {index}' if '\n' in text else ''),
                            source=source, kind=kind))


def template(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ''
    if isinstance(node, ast.JoinedStr):
        parts = []
        for item in node.values:
            if isinstance(item, ast.Constant):
                parts.append(item.value)
            else:
                value = ast.unparse(item.value)
                conversion = f'!{chr(item.conversion)}' if item.conversion != -1 else ''
                spec = ':' + template(item.format_spec) if item.format_spec else ''
                parts.append('{' + value + conversion + spec + '}')
        return ''.join(parts)
    return ''


def group_for(name: str, path: str) -> str:
    if path != 'macos/cli.py':
        return 'Helper and native errors'
    if name in {'show_startup_mark', 'terminal_style', 'fingerprint_oval'}:
        return 'Branding and layout'
    if 'enroll' in name or name in {'show_enrollment_view', 'show_enrollment_event'}:
        return 'Fingerprint enrollment'
    if name.startswith('interactive') or name in {'select_option', 'choose_mode', 'prompt_setting'}:
        return 'Interactive menus'
    if name in {'human_error', 'setting_value', 'setting_name', 'bounded_integer', 'error'}:
        return 'Errors and validation'
    if any(word in name for word in ('led', 'config', 'setting')):
        return 'Settings and lighting'
    if any(word in name for word in ('update', 'ota', 'download', 'release', 'network', 'package')):
        return 'Updates and downloads'
    if any(word in name for word in ('piv', 'pair', 'keys')):
        return 'PIV and pairing'
    if any(word in name for word in ('setup', 'hid', 'password', 'keychain', 'helper', 'macos')):
        return 'Setup and HID'
    if any(word in name for word in ('finger', 'delete', 'computer', 'host', 'reset')):
        return 'Fingerprints, computers and reset'
    return 'Connection and shared output'


def extract_python(path: str) -> None:
    tree = ast.parse((ROOT / path).read_text())
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                docstrings.add(node.body[0].value)
    count = 0
    for node in sorted(ast.walk(tree), key=lambda n: (getattr(n, 'lineno', 0), getattr(n, 'col_offset', 0))):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)) or node in docstrings:
            continue
        if isinstance(node, ast.Constant) and not isinstance(node.value, str):
            continue
        ancestors = []
        parent = parents.get(node)
        while parent:
            ancestors.append(parent)
            parent = parents.get(parent)
        # F-string fragments are represented by the outer template. Literals inside
        # expressions (for example a displayed fallback value) need their own rows.
        immediate = parents.get(node)
        if isinstance(immediate, ast.JoinedStr):
            continue
        if isinstance(node, ast.JoinedStr) and isinstance(immediate, ast.FormattedValue) and immediate.format_spec is node:
            continue
        value = template(node)
        if not value.strip():
            continue
        count += 1
        owner = next((p.name for p in ancestors if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef))), 'module metadata')
        condition = next((ast.unparse(p.test) for p in ancestors if isinstance(p, ast.If)), '')
        context = owner.replace('_', ' ') + (f' · when {condition[:150]}' if condition else '')
        calls = [p for p in ancestors if isinstance(p, ast.Call)]
        callnames = [ast.unparse(p.func) for p in calls]
        visible = any(n in {'say', 'print', 'ask', 'notify', 'getpass.getpass', 'verbose', 'select_option', 'diagnostic'}
                      or n.endswith(('Error', '.error', '.exit')) for n in callnames)
        visible = visible or any(isinstance(p, ast.keyword) and p.arg in
                                {'touch_prompt', 'touch_again_prompt', 'lift_prompt', 'wait_message', 'reason', 'back', 'message'} for p in ancestors)
        # Prose assigned to local variables/defaults also reaches output sinks.
        prose = bool(re.search(r'[A-Za-z]{2,}[ ,;:!?—–]', value)) and not value.startswith(('ERR ', 'OK ', 'EVENT ', '^', 'https://'))
        visible = visible or prose or value in {'Back', 'Exit', 'Cancelled.', 'HID', 'PIV'}
        # Evaluated snapshots below cover parser metadata and the structured catalogs.
        metadata = owner == 'parser' or (owner == 'module metadata' and path == 'macos/cli.py')
        group = group_for(owner, path) if visible and not metadata else 'Technical appendix'
        kind = 'template' if isinstance(node, ast.JoinedStr) else 'copy'
        if any(isinstance(p, ast.keyword) and p.arg == 'reason' for p in ancestors):
            kind = 'fragment'
        if group == 'Technical appendix':
            kind = 'technical'
        add(value, group, context, f'{path}:{node.lineno}', kind)
    COVERAGE.append(dict(file=path, literals=count, method='AST: all nonempty runtime string literals; docstrings excluded'))


def extract_c(path: str) -> None:
    text = (ROOT / path).read_text()
    # Tokenize comments and C strings together, so comment contents never become entries.
    token = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"')
    count = 0
    for match in token.finditer(text):
        if not match.group().startswith('"'):
            continue
        raw = match.group()[1:-1]
        value = raw.replace(r'\n', '\n').replace(r'\t', '\t').replace(r'\"', '"').replace(r'\\', '\\')
        if not value.strip():
            continue
        count += 1
        line = text.count('\n', 0, match.start()) + 1
        nearby = text[max(0, match.start() - 180):match.start()]
        visible = value.startswith(('LOG ', 'ERR ', 'OK ', 'STATUS ', 'EVENT ')) or 'touch_pin_hid_log_event(' in nearby.split(';')[-1]
        group = 'Device diagnostics' if visible else 'Technical appendix'
        add(value, group, Path(path).name + (' · device output format' if visible else ' · firmware literal'),
            f'{path}:{line}', 'device' if visible else 'technical')
    COVERAGE.append(dict(file=path, literals=count, method='C string tokens; comments excluded'))


def load_cli():
    sys.path.insert(0, str(ROOT / 'macos'))
    spec = importlib.util.spec_from_file_location('tinytouch_copy_catalog', ROOT / 'macos/cli.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generated_copy(cli) -> list[dict]:
    routes = []
    for name, (title, choices) in cli.INTERACTIVE_MENUS.items():
        context = f'Interactive menu · {name}'
        add(title, 'Interactive menus', context + ' · heading')
        for number, (key, label, target) in enumerate(choices, 1):
            add(f'{number}. {label} [{key}]', 'Interactive menus', context + ' · option')
            routes.append(dict(path=f'menu / {name} / {key}', target=' '.join(target) if isinstance(target, list) else f'menu / {target}'))
        add('0. Exit' if name == 'home' else '0. Back', 'Interactive menus', context + ' · exit')
    for name, spec in cli.SETTINGS.items():
        context = f'config / {name}'
        add(spec.label, 'Settings and lighting', context + ' · label')
        add(spec.description, 'Settings and lighting', context + ' · description')
        add(f'Default: {spec.default}. Values: {spec.allowed()}.', 'Settings and lighting', context + ' · limits')
        if spec.choices:
            for number, key in enumerate(spec.choices, 1):
                add(f'{number}. {key} [{key}]', 'Settings and lighting', context + ' · choice')
    for label, catalog in [('LED preset', cli.LED_PRESETS), ('Preview color', cli.LED_COLORS), ('Preview effect', cli.LED_EFFECTS)]:
        for number, key in enumerate(catalog, 1):
            add(f'{number}. {key.title()} [{key}]', 'Settings and lighting', label + ' · choice')
    for key, label, _style in cli.ENROLLMENT_VIEWS:
        add(label, 'Fingerprint enrollment', f'Enrollment / {key} · view label')
    # The original artwork is dynamic output; include its literal rows in the main catalog.
    tree = ast.parse((ROOT / 'macos/cli.py').read_text())
    startup = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'show_startup_mark')
    for node in startup.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'mark' for t in node.targets):
            for line in ast.literal_eval(node.value):
                add(line, 'Branding and layout', 'Startup Alpaca mark')
    add(f'tinyTouch CLI {cli.CLI_VERSION}', 'Branding and layout', 'tinytouch --version')
    seen = {}
    def visit(parser, path):
        if id(parser) in seen:
            routes.append(dict(path=path, target=f'alias of {seen[id(parser)]}'))
            return
        seen[id(parser)] = path
        routes.append(dict(path=path, target='Command help and runtime handler'))
        # Read help metadata before argparse wraps it into partial display lines.
        if parser.description:
            add(parser.description, 'Command help', path + ' --help · description', kind='help')
        if parser.epilog:
            for line in parser.epilog.splitlines():
                if line.lstrip().startswith('tinytouch'):
                    parts = re.split(r'\s{2,}', line.strip(), maxsplit=1)
                    if len(parts) == 2:
                        add(parts[1], 'Command help', path + ' --help · example description', kind='help')
                else:
                    add(line, 'Command help', path + ' --help · explanation', kind='help')
        for action in parser._actions:
            if action.help and action.help != argparse.SUPPRESS:
                add(action.help, 'Command help', path + ' --help · ' + action.dest, kind='help')
            if isinstance(action, argparse._SubParsersAction):
                for option in action._choices_actions:
                    if option.help and option.help != argparse.SUPPRESS:
                        add(option.help, 'Command help', path + ' --help · ' + option.dest, kind='help')
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    visit(child, f'{path} {name}')
    visit(cli.parser(), 'tinytouch')
    # Argument-driven paths do not have their own argparse parser.
    for path in ['setup --mode hid', 'setup --mode piv', 'setup --skip-enroll', 'setup --no-pair',
                 'enroll FINGER --replace', 'mode hid', 'mode piv', 'computers list', 'computers remove HOST_ID',
                 'config show', 'config list', 'config NAME', 'config NAME VALUE', 'config --json',
                 'settings NAME VALUE', 'led color ROLE COLOR', 'led effect EFFECT', 'led preset PRESET',
                 'led preview COLOR --effect EFFECT --duration-ms MS', 'help COMMAND', '--verbose COMMAND',
                 '--port PATH COMMAND', '--version', '_helper', '_package_test', '_network_test']:
        routes.append(dict(path='tinytouch ' + path, target='Argument-driven or internal command path'))
    return routes


def canonical(text: str) -> str:
    text = re.sub(r'^\s*\d+\.\s+', '', text)
    text = re.sub(r'\s+\[[\w-]+\]\s*$', '', text)
    return re.sub(r'\s+', ' ', text).strip().removeprefix('. ')


def word_count(text: str) -> int:
    # A template expression is one displayed value, not several prose words.
    text = re.sub(r'\{[^{}]*\}|\$\([^)]*\)', 'VALUE', text)
    return sum(bool(re.search(r"\w", token)) for token in text.split())


def sentence_catalog(previous: dict) -> tuple[list[dict], dict]:
    wording = json.loads((HERE / 'wording.json').read_text())
    selected = {}
    stats = dict(extracted=len(ENTRIES), duplicates=0, omitted=0, minimumWords=6)
    excluded = {'Technical appendix', 'Device diagnostics', 'External and dynamic output', 'Argument errors'}
    for occurrence in ENTRIES:
        text = canonical(occurrence['original'])
        if (occurrence['group'] in excluded or word_count(text) < 6
                or text.startswith(('Default:', 'Colors:', 'Effects:', 'Settings (', 'display notification ', 'sensor=', 'EV ', 'EV2 '))
                or re.match(r'^\w+: (?:off|breathe|on),', text)
                or text == 'password will be required after next SmartCard login'):
            stats['omitted'] += 1
            continue
        key = text.casefold().rstrip('.: ')
        where = {name: occurrence[name] for name in ['group', 'context', 'source']}
        if key in selected:
            stats['duplicates'] += 1
            if where not in selected[key]['occurrences']:
                selected[key]['occurrences'].append(where)
            continue
        # IDs are content-based, so adding/removing other sentences does not
        # detach comments from surviving wording.
        entry = dict(occurrence, id='TT-' + hashlib.sha256(key.encode()).hexdigest()[:10],
                     original=text, occurrences=[where], wordCount=word_count(text), legacyIds=[])
        entry['proposed'] = wording.get(text, text)
        selected[key] = entry
    for old in previous.get('entries', []):
        key = canonical(old['original']).casefold().rstrip('.: ')
        if key in selected:
            selected[key]['legacyIds'] = [item for item in dict.fromkeys(selected[key]['legacyIds'] + old.get('legacyIds', []) + [old['id']]) if item != selected[key]['id']]
    stats['retained'] = len(selected)
    return list(selected.values()), stats


def build_page(payload: dict) -> None:
    """Publish one coherent document; cached assets cannot mix page versions."""
    template = (HERE / 'index.template.html').read_text()
    style = (HERE / 'site/style.css').read_text()
    script = (HERE / 'site/app.js').read_text()
    embedded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = template.replace('<link rel="stylesheet" href="style.css">', '<style>\n' + style + '\n</style>')
    page = page.replace('  <script defer src="app.js"></script>\n', '')
    script = re.sub(r'</script', r'<\\/script', script, flags=re.IGNORECASE)
    page = page.replace('</body>', '<script type="application/json" id="copy-catalog">' + embedded + '</script>\n<script>\n' + script + '\n</script>\n</body>')
    (HERE / 'site/index.html').write_text(page)


def main() -> None:
    catalog_path = HERE / 'site/catalog.json'
    previous = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
    legacy_originals = previous.get('legacyOriginals')
    if legacy_originals is None:
        try:
            committed = json.loads(subprocess.check_output(['git', 'show', 'HEAD:tools/cli-copy-review/site/catalog.json'], cwd=ROOT, text=True))
            legacy_originals = {entry['id']: entry['original'] for entry in committed.get('entries', [])}
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            legacy_originals = {}
    cli = load_cli()
    routes = generated_copy(cli)
    for path in ['macos/cli.py', 'macos/tinytouch_helper.py', 'macos/tinytouch_runtime.py',
                 'macos/tinytouch_keychain.py', 'macos/tinytouch_ports.py']:
        extract_python(path)
    installer = (ROOT / 'release/install.sh').read_text()
    count = 0
    for number, line in enumerate(installer.splitlines(), 1):
        match = re.search(r'\becho\s+(?:"((?:\\.|[^"\\])*)"|\'([^\']*)\')', line)
        if match:
            text = match[1] if match[1] is not None else match[2]
            add(text, 'Installer', 'Install / update · shell output', f'release/install.sh:{number}', 'template' if '$' in text else 'copy')
            count += 1
    COVERAGE.append(dict(file='release/install.sh', literals=count, method='Installer echo output'))
    files = subprocess.check_output(['git', 'ls-files', 'firmware/tiny_touch_unified/main/*.c'], cwd=ROOT, text=True).splitlines()
    for path in files:
        extract_c(path)
    for context, value in [
        ('macOS administrator authorization', '[macOS sudo password prompt and authorization errors]'),
        ('macOS Login Keychain / PIV pairing', '[macOS Security / sc_auth messages and dialogs]'),
        ('Download and subprocess failures', '[Operating-system, network-library and subprocess error details]'),
        ('Device status / ports / logs', '[Current device values, identifiers, USB paths, times and numeric status codes]'),
        ('Helper diagnostics', '[Diagnostic JSON fields and event values; see the technical appendix]'),
    ]:
        add(value, 'External and dynamic output', context, kind='external')
    entries, stats = sentence_catalog(previous)
    legacy_key = f'tinytouch-copy-review:{previous.get("commit")}:{len(previous.get("entries", []))}'
    payload = dict(title='tinyTouch CLI sentence review', version=cli.CLI_VERSION,
                   commit=subprocess.check_output(['git', 'log', '-1', '--format=%h', '--', 'macos/cli.py'], cwd=ROOT, text=True).strip(),
                   entries=entries, routes=routes, coverage=COVERAGE, selection=stats,
                   legacyStorageKey=previous.get('legacyStorageKey', legacy_key),
                   legacyOriginals=legacy_originals,
                   style='Approximately 80% AES technical English: direct instructions, short sentences, consistent terminology, and explicit results and recovery steps.')
    (HERE / 'site/original-catalog.json').write_text(json.dumps(dict(entries=ENTRIES, coverage=COVERAGE), ensure_ascii=False, indent=2) + '\n')
    catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    build_page(payload)
    print(f'Built {len(entries)} unique sentences and longer phrases (six or more words).')
    print(f'Combined {stats["duplicates"]} duplicates; omitted {stats["omitted"]} short or technical entries.')


if __name__ == '__main__':
    main()
