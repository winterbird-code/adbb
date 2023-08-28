#!/usr/bin/env python
#
# This file is part of adbb.
#
# adbb is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# adbb is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with adbb.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import hashlib
import socket, sys, zlib
import threading
from time import time, sleep
from collections import deque

from adbb.responses import ResponseResolver
from adbb.errors import *
import adbb.commands

from Crypto.Cipher import AES

class AniDBLink(threading.Thread):
    def __init__(self,
                    user,
                    pwd,
                    #host='localhost',
                    host='api.anidb.net',
                    port=9000,
                    myport=9876,
                    nat_ping_interval=600,
                    timeout=20,
                    api_key=None):
        super(AniDBLink, self).__init__()
        self._user = user
        self._pwd = pwd
        self._server = (host, port)
        self._queue = deque()

        self._last_packet = 0
        self._counter = 0
        self._banned = 0

        self._current_tag = 0
        self._myport = myport
        self._nat_ping_interval = nat_ping_interval
        self._do_ping = False
        self._listener = AniDBListener(self, myport=myport,timeout=timeout)

        self.timeout = timeout
        self._stop = threading.Event()
        self._authed = threading.Event()
        self._authenticating = threading.Event()
        self._auth_lock = threading.Lock()
        self._session = None

        self._api_key=api_key

        self.daemon = True
        self.start()

    def _logout_handler(self, resp):
        adbb.log.info(f"Logged out from AniDB")
        self._stop.set()

    def _start_encrypted_session(self):
        req = adbb.commands.EncryptCommand(
                self._user,
                self._api_key,
                "1")
        self.request(req, self._encryption_handler)

    def _encryption_handler(self, resp):
        self._session_key = hashlib.md5(bytes(self._api_key + resp.attrs['salt'], 'utf-8')).digest()
        self._listener._cipher = AES.new(self._session_key, AES.MODE_ECB)
        adbb.log.info('Encrypted session established')
        self._banned = 0
        self._send_auth()

    def _send_auth(self):
        if self._api_key and not self._listener._cipher:
            adbb.log.error('Tried to do unencrypted auth but API Key is set!')
            return
        req = adbb.commands.AuthCommand(
                self._user, 
                self._pwd,
                adbb.anidb_api_version,
                adbb.anidb_client_name,
                adbb.anidb_client_version,
                nat=1)
        self.request(req, self._auth_handler)


    def _reauthenticate(self):
        with self._auth_lock:
            if self._authenticating.is_set() or self._authed.is_set():
                return
            self._authenticating.set()
        if self._api_key:
            self._start_encrypted_session()
        else:
            self._send_auth()

    def _auth_handler(self, resp):
        self._banned = 0
        addr = resp.attrs['address']
        ip, port = addr.split(':')
        port = int(port)
        if port != self._myport:
            self._do_ping = True
            adbb.log.info("NAT detected: will send PING every {} seconds".format(
                    self._nat_ping_interval))
        with self._auth_lock:
            self._authed.set()
            self._authenticating.clear()
        adbb.log.info(f"Logged in to AniDB with session {self._session}")

    def _new_tag(self):
        if self._current_tag >= 999:
            self._current_tag = 0
            newtag = "TOOO"
        else:
            self._current_tag += 1
            newtag = "T{:03d}".format(self._current_tag)
        return newtag

    def _do_delay(self):
        if self._banned > 0:
            delay = 1800*self._banned
            adbb.log.warning(f"API not available, will wait for {delay/60} minutes")
            sleep(delay)
        age = time() - self._last_packet
        if age > 600:
            self._counter = 0
            delay = 0
        elif self._counter < 5:
            delay = 2
        else:
            delay = 4
        delay = delay-age
        if delay > 0:
            adbb.log.debug("Delaying request with {} seconds".format(delay))
            sleep(delay)

    def _ping_callback(self, _resp):
        adbb.log.debug(f"Successful session refresh")

    def run(self):
        # can't figure out a better way than to do a busy-wait here :/
        while True:
            while len(self._queue) < 1:
                sleep(0.2)
                time_since_cmd = time()-self._last_packet
                if self._authed.is_set() \
                        and self._do_ping \
                        and time_since_cmd > self._nat_ping_interval:
                    command = adbb.commands.PingCommand()
                    self.request(command, self._ping_callback)
                elif self._authed.is_set() and time_since_cmd >= 1800:
                    command = adbb.commands.UptimeCommand()
                    adbb.log.debug("Session idle for 30 minutes, sending UPTIME command")
                    self.request(command, self._ping_callback)

            command = self._queue.pop()
            adbb.log.debug("sending command {} with tag {}".format(
                    command.command, command.tag))
            if self._authed.is_set() or command.command in ('AUTH', 'ENCRYPT', 'PING'):
                self._send_command(command)
            else:
                self.reauthenticate()
                self._authed.wait()
                self._send_command(command)

            if command.command == 'LOGOUT':
                break

    def _send_command(self, command):
        self._do_delay()
        if not self._listener.is_alive():
            adbb.log.error('Listener has died; aborting')
            raise AniDBInternalError('Listener has died')
        if not self._session and command.command not in ('AUTH', 'PING', 'ENCRYPT'):
            raise AniDBMustAuthError("You must be authed to execute command {}".format(command.command))
        if command.command == 'AUTH' and self._authed.is_set():
            adbb.log.warning('Attempted double auth; ignoring')
            return
        elif command.command == 'ENCRYPT' and self._listener._cipher:
            adbb.log.warning('Attempted double encrypt command; ignoring')
            return
        command.authorize(self._session)
        self._counter += 1
        self._last_packet = time()
        command.started = time()
        data = command.raw_data().encode('utf-8')
        if self._listener._cipher:
            data = self._listener.encrypt(data)
        
        if command.command == 'AUTH':
            adbb.log.debug("NetIO > AUTH data is not logged!")
        else:
            adbb.log.debug("NetIO > %s" % repr(data))

        try:
            self._listener.sock.sendto(data, self._server)
        except socket.gaierror as e:
            adbb.log.warning(f'Failed to send command {command.command}: {e}')
            if command.command not in ('AUTH', 'PING', 'ENCRYPT'):
                self._queue.append(command)
            self.set_banned(code=999, reason=b'Network unavailable')

    def request(self, command, callback, prio=False):
        command.started = None
        command.callback = callback
        command.tag = self._new_tag()
        self._listener.cmd_queue[command.tag] = command
        adbb.log.debug("Queued command {} with tag {}".format( command.command, command.tag))
        if command.command in ('ENCRYPT', 'AUTH', 'PING'):
            self._send_command(command)
            return
        if prio:
            self._queue.append(command)
        else:
            self._queue.appendleft(command)

    def set_session(self, session):
        self._session = session

    def reauthenticate(self):
        self._authed.clear()
        self._session = None
        self._listener._cipher = None
        self._reauthenticate()

    def stop(self):
        if self._authed.is_set():
            adbb.log.debug("Logging out from AniDB")
            req = adbb.commands.LogoutCommand()
            self.request(req, self._logout_handler)
            self._stop.wait(self.timeout)
        else:
            self._listener.stop()

    def set_banned(self, code, reason=None):
        adbb.log.error("Backing off: {}".format(reason))
        if not self._banned:
            self._banned = 1
        else:
            self._banned *= 2
        with self._auth_lock:
            self._authenticating.clear()
        self.reauthenticate()


