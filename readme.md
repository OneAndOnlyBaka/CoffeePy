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

# Source note

- ACR122U python abstraction from github project [Flowtter](https://github.com/Flowtter/py-acr122u). The code is in the folder [lib/nfc/Flowtter](lib/nfc/Flowtter) with minor modification The print() got removed with logging module
- All icon from [gui/res](gui/res) were obtained from https://iconarchive.com/
- For tkinter is [awthemes](https://wiki.tcl-lang.org/page/awthemes) gets copied in version 10.4.0 in gui/awthemes-10.4.0, see setup scripts.