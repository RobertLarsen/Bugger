#!/usr/bin/env python3

import shlex
import string
import re
import struct
import fcntl
import termios
import sys
import os
from subprocess import run, PIPE, STDOUT, TimeoutExpired
import json
import time
import signal
import ctypes
from typing import Dict, Union, List, Generator, overload, Tuple

RED       = u"\u001b[31m"
GREEN     = u"\u001b[32m"
YELLOW    = u"\u001b[33m"
BLUE      = u"\u001b[34m"
ERASE_EOL = u"\u001b[K"
RESET     = u"\u001b[0m"

HOURGLASS = u"\u29d6"
SUCCESS   = u"\u2714"
FAILURE   = u"\u2718"
def forward_backward(lst):
    return lst + lst[-1:0:-1]

ANIMATION = forward_backward([
               u"\u2840", u"\u2844", u"\u2846",
    u"\u2847", u"\u284f", u"\u285f", u"\u287f",
    u"\u28ff", u"\u28bf", u"\u28bb", u"\u28b9",
    u"\u28b8", u"\u28b0", u"\u28a0", u"\u2880"
])

PR_SET_CHILD_SUBREAPER = 36

signals = {int(getattr(signal, m)):m for m in dir(signal) if m.startswith('SIG')}
libc = ctypes.CDLL('libc.so.6')
libc.prctl(PR_SET_CHILD_SUBREAPER, 1)

def is_true(val):
    return val == True or \
            (isinstance(val, int) and val != 0) or \
            (isinstance(val, str) and val.lower() in ('true', 'on', 'yes'))

def read_ppid(path: str) -> Union[int, None]:
    try:
        with open(path) as f:
            for line in f:
                if line.startswith('PPid:'):
                    return int(line.split('\t')[-1])
    except:
        pass

def get_ppid(pid=os.getpid()) -> Union[int, None]:
    return read_ppid(f'/proc/{pid}/status')

def ppids():
    pid = os.getpid()
    while pid is not None:
        pid = get_ppid(pid)
        if pid is not None:
            yield pid

def parent_exes():
    for p in ppids():
        path = f'/proc/{p}/exe'
        if os.access(path, os.R_OK):
            yield os.readlink(path)

def children(parent_pid=os.getpid()):
    for entry in os.listdir('/proc'):
        ppid = read_ppid(f'/proc/{entry}/status')
        if ppid == parent_pid:
            yield int(entry)

def dict_concat(*dicts: Union[Dict[str,str], os._Environ, None]) -> Dict[str,str]:
    res = {}
    for d in dicts:
        if d:
            for key, val in d.items():
                res[key] = val
    return res

def normalize_dict(d: Dict[str,str], environ=os.environ) -> Union[Dict[str,str],None]:
    if d:
        res = {}
        for key, val in d.items():
            res[key] = normalize(val, environ)
        return res

def get_terminal_size() -> Tuple[int,int]:
    if sys.stdout.isatty():
        res = struct.unpack('hh', fcntl.ioctl(0, termios.TIOCGWINSZ, b'1234'))
        return (res[0], res[1])
    else:
        return (25, 80)

@overload
def normalize(s: str, environ=os.environ) -> str: ...
@overload
def normalize(s: None, environ=os.environ) -> None: ...
@overload
def normalize(s: list, environ=os.environ) -> list: ...
@overload
def normalize(s: dict, environ=os.environ) -> dict: ...
@overload
def normalize(s: int, environ=os.environ) -> int: ...

