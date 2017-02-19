#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2016 Red Hat, Inc.
# Copyright (c) 2016 IBM Corp.
# (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>, and others
# (c) 2016, Toshio Kuratomi <tkuratomi@ansible.com>
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.

# flake8: noqa
# This file shares a significant chunk of code with an upstream ansible
# function, run_command. The goal is to not have to fork quite so much
# of that function, and discussing that design with upstream means we
# should keep the changes to substantive ones only. For that reason, this
# file is purposely not enforcing pep8, as making the function pep8 clean
# would remove our ability to easily have a discussion with our friends
# upstream

DOCUMENTATION = '''
---
module: command
short_description: Executes a command on a remote node
version_added: historical
description:
     - The M(command) module takes the command name followed by a list of space-delimited arguments.
     - The given command will be executed on all selected nodes. It will not be
       processed through the shell, so variables like C($HOME) and operations
       like C("<"), C(">"), C("|"), C(";") and C("&") will not work (use the M(shell)
       module if you need these features).
options:
  free_form:
    description:
      - the command module takes a free form command to run.  There is no parameter actually named 'free form'.
        See the examples!
    required: true
    default: null
  creates:
    description:
      - a filename or (since 2.0) glob pattern, when it already exists, this step will B(not) be run.
    required: no
    default: null
  removes:
    description:
      - a filename or (since 2.0) glob pattern, when it does not exist, this step will B(not) be run.
    version_added: "0.8"
    required: no
    default: null
  chdir:
    description:
      - cd into this directory before running the command
    version_added: "0.6"
    required: false
    default: null
  executable:
    description:
      - change the shell used to execute the command. Should be an absolute path to the executable.
    required: false
    default: null
    version_added: "0.9"
  warn:
    version_added: "1.8"
    default: yes
    description:
      - if command warnings are on in ansible.cfg, do not warn about this particular line if set to no/false.
    required: false
notes:
    -  If you want to run a command through the shell (say you are using C(<),
       C(>), C(|), etc), you actually want the M(shell) module instead. The
       M(command) module is much more secure as it's not affected by the user's
       environment.
    -  " C(creates), C(removes), and C(chdir) can be specified after the command. For instance, if you only want to run a command if a certain file does not exist, use this."
author:
    - Ansible Core Team
    - Michael DeHaan
'''

EXAMPLES = '''
# Example from Ansible Playbooks.
- command: /sbin/shutdown -t now

# Run the command if the specified file does not exist.
- command: /usr/bin/make_database.sh arg1 arg2 creates=/path/to/database

# You can also use the 'args' form to provide the options. This command
# will change the working directory to somedir/ and will only run when
# /path/to/database doesn't exist.
- command: /usr/bin/make_database.sh arg1 arg2
  args:
    chdir: somedir/
    creates: /path/to/database
'''

import datetime
import glob
import pipes
import re
import shlex
import os

import getpass
import select
import subprocess
import traceback
import threading

from ansible.module_utils.basic import AnsibleModule, heuristic_log_sanitize
from ansible.module_utils.basic import get_exception
# ZUUL: Hardcode python2 until we're on ansible 2.2
from ast import literal_eval


PASSWD_ARG_RE = re.compile(r'^[-]{0,2}pass[-]?(word|wd)?')


class Console(object):
    def __enter__(self):
        self.logfile = open('/tmp/console.html', 'a', 0)
        return self

    def __exit__(self, etype, value, tb):
        self.logfile.close()

    def addLine(self, ln):
        # Note this format with deliminator is "inspired" by the old
        # Jenkins format but with microsecond resolution instead of
        # millisecond.  It is kept so log parsing/formatting remains
        # consistent.
        ts = datetime.datetime.now()
        outln = '%s | %s' % (ts, ln)
        self.logfile.write(outln)


def follow(fd):
    newline_warning = False
    with Console() as console:
        while True:
            line = fd.readline()
            if not line:
                break
            if not line.endswith('\n'):
                line += '\n'
                newline_warning = True
            console.addLine(line)
        if newline_warning:
            console.addLine('[Zuul] No trailing newline\n')


