from threading import Thread
from threading import Lock
from datetime import datetime
import time
import subprocess
from ast import literal_eval

class UIDConverter():
    @staticmethod
    def ToInt(uid:bytearray) -> int:
        if len(uid) != 4:
            raise Exception('invalid format')

        id = uid[3]
        id *= 256
        id += uid[2]
        id *= 256
        id += uid[1]
        id *= 256
        id += uid[0]

        return id

class ACR122U():
    def IsScannerInstalled(self) -> bool:
        if 'ACR122U' in self.__GetNFCListOutput():
            return True
        return False

    def IsCardConnected(self) -> bool:
        if 'UID' in self.__GetNFCListOutput():
            return True
        return False

    def GetUID(self) -> list:
        content = self.__GetNFCListOutput().split('\n')
        uidLines = list(filter(lambda x : 'UID' in x,content))
        if len(uidLines) != 1:
            raise Exception('Could not obtain UID from card')
        uid = uidLines[0].split(':')
        if len(uid) != 2:
            raise Exception('Could not obtain UID from card')
        uid = uid[1].strip().replace('  ',' ').split(' ')

        ret = []
        for x in uid:
            ret.append(literal_eval('0x' + x))
        return ret


    def __GetNFCListOutput(self) -> str:
        process = subprocess.Popen(['nfc-list'], stdout=subprocess.PIPE)
        return process.communicate()[0].decode("utf-8") 

class CardScannerThead(Thread):
    def __init__(self,newCardCallback = None,readErrorCard = None):
        Thread.__init__(self)
        self.__mutex = Lock()
        self.__uid = None
        self.__lastUpdate = None
        self.__run = True
        self.__stillConnected = False
        self.__newCardCallback = newCardCallback
        self.__readErrorCard = readErrorCard
        self.__reader = ACR122U()
        self.setDaemon(True)

    def GetLastCardInformation(self):
        self.__mutex.acquire()
        data = {'date': self.__lastUpdate ,'uid': self.__uid}
        self.__mutex.release()
        return data


    def run(self):
        while(self.__run):
            time.sleep(0.05)      

            if self.__stillConnected:
                self.__stillConnected = self.__reader.IsCardConnected()
            else:
                try:
                    uid =  self.__reader.GetUID().copy()

                    self.__mutex.acquire()
                    self.__uid = uid
                    self.__lastUpdate = datetime.now()
                    self.__mutex.release()

                    self.__stillConnected = True

                    if self.__newCardCallback:
                        self.__newCardCallback(self)
                except:
                    i = None





    def Kill(self):
        self.__run = True
        self.join