class AniDBListener(threading.Thread):
    def __init__(
            self, 
            sender,
            myport=9876, 
            timeout=20):
        super(AniDBListener, self).__init__()

        self.timeout = timeout
        self.sock = self._connect_socket(myport, self.timeout)
        self._sender = sender
        self._cipher = None
        self._last_receive = time()

        self.cmd_queue = {}

        self.daemon = True
        self.start()

    def _connect_socket(self, myport, timeout):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', myport))
        return sock

    def _disconnect_socket(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def encrypt(self, data):
        pad_len = 16-len(data) % 16
        padding = (chr(pad_len)*pad_len).encode('utf-8')
        data = data + padding
        return self._cipher.encrypt(data)

    def decrypt(self, data):
        data = self._cipher.decrypt(data)
        pad_len = data[-1]
        return data[:-pad_len]


    def stop(self):
        adbb.log.debug("Closing listening socket")
        self._disconnect_socket()

    def run(self):
        while self.sock:
            self.sock.settimeout(self.timeout)
            try:
                adbb.log.debug("Listening on socket with {}s timeout".format(self.sock.gettimeout()))
                data = self.sock.recv(8192)
            except socket.timeout:
                self._handle_timeouts()
                continue
            except OSError:
                continue
            adbb.log.debug("NetIO < %s" % repr(data))
            if self._cipher:
                try:
                    data = self.decrypt(data)
                except ValueError:
                    pass
            for i in range(2):
                tmp = data
                resp = None
                if tmp[:2] == b'\x00\x00':
                    tmp = zlib.decompressobj().decompress(tmp[2:])
                    adbb.log.debug("UnZip | %s" % repr(tmp))
                resp = ResponseResolver(tmp)
            if not resp:
                adbb.log.warning(f"Invalid response: {data}")
                continue
            if resp.restag:
                if resp.restag in self.cmd_queue:
                    cmd = self.cmd_queue.pop(resp.restag)
                else:
                    continue
            else:
                # No responsetag... we're probably banned
                try:
                    code=int(data[:3])
                except ValueError:
                    adbb.log.critical(f"Unparseable response from API: {repr(data)}")
                    sys.exit(2)
                reason = resp.resstr
                if code in (600, 601, 602, 604):
                    self._sender.set_banned(code=code, reason=reason)
                elif code in (598,):
                    # We get here if an encrypted session has timed out
                    # No need to log in again if all that's left in queue is a
                    # logout command.
                    if all([x.command == 'LOGOUT' for x in self.cmd_queue.values()]):
                        self.stop()
                    else:
                        adbb.log.warning('Lost encrypted session with AniDB; attempting to reauthenticate')
                        self._sender.reauthenticate()
                else:
                    adbb.log.critical(f'Unhandled response from API: {repr(data)}')
                    sys.exit(2)
                self._last_receive = time()
                continue
            resp = resp.resolve(cmd)
            resp.parse()
            if resp.rescode in ('200', '201'):
                self._sender.set_session(resp.attrs['sesskey'])
            elif resp.rescode in ('501', '506', '403'):
                if cmd.command == 'LOGOUT':
                    self.stop()
                else:
                    adbb.log.warning('Lost session with AniDB; attempting to reauthenticate')
                    self._sender.reauthenticate()
                    self._sender.request(cmd, cmd.callback, prio=True)
                self._last_receive = time()
                continue
            elif resp.rescode in ('203', '500', '503'):
                self.stop()

            self._last_receive = time()
            resp_thread = threading.Thread(target=resp.handle)
            resp_thread.daemon = True
            resp_thread.start()

    def _handle_timeouts(self):
        willpop = []
        cmd = None
        now = time()
        for tag, cmd in self.cmd_queue.items():
            if not tag:
                continue
            if cmd.started:
                adbb.log.debug("Command {} started at {} (now {})".format(
                        tag, cmd.started, time()))
                if now - cmd.started > self.timeout:
                    willpop.append(tag)

        for tag in willpop:
            cmd = self.cmd_queue.pop(tag)
            if cmd.started < self._last_receive:
                # API isn't dead yet, probably reauthenticating
                self._sender.request(cmd, cmd.callback, prio=True)
            else:
                adbb.log.warning("Command {} timed out".format(tag))
                cmd.handle_timeout(self._sender)