def normalize(s: Union[str,int,None,list,dict], environ=os.environ) -> Union[str,int,None,list,dict]:
    if s is None:
        return s
    elif type(s) in (int, bool):
        return s
    elif isinstance(s, list):
        return [normalize(e, environ) for e in s]
    elif isinstance(s, dict):
        return {k:normalize(v, environ) for k, v in s.items()}

    pairs = [
        ['{','}'],
        ['(',')']
    ]
    def findMatching(s, idx):
        openToken = s[idx + 1]
        tokenPair = next(iter(filter(lambda e: e[0] == openToken, pairs)), None)
        if tokenPair is not None:
            closeToken = tokenPair[1]
            count = 1
            for i in range(idx + 1, len(s)):
                if s[i] == '$' and s[i + 1] == openToken:
                    count += 1
                elif s[i] == closeToken:
                    count -= 1
                if count == 0:
                    return i

    regex = '\\$({})'.format('|'.join('\\' + p[0] for p in pairs))

    if isinstance(s, str):
        while True:
            m = re.search(regex, str(s))
            if not m:
                return s
            idx1 = m.span()[0]
            idx2 = findMatching(s, idx1)
            if idx2 is None:
                raise Exception('Missing closing bracket')
            n = normalize(str(s)[idx1 + 2:idx2], environ)
            if str(s)[idx1 + 1] == '{':
                parts = n.split('|')
                n = parts[0]
                filter_names = parts[1:]

                parts = n.split(':')
                n = parts[0]
                parts = parts[1:]
                default_value = None

                if parts and parts[0] and parts[0][0] == '-':
                    default_value = parts[0][1:]

                if n in environ:
                    n = environ[n]
                elif default_value is not None:
                    n = default_value
                else:
                    raise Exception('Unresolved environment variable: {}'.format(n))

                for filter_name in filter_names:
                    try:
                        f = getattr(str, filter_name)
                        n = f(n)
                    except:
                        raise Exception(f'No such filter: {filter_name}')

            elif str(s)[idx1 + 1] == '(':
                proc = run(['/bin/bash', '-c', n], stdout=PIPE, stderr=STDOUT)
                if proc.returncode != 0:
                    raise Exception('Could not execute command: {}'.format(n))
                n = proc.stdout.decode('utf8')
            s = str(s)[:idx1] + n + str(s)[idx2 + 1:]

def be_str(func):
    def w(self, t=None, count=True):
        if not t is None:
            if not type(t) in (bytes, str):
                t = str(t)
        return func(self, t, count)
    return w

class Writer(object):
    def __init__(self, out):
        self.out = out
        self.indentation = 0
        self.clean = True
        self.pos = 0
    def increment(self):
        self.indentation += 1
    def decrement(self):
        self.indentation -= 1
    def ansi(self, code):
        return self('\x01{}\x02'.format(code), False)
    @be_str
    def __call__(self, t, count=True):
        if count:
            w = get_terminal_size()[1]
            l = w - self.pos
            t = t[:l]
        self.out.write(t)
        self.out.flush()
        self.clean = False
        if count:
            self.pos += len(t)
        return self
    @be_str
    def red(self, t, count=True):
        return self.ansi(RED)(t, count).ansi(RESET)
    @be_str
    def green(self, t, count=True):
        return self.ansi(GREEN)(t, count).ansi(RESET)
    @be_str
    def yellow(self, t, count=True):
        return self.ansi(YELLOW)(t, count).ansi(RESET)
    @be_str
    def blue(self, t, count=True):
        return self.ansi(BLUE)(t, count).ansi(RESET)
    def begin(self):
        self.pos = 0
        return self.ansi('\r')(' ' * self.indentation).ansi(ERASE_EOL)
    def end(self, t=None, count=True, newline=True):
        self((t or ''), count)('\n' if newline else '', False)
        self.clean = True
        self.pos = 0
        return self

class Terminal(object):
    def __init__(self):
        self._stdout = Writer(sys.stdout)
        self._stderr = Writer(sys.stderr)

    def __enter__(self):
        self._stdout.increment()
        self._stderr.increment()

    def __exit__(self, t, v, tr):
        if not (self._stdout.clean and self._stdout.clean):
            (self._stdout if self._stdout.clean else self._stdout).end()
        self._stdout.decrement()
        self._stderr.decrement()

    def goto(self, row: int, col: int):
        self._stdout.ansi(f'\033[{int(row)};{int(col)}H')

    def clear_screen(self):
        self._stdout.ansi('\033[2J')

    def save_pos(self):
        self._stdout.ansi('\033[s')

    def restore_pos(self):
        self._stdout.ansi('\033[u')

    @property
    def stdout(self):
        return self._stdout.begin()

    @property
    def stderr(self):
        return self._stderr.begin()

