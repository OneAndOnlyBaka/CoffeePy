#!/bin/bash

wget http://deb.debian.org/debian/pool/main/t/tcl-awthemes/tcl-awthemes_10.4.0.orig.tar.xz
tar -xf tcl-awthemes_10.4.0.orig.tar.xz -C gui/
rm tcl-awthemes_10.4.0.orig.tar.xz

sudo apt-get -y install swig python3 python3-tk python3-pil python3-pil.imagetk python3-pyscard python3-pip pcscd autoconf debhelper flex libusb-dev libpcsclite-dev libpcsclite1 libccid pcscd pcsc-tools libpcsc-perl libusb-1.0-0-dev libtool libssl-dev cmake checkinstall

git clone https://github.com/nfc-tools/libnfc
sudo mkdir -p /etc/nfc/devices.d
cd libnfc
autoreconf -vis 
./configure --with-drivers=acr122_usb --sysconfdir=/etc --prefix=/usr
make
sudo make install all
cd ..
rm -rf libnfc

sudo sh -c 'echo blacklist pn533_usb >> /etc/modprobe.d/blacklist-nfc.conf'

pip install pyscard

echo "Restart is required..."
