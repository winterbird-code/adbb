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

#from Crypto.Cipher import AES

class AniDBLink(threading.Thread):
    def __init__(self,
                    user,
                    pwd,
                    #host='localhost',
                    host='api.anidb.info',
                    port=9000,
                    myport=9876,
                    nat_ping_interval=600,
                    timeout=20):
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

        self.daemon = True
        self.start()

    def _logout_handler(self, resp):
        self._stop.set()

    def _reauthenticate(self):
        with self._auth_lock:
            if self._authenticating.is_set() or self._authed.is_set():
                return
            self._authenticating.set()

        req = adbb.commands.AuthCommand(
                self._user, 
                self._pwd,
                adbb.anidb_api_version,
                adbb.anidb_client_name,
                adbb.anidb_client_version,
                nat=1)
        self.request(req, self._auth_handler)

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
            delay = max(pow(2*3600, self._banned), 1*3600)
            adbb.log.info("Banned, sleeping for {} hours".format(delay / 3600))
            sleep(delay)
            adbb.log.info("Slept well, let's see if we're still banned...")
            self._reauthenticate()
            return
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

    def run(self):
        # can't figure out a better way than to do a busy-wait here :/
        while True:
            while len(self._queue) < 1:
                sleep(0.2)
                if self._authed.is_set() \
                        and self._do_ping \
                        and time() - self._last_packet > self._nat_ping_interval:
                    command = adbb.commands.PingCommand()
                    self.request(command)
            command = self._queue.pop()
            adbb.log.debug("sending command {} with tag {}".format(
                    command.command, command.tag))
            if command.command != 'AUTH':
                if not self._authed.is_set():
                    self._reauthenticate()
                self._authed.wait()
            self._send_command(command)
            if command.command == 'LOGOUT':
                break

    def _send_command(self, command):
        self._do_delay()
        if not self._session and command.command not in ('AUTH', 'PING', 'ENCRYPT'):
            raise AniDBMustAuthError("You must be authed to execute command {}".format(command.command))
        command.authorize(self._session)
        self._counter += 1
        self._last_packet = time()
        command.started = time()
        data = command.raw_data().encode('utf-8')
        
        if command.command == 'AUTH':
            adbb.log.debug("NetIO > AUTH data is not logged!")
        else:
            adbb.log.debug("NetIO > %s" % repr(data))

        self._listener.sock.sendto(data, self._server)

    def request(self, command, callback, prio=False):
        command.started = None
        command.callback = callback
        command.tag = self._new_tag()
        self._listener.cmd_queue[command.tag] = command
        adbb.log.debug("Queued command {} with tag {}".format(
                command.command, command.tag))
        # special case, AUTH command should not be queued but sent asap.
        if command.command == 'AUTH':
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
        self._reauthenticate()

    def stop(self):
        if self._authed.isSet():
            adbb.log.debug("Logging out from AniDB")
            req = adbb.commands.LogoutCommand()
            self.request(req, self._logout_handler)
            self._stop.wait(self.timeout)
        else:
            self._listener.stop()

    def set_banned(self, reason=None):
        adbb.log.error("Oh no! I'm banned: {}".format(reason))
        self._banned += 1
        self._authed.clear()
        self._session = 0
        self._reauthenticate()


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
        self.sock.close()
        self.sock = None

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
            adbb.log.debug("NetIO < %s" % repr(data))
            for i in range(2):
                tmp = data
                resp = None
                if tmp[:2] == b'\x00\x00':
                    tmp = zlib.decompressobj().decompress(tmp[2:])
                    adbb.log.debug("UnZip | %s" % repr(tmp))
                resp = ResponseResolver(tmp)
            if not resp:
                raise AniDBPacketCorruptedError("Either decrypting, decompressing or parsing the packet failed")
            if resp.restag:
                if resp.restag in self.cmd_queue:
                    cmd = self.cmd_queue.pop(resp.restag)
                else:
                    continue
            else:
                # No responsetag... we're probably banned
                adbb.log.critical("We've been banned from the anidb UDP API: {}".format(repr(data)))
                reason = resp.resstr
                self._sender.set_banned(reason=reason)
                continue
            resp = resp.resolve(cmd)
            resp.parse()
            if resp.rescode in ('200', '201'):
                self._sender.set_session(resp.attrs['sesskey'])
            elif resp.rescode in ('209',):
                raise AniDBError("sorry encryption is not supported")
            elif resp.rescode in ('501', '506', '403'):
                self._sender.reauthenticate()
                self._sender.request(cmd, cmd.callback, prio=True)
                continue
            elif resp.rescode in ('203', '500', '503'):
                self.stop()

            resp_thread = threading.Thread(target=resp.handle)
            resp_thread.daemon = True
            resp_thread.start()

    def _handle_timeouts(self):
        willpop = []
        adbb.log.debug("Timeout; commands in queue: {}".format(self.cmd_queue))
        cmd = None
        for tag, cmd in self.cmd_queue.items():
            if not tag:
                continue
            if cmd.started:
                adbb.log.debug("Command {} started at {} (now {}".format(
                        tag, cmd.started, time()))
                if time() - cmd.started > self.timeout:
                    adbb.log.warning("Command {} timed out".format(tag))
                    willpop.append(tag)

        for tag in willpop:
            if isinstance(cmd, adbb.commands.AuthCommand):
                self._sender.reauthenticate()
            cmd = self.cmd_queue.pop(tag)
            cmd.handle_timeout(self._sender)