term = Terminal()

class Command(object):
    STATE_NEW = 0
    STATE_SUCCESSFUL = 1
    STATE_SIGNALED = 2
    STATE_FAILED = 3
    STATE_TIMED_OUT = 4
    STATE_SKIPPED = 5
    STATE_EXECUTING = 6

    def __init__(self, conf, group, settings):
        if not 'name' in conf:
            raise Exception('Command missing "name"')
        if not ('exec' in conf or 'system' in conf):
            raise Exception('Command missing "exec" or "system"')
        self._animation_idx = 0
        self.group = group
        self.state = Command.STATE_NEW
        self.output = None
        self.duration = None

        self._conf = conf
        self._settings = settings
        self._success = None
        self._failure = None
        self._execute = None
        self._skipped = None

    def __getattr__(self, name):
        if 'settings' in self._conf and name in self._conf['settings']:
            return self._conf['settings'][name]
        elif name in self._settings:
            return self._settings[name]

    @property
    def failed(self):
        return self.state in (Command.STATE_FAILED, Command.STATE_SIGNALED, Command.STATE_TIMED_OUT)

    @property
    def finished(self):
        return self.state not in (Command.STATE_NEW, Command.STATE_EXECUTING)

    def skip(self):
        self.state = Command.STATE_SKIPPED

    def on_skipped(self, callback):
        self._skipped = callback
        return self

    def on_success(self, callback):
        self._success = callback
        return self

    def on_failure(self, callback):
        self._failure = callback
        return self

    def on_execute(self, callback):
        self._execute = callback
        return self

    def _output(self, o):
        self.output = o

    @property
    def strout(self) -> str:
        if isinstance(self.output, str):
            return self.output

        try:
            return self.output.decode('utf-8') if self.output is not None else '<<< NO OUTPUT SET >>>'
        except:
            return '<<< OUTPUT NOT UTF-8 >>>'


    @property
    def env(self):
        return dict_concat(os.environ, normalize_dict(self._conf.get('environment')))

    @property
    def name(self):
        return normalize(self._conf['name'], self.env)

    @property
    def path(self):
        return normalize(self._conf['exec'], self.env) if 'exec' in self._conf else self.env.get('SHELL', '/bin/sh')

    @property
    def args(self):
        return ([normalize(a, self.env) for a in self._conf['arguments']] if 'arguments' in self._conf else []) if 'exec' in self._conf else ['-c', self._conf['system']]

    def _fail(self, n=None):
        if self._failure:
            self._failure(self, n or self.strout)

        return self

    def _succeed(self):
        if 'stdout-to-env' in self._conf and isinstance(self._conf['stdout-to-env'], str):
            os.environ[self._conf['stdout-to-env']] = self.strout

        if self._success:
            self._success(self)
        return self

    def __call__(self):
        if self.state == Command.STATE_SKIPPED:
            if self._skipped:
                self._skipped(self)
            return self

        self.state = Command.STATE_EXECUTING
        pre = time.time()

        for key, val in self._conf.items():
            self._conf[key] = normalize(val, self.env)

        try:
            try:
                if self._execute:
                    self._execute(self)
                self.command_str = ' '.join(shlex.quote(s) for s in ([self.path] + self.args))
                proc = run([self.path] + self.args, stdout=PIPE, stderr=STDOUT, timeout=self.timeout, cwd=normalize(self.chdir, self.env), env=self.env)
            finally:
                self.duration = time.time() - pre

            self._output(proc.stdout)

            for i in ('output-matches', '!output-matches'):
                if i in self._conf and isinstance(self._conf[i], list):
                    self._conf[i] = '\n'.join(self._conf[i])

            if proc.returncode == self.__getattr__('expected-exit-code'):
                if 'output-matches' in self._conf:
                    if self._conf['output-matches'] != self.strout:
                        self.state = Command.STATE_FAILED
                        return self._fail()
                if 'output-contains' in self._conf:
                    m = self._conf['output-contains']
                    if type(m) == str:
                        m = [m]
                    if not all(self.strout.find(s) >= 0 for s in m):
                        self.state = Command.STATE_FAILED
                        return self._fail()
                if '!output-contains' in self._conf:
                    m = self._conf['!output-contains']
                    if type(m) == str:
                        m = [m]
                    if not all(self.strout.find(s) < 0 for s in m):
                        self.state = Command.STATE_FAILED
                        return self._fail()
                if 'success-command' in self._conf:
                    cmd = self._conf['success-command']
                    p = run(['/bin/bash', '-c', cmd], stdout=PIPE, stderr=STDOUT, cwd=normalize(self.chdir, self.env), env=self.env)
                    if p.returncode != 0:
                        n = p.stdout.decode('utf8')
                        self.state = Command.STATE_FAILED
                        return self._fail(n if n else 'Success checking command failed')
                self.state = Command.STATE_SUCCESSFUL
                return self._succeed()
            elif proc.returncode < 0:
                self.state = Command.STATE_SIGNALED
                self.signal = -proc.returncode
                return self._fail('Terminated by signal {}'.format(signals.get(-proc.returncode, str(-proc.returncode))))
            else:
                self.state = Command.STATE_FAILED
                return self._fail()
        except TimeoutExpired as e:
            self.state = Command.STATE_TIMED_OUT
            return self._fail('Timed out')
        except Exception as e:
            self.state = Command.STATE_FAILED
            return self._fail(str(e))

    def _animation(self):
        s = ANIMATION[self._animation_idx]
        self._animation_idx = (self._animation_idx + 1) % len(ANIMATION)
        return s

    def _sign(self):
        return (HOURGLASS if self.state == Command.STATE_NEW else \
                SUCCESS if self.state == Command.STATE_SUCCESSFUL else \
                self._animation() if self.state == Command.STATE_EXECUTING else \
                FAILURE)

    def __str__(self):
        return self._sign() + ' ' + self.name

