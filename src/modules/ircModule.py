################################################################################
#   (c) 2011, The Honeynet Project
#   Author: Patrik Lantz  patrik@pjlantz.com
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
################################################################################


import re
from utils import *
import moduleManager
from twisted.internet import reactor, defer
from twisted.internet.protocol import Protocol, ClientFactory


@moduleManager.register("irc")
def setup_module(config, hash):
    """
    Function to register modules, simply
    implement this to pass along the config
    to the module object and return it back
    """

    return IRC(config, hash)


class IRC(moduleInterface.Module):
    """
    Implementation of a irc client to do irc based
    botnet monitoring
    """

    def __init__(self, config, hash):
        """
        Constructor sets up configs and moduleCoordinator object
        """
        
        self.hash = hash
        self.config = config
        self.prox = proxySelector.ProxySelector()
         
    def run(self):
        """
        Start execution
        """
        
        factory   = IRCClientFactory(self.hash, self.config, self)
        host      = self.config['botnet']
        port      = int(self.config['port'])
        proxyInfo = self.prox.getRandomProxy()

        if not proxyInfo:
            self.connector = reactor.connectTCP(host, port, factory)
            return

        proxyHost = proxyInfo['HOST']
        proxyPort = proxyInfo['PORT']
        proxyUser = proxyInfo['USER']
        proxyPass = proxyInfo['PASS']
        socksify = socks5.ProxyClientCreator(reactor, factory)
        if len(proxyUser) == 0:
            self.connector = socksify.connectSocks5Proxy(host, port, proxyHost, proxyPort, "HALE")
        else:
            self.connector = socksify.connectSocks5Proxy(host, port, proxyHost, proxyPort, "HALE", proxyUser, proxyPass)
        
    def stop(self):
        """
        Stop execution
        """

        self.connector.disconnect()
                
    def getConfig(self):
        """
        Return specific configuration used by this module
        """
        
        return self.config
        