# Taken from ansible/module_utils/basic.py ... forking the method for now
# so that we can dive in and figure out how to make appropriate hook points
def zuul_run_command(self, args, check_rc=False, close_fds=True, executable=None, data=None, binary_data=False, path_prefix=None, cwd=None, use_unsafe_shell=False, prompt_regex=None, environ_update=None):
    '''
    Execute a command, returns rc, stdout, and stderr.

    :arg args: is the command to run
        * If args is a list, the command will be run with shell=False.
        * If args is a string and use_unsafe_shell=False it will split args to a list and run with shell=False
        * If args is a string and use_unsafe_shell=True it runs with shell=True.
    :kw check_rc: Whether to call fail_json in case of non zero RC.
        Default False
    :kw close_fds: See documentation for subprocess.Popen(). Default True
    :kw executable: See documentation for subprocess.Popen(). Default None
    :kw data: If given, information to write to the stdin of the command
    :kw binary_data: If False, append a newline to the data.  Default False
    :kw path_prefix: If given, additional path to find the command in.
        This adds to the PATH environment vairable so helper commands in
        the same directory can also be found
    :kw cwd: If given, working directory to run the command inside
    :kw use_unsafe_shell: See `args` parameter.  Default False
    :kw prompt_regex: Regex string (not a compiled regex) which can be
        used to detect prompts in the stdout which would otherwise cause
        the execution to hang (especially if no input data is specified)
    :kwarg environ_update: dictionary to *update* os.environ with
    '''

    shell = False
    if isinstance(args, list):
        if use_unsafe_shell:
            args = " ".join([pipes.quote(x) for x in args])
            shell = True
    elif isinstance(args, (str, unicode)) and use_unsafe_shell:
        shell = True
    elif isinstance(args, (str, unicode)):
        # On python2.6 and below, shlex has problems with text type
        # ZUUL: Hardcode python2 until we're on ansible 2.2
        if isinstance(args, unicode):
            args = args.encode('utf-8')
        args = shlex.split(args)
    else:
        msg = "Argument 'args' to run_command must be list or string"
        self.fail_json(rc=257, cmd=args, msg=msg)

    prompt_re = None
    if prompt_regex:
        try:
            prompt_re = re.compile(prompt_regex, re.MULTILINE)
        except re.error:
            self.fail_json(msg="invalid prompt regular expression given to run_command")

    # expand things like $HOME and ~
    if not shell:
        args = [ os.path.expanduser(os.path.expandvars(x)) for x in args if x is not None ]

    rc = 0
    msg = None
    st_in = None

    # Manipulate the environ we'll send to the new process
    old_env_vals = {}
    # We can set this from both an attribute and per call
    for key, val in self.run_command_environ_update.items():
        old_env_vals[key] = os.environ.get(key, None)
        os.environ[key] = val
    if environ_update:
        for key, val in environ_update.items():
            old_env_vals[key] = os.environ.get(key, None)
            os.environ[key] = val
    if path_prefix:
        old_env_vals['PATH'] = os.environ['PATH']
        os.environ['PATH'] = "%s:%s" % (path_prefix, os.environ['PATH'])

    # If using test-module and explode, the remote lib path will resemble ...
    #   /tmp/test_module_scratch/debug_dir/ansible/module_utils/basic.py
    # If using ansible or ansible-playbook with a remote system ...
    #   /tmp/ansible_vmweLQ/ansible_modlib.zip/ansible/module_utils/basic.py

    # Clean out python paths set by ansiballz
    if 'PYTHONPATH' in os.environ:
        pypaths = os.environ['PYTHONPATH'].split(':')
        pypaths = [x for x in pypaths \
                    if not x.endswith('/ansible_modlib.zip') \
                    and not x.endswith('/debug_dir')]
        os.environ['PYTHONPATH'] = ':'.join(pypaths)
        if not os.environ['PYTHONPATH']:
            del os.environ['PYTHONPATH']

    # create a printable version of the command for use
    # in reporting later, which strips out things like
    # passwords from the args list
    to_clean_args = args
    # ZUUL: Hardcode python2 until we're on ansible 2.2
    if isinstance(args, (unicode, str)):
        to_clean_args = shlex.split(to_clean_args)

    clean_args = []
    is_passwd = False
    for arg in to_clean_args:
        if is_passwd:
            is_passwd = False
            clean_args.append('********')
            continue
        if PASSWD_ARG_RE.match(arg):
            sep_idx = arg.find('=')
            if sep_idx > -1:
                clean_args.append('%s=********' % arg[:sep_idx])
                continue
            else:
                is_passwd = True
        arg = heuristic_log_sanitize(arg, self.no_log_values)
        clean_args.append(arg)
    clean_args = ' '.join(pipes.quote(arg) for arg in clean_args)

    if data:
        st_in = subprocess.PIPE

    # ZUUL: changed stderr to follow stdout
    kwargs = dict(
        executable=executable,
        shell=shell,
        close_fds=close_fds,
        stdin=st_in,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if cwd and os.path.isdir(cwd):
        kwargs['cwd'] = cwd

    # store the pwd
    prev_dir = os.getcwd()

    # make sure we're in the right working directory
    if cwd and os.path.isdir(cwd):
        try:
            os.chdir(cwd)
        except (OSError, IOError):
            e = get_exception()
            self.fail_json(rc=e.errno, msg="Could not open %s, %s" % (cwd, str(e)))

    try:

        if self._debug:
            if isinstance(args, list):
                running = ' '.join(args)
            else:
                running = args
            self.log('Executing: ' + running)
        # ZUUL: Replaced the excution loop with the zuul_runner run function
        cmd = subprocess.Popen(args, **kwargs)
        t = threading.Thread(target=follow, args=(cmd.stdout,))
        t.daemon = True
        t.start()
        ret = cmd.wait()
        # Give the thread that is writing the console log up to 10 seconds
        # to catch up and exit.  If it hasn't done so by then, it is very
        # likely stuck in readline() because it spawed a child that is
        # holding stdout or stderr open.
        t.join(10)
        with Console() as console:
            if t.isAlive():
                console.addLine("[Zuul] standard output/error still open "
                                "after child exited")
            console.addLine("[Zuul] Task exit code: %s\n" % ret)

        # ZUUL: If the console log follow thread *is* stuck in readline,
        # we can't close stdout (attempting to do so raises an
        # exception) , so this is disabled.
        # cmd.stdout.close()

        # ZUUL: stdout and stderr are in the console log file
        stdout = ''
        stderr = ''

        rc = cmd.returncode
    except (OSError, IOError):
        e = get_exception()
        self.fail_json(rc=e.errno, msg=str(e), cmd=clean_args)
    except Exception:
        e = get_exception()
        self.fail_json(rc=257, msg=str(e), exception=traceback.format_exc(), cmd=clean_args)

    # Restore env settings
    for key, val in old_env_vals.items():
        if val is None:
            del os.environ[key]
        else:
            os.environ[key] = val

    if rc != 0 and check_rc:
        msg = heuristic_log_sanitize(stderr.rstrip(), self.no_log_values)
        self.fail_json(cmd=clean_args, rc=rc, stdout=stdout, stderr=stderr, msg=msg)

    # reset the pwd
    os.chdir(prev_dir)

    return (rc, stdout, stderr)


def check_command(commandline):
    arguments = { 'chown': 'owner', 'chmod': 'mode', 'chgrp': 'group',
                  'ln': 'state=link', 'mkdir': 'state=directory',
                  'rmdir': 'state=absent', 'rm': 'state=absent', 'touch': 'state=touch' }
    commands  = { 'hg': 'hg', 'curl': 'get_url or uri', 'wget': 'get_url or uri',
                  'svn': 'subversion', 'service': 'service',
                  'mount': 'mount', 'rpm': 'yum, dnf or zypper', 'yum': 'yum', 'apt-get': 'apt',
                  'tar': 'unarchive', 'unzip': 'unarchive', 'sed': 'template or lineinfile',
                  'dnf': 'dnf', 'zypper': 'zypper' }
    become   = [ 'sudo', 'su', 'pbrun', 'pfexec', 'runas' ]
    warnings = list()
    command = os.path.basename(commandline.split()[0])
    if command in arguments:
        warnings.append("Consider using file module with %s rather than running %s" % (arguments[command], command))
    if command in commands:
        warnings.append("Consider using %s module rather than running %s" % (commands[command], command))
    if command in become:
        warnings.append("Consider using 'become', 'become_method', and 'become_user' rather than running %s" % (command,))
    return warnings


def main():

    # the command module is the one ansible module that does not take key=value args
    # hence don't copy this one if you are looking to build others!
    module = AnsibleModule(
        argument_spec=dict(
          _raw_params = dict(),
          _uses_shell = dict(type='bool', default=False),
          chdir = dict(type='path'),
          executable = dict(),
          creates = dict(type='path'),
          removes = dict(type='path'),
          warn = dict(type='bool', default=True),
          environ = dict(type='dict', default=None),
        )
    )

    shell = module.params['_uses_shell']
    chdir = module.params['chdir']
    executable = module.params['executable']
    args  = module.params['_raw_params']
    creates  = module.params['creates']
    removes  = module.params['removes']
    warn = module.params['warn']
    environ = module.params['environ']

    if args.strip() == '':
        module.fail_json(rc=256, msg="no command given")

    if chdir:
        chdir = os.path.abspath(chdir)
        os.chdir(chdir)

    if creates:
        # do not run the command if the line contains creates=filename
        # and the filename already exists.  This allows idempotence
        # of command executions.
        if glob.glob(creates):
            module.exit_json(
                cmd=args,
                stdout="skipped, since %s exists" % creates,
                changed=False,
                rc=0
            )

    if removes:
    # do not run the command if the line contains removes=filename
    # and the filename does not exist.  This allows idempotence
    # of command executions.
        if not glob.glob(removes):
            module.exit_json(
                cmd=args,
                stdout="skipped, since %s does not exist" % removes,
                changed=False,
                rc=0
            )

    warnings = list()
    if warn:
        warnings = check_command(args)

    if not shell:
        args = shlex.split(args)
    startd = datetime.datetime.now()

    rc, out, err = zuul_run_command(module, args, executable=executable, use_unsafe_shell=shell, environ_update=environ)

    endd = datetime.datetime.now()
    delta = endd - startd

    if out is None:
        out = ''
    if err is None:
        err = ''

    module.exit_json(
        cmd      = args,
        stdout   = out.rstrip("\r\n"),
        stderr   = err.rstrip("\r\n"),
        rc       = rc,
        start    = str(startd),
        end      = str(endd),
        delta    = str(delta),
        changed  = True,
        warnings = warnings
    )

if __name__ == '__main__':
    main()