def resolve_includes(conf, conf_path):
    def include(inc):
        if not 'include' in inc:
            raise Exception('Missing include')
        path = os.path.join(os.path.dirname(conf_path), inc['include'])
        if not os.path.isfile(path):
            raise Exception(f'{path} is not a file')
        obj = None
        with open(path) as f:
            obj = json.loads(f.read())

        for decoration in ('environment', 'settings'):
            if decoration in inc:
                for t in obj:
                    t[decoration] = dict_concat(inc[decoration], t[decoration] if decoration in t else None)
        return obj

    if 'command_groups' in conf:
        for group_name, group_value in conf['command_groups'].items():
            if type(group_value) == dict:
                conf['command_groups'][group_name] = include(group_value)

    return conf

class CommandGroup(object):
    def __init__(self, name: str, bugger: 'Bugger'):
        self.name = name
        self.bugger = bugger
        self.commands: List[Command] = []

    @property
    def pending(self) -> bool:
        return all(c.state == Command.STATE_NEW for c in self.commands)

    @property
    def finished(self) -> bool:
        return not any(c.state == Command.STATE_NEW for c in self.commands)

    @property
    def running(self) -> bool:
        return any(c.state == Command.STATE_EXECUTING for c in self.commands)

    @property
    def failed(self) -> bool:
        return any(c.state in (Command.STATE_FAILED, Command.STATE_SIGNALED, Command.STATE_TIMED_OUT) for c in self.commands)

    def collapsed(self) -> bool:
        return self.bugger.should_collapse and not self.running

    @property
    def is_disabled(self) -> bool:
        return self.name.startswith('_')

    def append(self, command: Command):
        self.commands.append(command)

