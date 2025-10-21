import sqlite3
import os
from datetime import datetime
import time
from threading import Thread, Lock
import io

class Connector():
    def __init__(self):
        self.__databaseFile = os.getcwd() + '/coffee.db'
        self.__InitDatabase()

    def __InitDatabase(self):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS coffee_sorts(coffee_name TEXT, coffee_price REAL,coffee_machine_strokes INT)')
        cur.execute('CREATE TABLE IF NOT EXISTS user(user_uid INTEGER PRIMARY KEY, user_alt_uid INTEGER UNIQUE DEFAULT NULL, user_nick_name TEXT, user_favourite_coffee INT, FOREIGN KEY(user_favourite_coffee) REFERENCES coffee_sorts(rowid))')
        cur.execute('CREATE TABLE IF NOT EXISTS coffee_order(user_uid INTEGER, order_value REAL, order_coffee_machine_strokes INT, order_timestamp INT, order_datetime TEXT, order_info TEXT, FOREIGN KEY(user_uid) REFERENCES user(user_uid))')
        cur.execute('CREATE TABLE IF NOT EXISTS payments(user_uid INTEGER, payment_value REAL, payment_timestamp INT, payment_datetime TEXT, payment_info TEXT, FOREIGN KEY(user_uid) REFERENCES user(user_uid))')
        # Ensure user_alt_uid column exists (for older DBs)
        cur.execute("PRAGMA table_info(user)")
        cols = [row[1] for row in cur.fetchall()]
        if 'user_alt_uid' not in cols:
            # Add column and unique index to preserve original schema intent
            cur.execute("ALTER TABLE user ADD COLUMN user_alt_uid INTEGER DEFAULT NULL")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_alt_uid ON user(user_alt_uid)")
        con.commit()
        con.close()

    def GetDatabasePath(self):
        return self.__databaseFile

    def IsUserExisting(self,uid:int):
        return self.GetUserMeta(uid) != None

    def CreateNewUser(self,uid:int,nickName:str,favouriteCoffeeUid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO user(user_uid, user_alt_uid, user_nick_name, user_favourite_coffee) VALUES(?, NULL, ?, ?)', (uid, nickName, favouriteCoffeeUid))
        con.commit()
        con.close()

    def UpdateUser(self,uid:int,nickName:str,favouriteCoffeeUid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('UPDATE user SET user_nick_name = \'' + nickName + '\', user_favourite_coffee = ' + str(favouriteCoffeeUid) + ' WHERE user_uid = ' + str(uid))
        con.commit()
        con.close()

    def UpdateUserAltUID(self,uid:int,altUID:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('UPDATE user SET user_alt_uid = ' + str(altUID) + ' WHERE user_uid = ' + str(uid))
        con.commit()
        con.close()

    def GetUserList(self):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        try:
            cur.execute('SELECT user_uid, user_nick_name, user_favourite_coffee, user_alt_uid FROM user')
            rows = cur.fetchall()
            if len(rows) == 0:
                rows = None
        except:
            rows = None
        finally:
            con.close()

        if rows == None:
            return None

        ret = []
        for item in rows:
            ret.append({'uid': item[0], 'nick_name': item[1], 'favourite_coffee': item[2], 'user_alt_uid': item[3]})

        return ret

    def GetUserMeta(self,uid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        try:
            cur.execute('SELECT user_uid, user_nick_name, user_favourite_coffee, user_alt_uid FROM user WHERE user_uid = ? OR user_alt_uid = ?', (uid, uid))
            rows = cur.fetchall()
            if len(rows) == 0:
                rows = None
        except:
            rows = None
        finally:
            con.close()

        if rows == None:
            return None

        return {'uid': rows[0][0], 'nick_name': rows[0][1], 'favourite_coffee': rows[0][2], 'user_alt_uid': rows[0][3]}

    def DeleteUser(self,uid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('DELETE FROM user WHERE user_uid = ' + str(uid))
        con.commit()
        con.close()

    def CreateCoffeeSort(self,name:str,price:float,strokes:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO coffee_sorts VALUES(\'' + name + '\', ' + str(price) + ', ' + str(strokes) +')')
        con.commit()
        con.close()

    def UpdateCoffeeSort(self,name:str,price:float,strokes:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('UPDATE coffee_sorts SET coffee_price = ' + str(price) + ', coffee_machine_strokes = ' + str(strokes) + ' WHERE coffee_name = \'' + name + '\'')
        con.commit()
        con.close()

    def GetCoffeeSorts(self):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        try:
            cur.execute('SELECT rowid,* FROM coffee_sorts')
            rows = cur.fetchall()
            if len(rows) == 0:
                rows = None
        except:
            rows = None
        finally:
            con.close()

        if rows == None:
            return None

        ret = []
        for item in rows:
            ret.append({'coffee_id': item[0], 'coffee_name': item[1], 'coffee_price': item[2], 'coffee_machine_strokes': item[3]})

        return ret

    def GetCoffeeSort(self,uid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        try:
            cur.execute('SELECT rowid,* FROM coffee_sorts WHERE rowid = ' + str(uid))
            rows = cur.fetchall()
            if len(rows) == 0:
                rows = None
        except:
            rows = None
        finally:
            con.close()

        if rows == None:
            return None

        return {'coffee_id': rows[0][0], 'coffee_name': rows[0][1], 'coffee_price': rows[0][2], 'coffee_machine_strokes': rows[0][3]}

    def AddOrder(self,uid:int,value:float,strokes:int,note:str=''):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO coffee_order VALUES(' + str(uid) + ',' + str(value) + ',' + str(strokes) + ',' + str(int(time.time())) + ',\'' + str(datetime.now())  + '\',\''+ note +'\')')
        con.commit()
        con.close()

    def GetTotalCupsForUID(self,uid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT SUM(order_coffee_machine_strokes) FROM coffee_order WHERE user_uid = ' + str(uid))
        rows = cur.fetchall()
        con.close()
        
        if len(rows) == 0:
            return 0

        if rows[0][0] == None:
            return 0

        return int(rows[0][0])

    def GetCurrentMonthCupsForUID(self,uid:int):
        date = time.mktime(datetime(datetime.now().year,datetime.now().month,1,0,0,0).timetuple())
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT SUM(order_coffee_machine_strokes) FROM coffee_order WHERE user_uid = ' + str(uid)
                    + ' AND order_timestamp >= ' + str(date))
        rows = cur.fetchall()
        con.close()
        
        if len(rows) == 0:
            return 0

        if rows[0][0] == None:
            return 0

        return int(rows[0][0])

    def GetAccountBalance(self,uid:int,round_value:bool=True,for_year:int=-1,for_month:int=-1,):
        payments = self.GetPayments(uid,round_value=False)
        deposit = self.GetDeposit(uid,round_value=False,for_year=for_year,for_month=for_month)

        diff = payments - deposit
        if round_value:
            return round(diff,2)
        return diff

    def GetPayments(self,uid:int,round_value:bool=True) -> float:
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT SUM(payment_value) FROM payments WHERE user_uid = ' + str(uid))
        rows = cur.fetchall()
        con.close()

        if len(rows) == 0:
            return 0.0

        if rows[0][0] == None:
            return 0.0

        if round_value:
            return round(float(rows[0][0]),2)
        else:
            return float(rows[0][0])

    def GetDeposit(self,uid:int,for_year:int=-1,for_month:int=-1,round_value:bool=True) -> float:
        unixTimeSpanElements = self.__GetStartEndEndPoint(for_year,for_month) 
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        if unixTimeSpanElements[0] == None:
            cur.execute('SELECT SUM(order_value) FROM coffee_order WHERE user_uid = ' + str(uid))
        else:
            cur.execute('SELECT SUM(order_value) FROM coffee_order WHERE user_uid = ' + str(uid) 
                        + ' AND order_timestamp >= ' + str(unixTimeSpanElements[0])
                        + ' AND order_timestamp < ' + str(unixTimeSpanElements[1]))
        rows = cur.fetchall()
        con.close()
        
        if len(rows) == 0:
            return 0.0

        if rows[0][0] == None:
            return 0.0

        if round_value:
            return round(float(rows[0][0]),2)
        else:
            return float(rows[0][0])

    def __GetStartEndEndPoint(self,for_year,for_month):
        dateStart = None
        dateEnd = None
        if for_year > -1 or for_month > -1:
            if for_year == -1:
                for_year = datetime.now().year
            if for_month == -1:
                dateStart = datetime(for_year,1,1,0,0,0)
                dateEnd = datetime(for_year + 1,1,1,0,0,0)
            else:
                dateStart = datetime(for_year,for_month,1,0,0,0)
                if for_month != 12:
                    dateEnd = datetime(for_year,for_month + 1,1,0,0,0)
                else:
                    dateEnd = datetime(for_year + 1,1,1,0,0,0)

        if dateStart == None or dateEnd == None:
            return (None,None)

        return (time.mktime(dateStart.timetuple()),time.mktime(dateEnd.timetuple()))

    def AddPayment(self,uid:int,value:float):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO payments VALUES(' + str(uid) + ',' + str(value) + ',' + str(int(time.time())) + ',\'' + str(datetime.now())  + '\',\'\')')
        con.commit()
        con.close()

    def GetUIDSortedByTotalCupsDecreasing(self):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT user_uid, SUM(order_coffee_machine_strokes) FROM coffee_order GROUP BY user_uid ORDER BY SUM(order_coffee_machine_strokes) DESC')
        rows = cur.fetchall()
        con.close()

        ret = []

        try:
            for line in rows:
                ret.append(int(line[0]))
        except:
            ret = []

        return ret

    def GetUIDSortedByCurrentMonthsCupsDecreasing(self):
        date = time.mktime(datetime(datetime.now().year,datetime.now().month,1,0,0,0).timetuple())
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT user_uid, SUM(order_coffee_machine_strokes) FROM coffee_order'
                    + ' WHERE order_timestamp >= ' + str(date)
                    + ' GROUP BY user_uid ORDER BY SUM(order_coffee_machine_strokes) DESC')
        rows = cur.fetchall()
        con.close()

        ret = []

        try:
            for line in rows:
                ret.append(int(line[0]))
        except:
            ret = []

        return ret

    def CreateBackup(self,backupFile:str='coffee_backup.db'):
        con = sqlite3.connect(self.__databaseFile)
        with io.open(backupFile, 'w') as p:
            for line in con.iterdump():
                p.write('%s\n' % line)

    def GetPillorySortedByDecreasing(self):
        # Calculate the unix timestamp for the start of the current month.
        start_of_month = int(time.mktime(datetime(datetime.now().year, datetime.now().month, 1, 0, 0, 0).timetuple()))

        # Use a single aggregated query to compute, per user, the sum of payments and
        # the sum
        # 
        #  of order_value (deposits) where timestamps are strictly before the
        # start_of_month. LEFT JOIN with user to include users with NULL sums.
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()

        query = (
            'SELECT u.user_uid, '
            'IFNULL(p.payments_sum, 0) AS payments_sum, '
            'IFNULL(o.deposits_sum, 0) AS deposits_sum '
            'FROM user u '
            'LEFT JOIN (SELECT user_uid, SUM(payment_value) AS payments_sum FROM payments WHERE payment_timestamp < ? GROUP BY user_uid) p ON u.user_uid = p.user_uid '
            'LEFT JOIN (SELECT user_uid, SUM(order_value) AS deposits_sum FROM coffee_order WHERE order_timestamp < ? GROUP BY user_uid) o ON u.user_uid = o.user_uid '
        )

        cur.execute(query, (start_of_month, start_of_month))
        rows = cur.fetchall()
        con.close()

        pillory = []
        for uid, payments_sum, deposits_sum in rows:
            payments = float(payments_sum if payments_sum is not None else 0.0)
            deposit = float(deposits_sum if deposits_sum is not None else 0.0)
            balance = payments - deposit
            if balance < 0.0:
                pillory.append({'id': int(uid), 'balance': round(balance, 2)})

        # Sort ascending so the most negative balances are first
        return sorted(pillory, key=lambda d: d['balance'])


class DatabaseBackupThread(Thread):
    def __init__(self,connector:Connector,path:str,backupIntervalMinutes:int=60,backupDepth=10):
        Thread.__init__(self)
        self.__running = True
        self.__backupIntervalSeconds = backupIntervalMinutes * 60
        self.__backupDepth = backupDepth
        self.__connector = connector
        self.__path = path
        self.__mutex = Lock()

    def run(self) -> None:
        backupCounter = 1
        running = self.__running
        while running:
            backupFileName = self.__path +  'coffee' + str(backupCounter) + '.db'

            self.__connector.CreateBackup(backupFileName)

            backupCounter = backupCounter + 1
            if backupCounter > self.__backupDepth :
                backupCounter = 1

            time.sleep(float(self.__backupIntervalSeconds))
            
            self.__mutex.acquire()
            running = self.__running
            self.__mutex.release()

    def kill(self):
        self.__mutex.acquire()
        self.__running = False
        self.__mutex.release()
        self.join()
