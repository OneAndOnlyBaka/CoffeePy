import subprocess
from datetime import datetime

class datetime_setter:
    @staticmethod
    def set_system_datetime(new_datetime: datetime):
        date_str = new_datetime.strftime('%Y-%m-%d %H:%M:%S')
        subprocess.run(['sudo', 'date', '-s', date_str], check=True)
        subprocess.run(['sudo', 'hwclock', '-w'], check=True)
        