class Bugger(object):
    def __init__(self, conf, conf_path):
        self.groups: List[CommandGroup] = []
        self._conf = resolve_includes(conf, conf_path)
        self._settings = {
           'timeout': 10,
           'exit-on-fail': False,
           'expected-exit-code': 0,
           'animation': True,
           'enable-collapse': True
        }

        if not 'command_groups' in conf:
            raise Exception('No command groups section found')

        # Add environment variables
        os.environ['BUGGER_FILE'] = str(os.path.realpath(conf_path))
        os.environ['BUGGER_DIR'] = str(os.path.dirname(os.environ['BUGGER_FILE']))
        if 'environment' in conf:
            for k, v in conf['environment'].items():
                try:
                    os.environ[k] = normalize(v)
                except Exception as e:
                    term.stderr.red('Error normalizing environment variable "').blue(k).red('"="').blue(v).red('" : ').blue(str(e))

        # Update default settings
        if 'settings' in conf:
            for k, v in conf['settings'].items():
                self._settings[k] = normalize(v)

        self._create_commands()

    @property
    def enable_collapse(self):
        return is_true(self._settings['enable-collapse'])

    @property
    def line_count(self) -> int:
        return len(self.groups) + len([c for c in self.commands]) + 1

    @property
    def should_collapse(self) -> bool:
        return self.enable_collapse and get_terminal_size()[0] < self.line_count

    def _create_commands(self):
        for group_name in self._conf['command_groups'].keys():
            group = CommandGroup(normalize(group_name), self)
            if not group.is_disabled:
                self.groups.append(group)
                with term:
                    for t in self._conf['command_groups'][group_name]:
                        c = Command(t, group.name, self._settings) \
                        .on_execute(lambda c: term.stdout.yellow(str(c))(': ').blue(' '.join([e.replace('\n', '') for e in [c.path] + c.args]))) \
                        .on_success(lambda c: term.stdout.green(str(c))(' ({:.3f} secs): '.format(c.duration or 0.0)).blue(c.strout.split('\n')[0]).end())  \
                        .on_failure(lambda c, e: term.stdout.red(str(c))(' ({:.3f} secs): '.format(c.duration)).blue(e.split('\n')[0]).end()) \
                        .on_skipped(lambda c: term.stdout(str(c))(': SKIPPED!').end())
                        group.append(c)

    @property
    def commands(self) -> Generator[Command, None, None]:
        for g in self.groups:
            for c in g.commands:
                yield c

    def _animate(self, signum: int, stack_frame):
        c = next(iter([c for c in self.commands if c.state == Command.STATE_EXECUTING]), None)
        if c:
            term.stdout(f'\r    ').yellow(str(c))(': ').blue(' '.join([e.replace('\n', '') for e in [c.path] + c.args]))

    def _save(self, path):
        def ensure(p):
            if not os.path.exists(p):
                ensure(os.path.dirname(p))
                os.mkdir(p)

        if path:
            for group in self.groups:
                for command in group.commands:
                    good = string.ascii_letters + string.digits + '_'
                    def unbadify(s):
                        return ''.join(c if c in good else '_' for c in s)
                    cmd_path = os.path.realpath(os.path.join(path, unbadify(group.name), unbadify(command.name)))
                    ensure(cmd_path)
                    if command.command_str is not None:
                        with open(os.path.join(cmd_path, 'command'), 'w') as f:
                            f.write(command.command_str)
                    if command.output is not None:
                        with open(os.path.join(cmd_path, 'output'), 'wb') as f:
                            f.write(command.output)
                    with open(os.path.join(cmd_path, 'status'), 'w') as f:
                        if command.state == Command.STATE_SUCCESSFUL:
                            f.write("Successful")
                        elif command.state == Command.STATE_SIGNALED:
                            f.write(f"Terminated by signal {signals.get(command.signal, str(command.signal))}")
                        elif command.state == Command.STATE_FAILED:
                            f.write("Failed")
                        elif command.state == Command.STATE_TIMED_OUT:
                            f.write("Timed out")
                        elif command.state == Command.STATE_SKIPPED:
                            f.write("Skipped")
                    if 'output-matches' in command._conf:
                        with open(os.path.join(cmd_path, 'output-matches'), 'w') as f:
                            f.write(command._conf['output-matches'])

    @property
    def current_group(self) -> Union[CommandGroup, None]:
        for g in self.groups:
            if not g.finished:
                return g

    def _print_pre_run(self):
        term.clear_screen()
        term.goto(1, 1)
        for group in self.groups:
            term.stdout.blue(group.name).end()
            if self.should_collapse == False or group == self.current_group:
                with term:
                    for c in group.commands:
                        term.stdout.yellow(str(c)).end()

    def _print_collapsed_run(self):
        term.clear_screen()
        term.goto(1, 1)
        for group in self.groups:
            term.stdout.blue(group.name).end()
            if group.finished:
                with term:
                    for c in group.commands:
                        if c.failed:
                            term.stdout.red(str(c))(': ').blue(' '.join([e.replace('\n', '') for e in [c.path] + c.args])).end()
            elif group == self.current_group:
                with term:
                    for c in group.commands:
                        if c == group.commands[0]:
                            term.save_pos()
                        term.stdout.yellow(str(c)).end()
        if self.current_group is not None:
            term.restore_pos()


    def _run(self):
        skip = False
        term.goto(1, 1)
        for group in self.groups:
            if self.should_collapse:
                self._print_collapsed_run()
            else:
                term.stdout.blue(group.name).end()
            with term:
                for c in group.commands:
                    if skip:
                        c.skip()
                    c()
                    if c.__getattr__('exit-on-fail') and c.state != Command.STATE_SUCCESSFUL:
                        skip = True
        if self.should_collapse:
            self._print_collapsed_run()

    def _print_summary(self):
        term.stdout.blue(len([c for c in self.commands]))(' commands. ') \
                   .green(len(list(filter(lambda c: c.state == Command.STATE_SUCCESSFUL, self.commands))))(' successfull, ') \
                   .red(len(list(filter(lambda c: c.state == Command.STATE_FAILED, self.commands))))(' failed, ') \
                   .yellow(len(list(filter(lambda c: c.state == Command.STATE_SIGNALED, self.commands))))(' signaled, ') \
                   .blue(len(list(filter(lambda c: c.state == Command.STATE_TIMED_OUT, self.commands))))(' timed out, ') \
                   (len(list(filter(lambda c: c.state == Command.STATE_SKIPPED, self.commands))))(' skipped') \
                   .end(newline=self.have_trailing_newline)

    def _set_animation_timer(self):
        if self.animation_enabled:
            signal.signal(signal.SIGALRM, self._animate)
            signal.setitimer(signal.ITIMER_REAL, 0.1, 0.1)

    def _stop_animation_timer(self):
        if self.animation_enabled:
            signal.setitimer(signal.ITIMER_REAL, 0, 0)

    def _save_result(self):
        if 'save-output' in self._settings and self._settings['save-output']:
            self._save(normalize(self._settings['save-output']))

    @property
    def have_trailing_newline(self):
        for p in parent_exes():
            if os.path.basename(p) in ('entr', 'watch'):
                return False
        return True

    def _reap_children(self):
        my_pid = os.getpid()
        while True:
            children_pids = [c for c in children(my_pid)]
            if not children_pids:
                return
            for child in children_pids:
                try:
                    os.kill(child, signal.SIGKILL)
                    os.waitpid(child, os.WSTOPPED)
                except:
                    pass

    @property
    def animation_enabled(self):
        return ('animation' not in self._settings) or \
                is_true(self._settings['animation'])

    def __call__(self):
        self._print_pre_run()
        self._set_animation_timer()
        self._run()
        self._print_summary()
        self._stop_animation_timer()
        self._save_result()
        self._reap_children()

        return 0 if all(c.state == Command.STATE_SUCCESSFUL for c in self.commands) else 1

def main(test_config_path='./bugger.json'):
    if not os.path.isfile(test_config_path):
        term.stderr.blue(test_config_path).red(' is missing or not a file\n')
    else:
        try:
            with open(test_config_path) as f:
                r = Bugger(json.loads(f.read()), test_config_path)
                return r()
        except PermissionError:
            term.stderr.red('Could not open ').blue(test_config_path).red(' for reading')
        except json.decoder.JSONDecodeError:
            term.stderr.blue(test_config_path).red(' is not valid JSON')
    return 1

if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
