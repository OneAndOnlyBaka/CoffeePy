#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
START_COFFEEPY_BASH=$SCRIPT_DIR"/StartCoffeePy.sh"
AUTOSTART_PATH="~/.config/autostart/"
AUTOSTART_FILE=$SCRIPT_DIR"/CoffeePy.desktop"
SPLASH_FILE=$SCRIPT_DIR"/setup/splash.png"

wget http://deb.debian.org/debian/pool/main/t/tcl-awthemes/tcl-awthemes_10.4.0.orig.tar.xz
tar -xf tcl-awthemes_10.4.0.orig.tar.xz -C gui/
rm tcl-awthemes_10.4.0.orig.tar.xz

sudo apt-get -y install swig python3 python3-tk python3-pil python3-pil.imagetk python3-pyscard python3-pip pcscd autoconf debhelper flex libusb-dev libpcsclite-dev libpcsclite1 libccid pcscd pcsc-tools libpcsc-perl libusb-1.0-0-dev libtool libssl-dev cmake xterm checkinstall

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

~/.config/autostart/coffeepy.desktop

echo "#!/bin/bash" > $START_COFFEEPY_BASH
echo "cd "$SCRIPT_DIR >> $START_COFFEEPY_BASH
echo "/bin/python3 CoffeePy.py" >> $START_COFFEEPY_BASH

mkdir -p $AUTOSTART_PATH
echo "[Desktop Entry]" > $AUTOSTART_FILE
echo "Type=Application" >> $AUTOSTART_FILE
echo "Name=CoffeePyApp" >> $AUTOSTART_FILE
echo "Exec=xterm -hold -e 'sh "$START_COFFEEPY_BASH"'" >> $AUTOSTART_FILE
echo "Terminal=false" >> $AUTOSTART_FILE

sudo mv /usr/share/plymouth/themes/pix/splash.png /usr/share/plymouth/themes/pix/splash.png.bk
sudo cp $SPLASH_FILE /usr/share/plymouth/themes/pix/splash.png
sudo cp $SPLASH_FILE ~/Pictures/wp.png
pcmanfm --set-wallpaper ~/Pictures/wp.png

echo "Restart is required..."
