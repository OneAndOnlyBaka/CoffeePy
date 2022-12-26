#!/bin/bash

wget http://deb.debian.org/debian/pool/main/t/tcl-awthemes/tcl-awthemes_10.4.0.orig.tar.xz
tar -xf tcl-awthemes_10.4.0.orig.tar.xz -C gui/
rm tcl-awthemes_10.4.0.orig.tar.xz

sudo apt install -y swig python3 python3-tk python3-pil python3-pil.imagetk python3-pyscard python3-pip pcscd checkinstall

sudo sh -c 'echo blacklist pn533_usb >> /etc/modprobe.d/blacklist-nfc.conf'

pip install pyscard

echo "Restart is required..."