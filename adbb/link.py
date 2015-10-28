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
from time import time, sleep
import threading
from adbb.responses import ResponseResolver
from adbb.errors import *
import adbb.commands

#from Crypto.Cipher import AES


class AniDBLink(threading.Thread):
    def __init__(
            self, 
            user, 
            pwd, 
            server='api.anidb.info', 
            port=9000, 
            myport=9876, 
            timeout=20, 
            logPrivate=False):
        super(AniDBLink, self).__init__()
        self.server = server
        self.port = port
        self.user = user
        self.pwd = pwd
        self.target = (server, port)
        self.timeout = timeout

        self.myport = 0
        self.bound = self.connectSocket(myport, self.timeout)

        self.cmd_queue = {None:None}
        self.resp_tagged_queue = {}
        self.resp_untagged_queue = []
        self.current_tag = 0
        self.tags = []

        # delaying vars
        self.lastpacket = time()
        self.counter = 0
        self.delay = 2
        # banned indicates number of retries done.
        # when banned > 0 the sender will wait max(2^banned, 48) hours until
        # trying again. banned == 0 == not banned :)
        self.banned = 0

        self.session = None
        self.crypt = None

        self.log = adbb._log
        self.logPrivate = logPrivate

        self._stop = threading.Event()
        self._auth_lock = threading.Lock()
        self._authenticating = threading.Event()
        self._authed = threading.Event()
        self._quitting = False
        self.setDaemon(True)
        self.start()

    def _reauthenticate(self):
        with self._auth_lock:
            if self._authenticating.is_set() or self._authed.is_set():
                return
            self._authenticating.set()

        req = adbb.commands.AuthCommand(
                self.user, 
                self.pwd,
                adbb.anidb_api_version,
                adbb.anidb_client_name,
                adbb.anidb_client_version,
                nat=1)
        self.request(req, self._auth_handler)

    def _auth_handler(self, resp):
        self.banned = 0
        with self._auth_lock:
            self._authed.set()
            self._authenticating.clear()

    def _logout_handler(self, resp):
        self._stop.set()

    def connectSocket(self, myport, timeout):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        portlist = [myport] + [7654]
        for port in portlist:
            try:
                self.sock.bind(('', port))
            except:
                continue
            else:
                self.myport = port
                return True
        else:
            return False;

    def disconnectSocket(self):
        self.sock.close()

    def stop (self):
        if self._authed.isSet():
            self.log.debug("Logging out from AniDB")
            req = adbb.commands.LogoutCommand()
            self.request(req, self._logout_handler)
            self._stop.wait(self.timeout)
        if not self._stop.isSet():
            self._stop.set()
        self.log.debug("Closing listening socket")
        self._quitting = True
        self.disconnectSocket()

    def stopped (self):
        return self._stop.isSet()

    def print_log(self, data):
        print(data)

    def print_log_dummy(self, data):
        pass

    def run(self):
        while not self._quitting:
            self.sock.settimeout(self.timeout)
            try:
                data = self.sock.recv(8192)
            except socket.timeout:
                self._handle_timeouts()
                continue
            self.log.debug("NetIO < %s" % repr(data))
            for i in range(2):
                tmp = data
                resp = None
                if tmp[:2] == '\x00\x00':
                    tmp = zlib.decompressobj().decompress(tmp[2:])
                    self.log.debug("UnZip | %s" % repr(tmp))
                if self.crypt:
                    tmp = self.crypt.decrypt(tmp)
                resp = ResponseResolver(tmp)
            if not resp:
                raise AniDBPacketCorruptedError("Either decrypting, decompressing or parsing the packet failed")
            cmd = self._cmd_dequeue(resp)
            resp = resp.resolve(cmd)
            resp.parse()
            if resp.rescode in ('200', '201'):
                self.session = resp.attrs['sesskey']
            elif resp.rescode in ('209',):
                self.log.error("sorry encryption is not supported")
                raise
                #self.crypt=AES.new(hashlib.md5(
                #    u'{}{}'.format(resp.req.apipassword,resp.attrs['salt']).\
                #        encode('utf-8')).digest(),
                #        AES.MODE_ECB)
            elif resp.rescode in ('501', '506', '403'):
                self._authed.clear()
                self._reauthenticate()
                self.tags.remove(resp.restag)
                self.request(cmd, cmd.callback)
                continue
            elif resp.rescode in ('203', '500', '503'):
                self.session = None
                self.crypt = None
            elif resp.rescode in ('504', '555'):
                try:
                    reason = resp.datalines[0]
                except IndexError:
                    reason = resp.resstr
                self.log.error("Oh no! AniDB says I'm banned: {}".\
                        format(reason))
                self.banned += 1
                self._authed.clear()
                self._reauthenticate()
                self.tags.remove(resp.restag)
                self.request(cmd, cmd.callback)

            resp.handle()
            if not cmd:
                self._resp_queue(resp)
            else:
                self.tags.remove(resp.restag)

    def _handle_timeouts(self):
        willpop = []
        for tag, cmd in self.cmd_queue.items():
            if not tag:
                continue
            if time() - cmd.started > self.timeout:
                self.tags.remove(cmd.tag)
                willpop.append(cmd.tag)

        for tag in willpop:
            if isinstance(cmd, adbb.commands.AuthCommand):
                self._reauthenticate()
            cmd = self.cmd_queue.pop(tag)
            cmd.handle_timeout(self)

    def _resp_queue(self, response):
        if response.restag:
            self.resp_tagged_queue[response.restag] = response
        else:
            self.resp_untagged_queue.append(response)

    def getresponse(self, command):
        if command:
            resp = self.resp_tagged_queue.pop(command.tag)
        else:
            resp = self.resp_untagged_queue.pop()
        self.tags.remove(resp.restag)
        return resp

    def _cmd_queue(self, command):
        self.tags.append(command.tag)
        self.cmd_queue[command.tag] = command

    def _cmd_dequeue(self, resp):
        if not resp.restag:
            return None
        else:
            return self.cmd_queue.pop(resp.restag)

    def _delay(self):
        age = time() - self.lastpacket
        if age > 120:
            self.counter = 0
            self.delay = 0
        elif self.counter < 5:
            self.delay = 2
        else:
            self.delay = 6

        return (self.delay < 2.1 and 2.1 or self.delay)

    def _do_delay(self):
        if self.banned > 0:
            delay = max(pow(2*3600, self.banned), 48*3600)
            self.log.info("Banned, sleeping for {} hours".format(delay/3600))
            sleep(delay)
            self.log.info("Slept well, let's see if we're still banned...")
            return
        age = time() - self.lastpacket
        delay = self._delay()
        if age <= delay:
            sleep(delay - age)

    def _send(self, command):
        if command.command != 'AUTH':
            if not self._authed.is_set():
                self._reauthenticate()
            self._authed.wait()
        self._do_delay()
        if not self.session and command.command not in ('AUTH', 'PING', 'ENCRYPT'):
            raise AniDBMustAuthError("You must be authed to execute command {}".format(command.command))
        command.authorize(self.session)
        self.counter += 1
        self.lastpacket = time()
        command.started = time()
        data = command.raw_data().encode('utf-8')
        
        if command.command == 'AUTH' and not self.logPrivate:
            self.log.debug("NetIO > sensitive data is not logged!")
        else:
            self.log.debug("NetIO > %s" % repr(data))

        if self.crypt:
            data = self.crypt.encrypt(data)

        self.sock.sendto(data, self.target)

    def new_tag(self):
        if self.current_tag == 100:
            self.current_tag = 0
            newtag = "TOOO"
        else:
            newtag = "T{:03d}".format(self.current_tag+1)
            self.current_tag += 1
        return newtag

    def request(self, command, callback):
        command.started = time()
        command.callback = callback
        command.tag = self.new_tag()
        self._cmd_queue(command)
        self._send(command)