class IRCProtocol(Protocol):
    """
    Protocol taking care of connection
    """

    factory = None

    @property
    def _ConnectPasswordCmd(self):
        return "%s %s\r\n" % (self.factory.config['pass_grammar'],
                              self.factory.config['password'], )

    def doConnectPassword(self):
        if self.factory.config['password']:
            self.transport.write(self._ConnectPasswordCmd)

    @property
    def _SendNickCmd(self):
        return "%s %s\r\n" % (self.factory.config['nick_grammar'],
                              self.factory.config['nick'], )

    def doSendNick(self):
        self.transport.write(self._SendNickCmd)

    @property
    def _SendUserCmd(self):
         return "%s %s %s %s :%s\r\n" % (self.factory.config['user_grammar'],
                                         self.factory.config['username'], 
                                         self.factory.config['username'],
                                         self.factory.config['username'],
                                         self.factory.config['realname'])

    def doSendUser(self):
        self.transport.write(self._SendUserCmd)

    def connectionMade(self):
        """
        When connection is made
        """
        
        self.doConnectPassword()
        self.doSendNick()
        self.doSendUser()

    def checkIsPing(self, data):
        return (data.find(self.factory.config['ping_grammar']) != -1)

    @property
    def _JoinWithPassCmd(self):
        return "%s %s %s\r\n" % (self.factory.config['join_grammar'],
                                 self.factory.config['channel'],
                                 self.factory.config['channel_pass'], )

    @property
    def _JoinWithoutPassCmd(self):
        return "%s %s\r\n" % (self.factory.config['join_grammar'],
                              self.factory.config['channel'], )

    def doSendPong(self, data):
        pong = "%s %s\r\n" % (self.factory.config['pong_grammar'], 
                              data.split()[1])
        self.transport.write(pong)

    def doSendJoin(self):
        if not self.factory.firstPing:
            return

        if self.factory.config['channel_pass']:
            joinCmd = _JoinWithPassCmd
        else:
            joinCmd = _JoinWithoutPassCmd
        
        self.transport.write(joinCmd)
        self.factory.firstPing = False

    def checkIsTopic(self, data):
        return (data.find(self.factory.config['topic_grammar']) != -1)

    def checkIsCurrentTopic(self, data):
        return (data.find(self.factory.config['currenttopic_grammar']) != -1)

    def checkIsPrivMsg(self, data):
        return (data.find(self.factory.config['privmsg_grammar']) != -1)

    def checkIsVersion(self, data):
        return (data.find(self.factory.config['version_grammar']) != -1)

    def checkIsTime(self, data):
        return (data.find(self.factory.config['time_grammar']) != -1)

    def dataReceived(self, data):
        """
        Data is received
        """

        checkHost  = None
        match      = None
        firstline  = None
        secondline = None

        s_data = data.split(':')
        if len(s_data) > 1:
            checkHost = s_data[1].split(' ')[0].strip()

        if checkHost:
            match = self.factory.expr.findall(checkHost)

        t_data = data.split('@')
        if match and len(t_data) > 1:
            self.factory.addRelIP(t_data[1].split(' ')[0].strip())

        self.factory.checkForURL(data)
        
        if self.checkIsPing(data):              # PING
            self.doSendPong(data) 
            self.doSendJoin()

        elif self.checkIsTopic(data):           # TOPIC
           self.factory.putLog(data)

        elif self.checkIsCurrentTopic(data):    # RPL_TOPIC
            for line in data.split('\r\n'):
                if not firstline:
                    s_line = line.split(self.factory.config['nick'])
                    if len(s_line) < 2:
                        continue

                    firstline = s_line[1].strip().split(' ')
                    if len(firstline) < 2:
                        firstline = None
                        continue

                    chan  = firstline[0].strip()
                    topic = firstline[1].strip()
                    
                    _topic = topic.split(":")
                    if len(_topic) > 1:
                        _topic = _topic[1]
                    else:
                        _topic = topic

                    msg = "CURRENTTOPIC Channel: %s Topic: %s" % (chan, _topic, )
                    self.factory.putLog(msg)
                    continue

                if not secondline:
                    secondline = line.split(self.factory.config['channel'])
                    if len(secondline) < 2:
                        secondline = None
                        continue
                        
                    secondline = secondline[1].strip()
                    setby      = secondline.split(' ')[0].strip()

                    msg = "CURRENTTOPIC Set by: %s" % (setby, )
                    self.factory.putLog(msg)
                    break
                  
        elif self.checkIsPrivMsg(data):         # PRIVMSG
            if not self.checkIsVersion(data) and not self.checkIsTime(data)::
                self.factory.putLog(data)

        else:                                   # Unrecognized commands
            if match and len(s_data) > 1:
                grammars = self.factory.config.values() 
                command  = sdata[1].split(' ')[1]
                if command not in grammars:
                    self.factory.putLog(data)
        
class IRCClientFactory(ClientFactory):
    """
    Clientfactory for the IRCProtocol
    """

    protocol = IRCProtocol # tell base class what proto to build

    def __init__(self, hash, config, module):
        """
        Constructor, sets first ping received flag
        and config to be used
        """
        
        self.module    = module
        self.expr      = re.compile('!~.*?@')
        self.config    = config
        self.firstPing = True
        self.hash      = hash

    def clientConnectionFailed(self, connector, reason):
        """
        Called on failed connection to server
        """

        moduleCoordinator.ModuleCoordinator().putError("Error connecting to " + self.config['botnet'], self.module)

    def clientConnectionLost(self, connector, reason):
        """
        Called on lost connection to server
        """

        moduleCoordinator.ModuleCoordinator().putError("Connection lost to " + self.config['botnet'], self.module)

    def putLog(self, log):
        """
        Put log to the event handler
        """
        
        moduleCoordinator.ModuleCoordinator().addEvent(moduleCoordinator.LOG_EVENT, log, self.hash, self.config)
        
    def checkForURL(self, data):
        """
        Check for URL in the event handler
        """
        
        moduleCoordinator.ModuleCoordinator().addEvent(moduleCoordinator.URL_EVENT, data, self.hash)
        
    def addRelIP(self, data):
        """
        Put possible ip related to the botnet being monitored
        in the event handler.
        """
        
        moduleCoordinator.ModuleCoordinator().addEvent(moduleCoordinator.RELIP_EVENT, data, self.hash)
        
    def __call__(self):
        """
        Used by the socks5 module to return
        the protocol handled by this factory
        """
        
        p = self.protocol()
        p.factory = self
        return p

