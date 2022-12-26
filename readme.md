# Abstract

Software to handle coffee matters. Simple GUI with SQL Backend which replaces a pen and paper tally for your coffee machine. To simplify things the identification uses NFC for handling the user management.

The gui is designed for a 1024x600 touchscreen to run it in fullscreen.

# First Steps

## Hardware requirements

- Touchscreen with 1024x600, e.g. https://www.adafruit.com/product/2396
- Advanced Card System ACR

## Software requierments

For an installation is debian or ubuntu required. Basically are the packages from [setup_debian.sh](setup_debian.sh) required. This script contains all relevant packages for python like tkinter. Also it installs the dependencies for the nfc reader [yongshi-pynfc](https://pypi.org/project/yongshi-pynfc/), which is required for the ACR122U Reader.

For raspberry pi got [setup_raspbian.sh](setup_raspbian.sh) added. It will also compile and setup libnfc which is installed already on debian for the ACR122U.

Also awthemes needs to get copied into gui/awthemes-10.4.0

```
wget http://deb.debian.org/debian/pool/main/t/tcl-awthemes/tcl-awthemes_10.4.0.orig.tar.xz
tar -xf tcl-awthemes_10.4.0.orig.tar.xz -C gui/
rm tcl-awthemes_10.4.0.orig.tar.xz
```

## Initalize

This chapter will show you how to modify and setup CoffeePy, before it can be used.

### Coffee Sorts

Setup coffee sorts is mandatory, otherwise the users cannot select your coffee. For a quick start you can use [GenerateDefaultCoffeeSorts.py](GenerateDefaultCoffeeSorts.py). This can be modified and should only get triggered once. Also you can use sqlite3 to insert your sorts.

```
INSERT INTO coffee_sorts VALUES('<coffee name as string>', <price of coffee as REAL>, <Number of strokes as int>)
```

If you want to modify your sorts later on use the [UPDATE statement](https://www.sqlite.org/lang_update.html) or the [DELETE statement](https://www.sqlite.org/lang_delete.html). Each line has an row id as unique id for your sort, so it can use that as "WHERE" statement in your queries. The price calculation is not directly related to any product, so you can modify it without problems. At least the users may need to change their favs. 


### Backup

You can create a CoffeePy.ini in the root of CoffeePy to setup the backup.

```
[Backup]
Enabled = True
Path = path_to_backup/
Interval = 60
Depth = 48
```

To enable the backup, Enabled must be "True" to disable it has to be set to "False" or its possible to not create the ini File. Path is mandatory if the backup is enabled. The tool will store its backups under this path. 
The parameters Interval and Depth are optional. Interval specifies how often a backup will be generated, in this example it generates every 60 minutes an backup. Depth defines how many backups will be generated. In this case if the program runs for more than two days the last two days are under the backup path (60 min*48 times = 2 days).

The backup files will be named in following scheme "coffee<1..Depth>.db". If the program get restarted the backup always starts at 1.

# Usage

# Source note

- ACR122U python abstraction from github project [Flowtter](https://github.com/Flowtter/py-acr122u). The code is in the folder [lib/nfc/Flowtter](lib/nfc/Flowtter) with minor modification The print() got removed with logging module
- All icon from [gui/res](gui/res) were obtained from https://iconarchive.com/
- For tkinter is [awthemes](https://wiki.tcl-lang.org/page/awthemes) gets copied in version 10.4.0 in gui/awthemes-10.4.0, see setup scripts.