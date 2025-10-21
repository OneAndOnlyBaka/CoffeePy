#!/bin/bash

# Setups used paths and file paths

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
START_COFFEEPY_BASH=$SCRIPT_DIR"/StartCoffeePy.sh"
AUTOSTART_PATH="~/.config/autostart/"
AUTOSTART_FILE=$AUTOSTART_PATH"/CoffeePy.desktop"
SPLASH_FILE=$SCRIPT_DIR"/setup/splash.png"

# Installs themes for tkinter

wget http://deb.debian.org/debian/pool/main/t/tcl-awthemes/tcl-awthemes_10.4.0.orig.tar.xz
tar -xf tcl-awthemes_10.4.0.orig.tar.xz -C gui/
rm tcl-awthemes_10.4.0.orig.tar.xz

# Installs all required modules
sudo apt-get update
sudo apt-get -y install swig util-linux-extra python3 python3-tk python3-pil python3-pil.imagetk python3-pyscard python3-pip pcscd autoconf debhelper flex libusb-dev libpcsclite-dev libpcsclite1 libccid pcscd pcsc-tools libpcsc-perl libusb-1.0-0-dev libtool libssl-dev cmake xterm unclutter xscreensaver i2c-tools checkinstall

# Install DS3231 as clock source
echo "i2c_dev" >> /etc/modules
echo "i2c-bcm2708" >> /etc/modules
echo ds3231 0x68 | sudo tee /sys/class/i2c-adapter/i2c-1/new_device
sudo hwclock -w
sudo update-rc.d fake-hwclock disable
sudo hwclock -w

# Installs NFC lib

git clone https://github.com/nfc-tools/libnfc
sudo mkdir -p /etc/nfc/devices.d
cd libnfc
autoreconf -vis 
./configure --with-drivers=acr122_usb --sysconfdir=/etc --prefix=/usr
make
sudo make install all
cd ..
rm -rf libnfc

# Blacklists the NFC reader if its used by the system

sudo sh -c 'echo blacklist pn533_usb >> /etc/modprobe.d/blacklist-nfc.conf'

# Puts CoffeePy into autostart

~/.config/autostart/coffeepy.desktop

echo "#!/bin/bash" > $START_COFFEEPY_BASH
echo "echo ds3231 0x68 | sudo tee /sys/class/i2c-adapter/i2c-1/new_device" >> $START_COFFEEPY_BASH
echo "cd "$SCRIPT_DIR >> $START_COFFEEPY_BASH
echo "/bin/python3 runner.py" >> $START_COFFEEPY_BASH

mkdir -p $AUTOSTART_PATH
echo "[Desktop Entry]" > $AUTOSTART_FILE
echo "Type=Application" >> $AUTOSTART_FILE
echo "Name=CoffeePyApp" >> $AUTOSTART_FILE
echo "Exec=xterm -hold -e 'sh "$START_COFFEEPY_BASH"'" >> $AUTOSTART_FILE
echo "Terminal=false" >> $AUTOSTART_FILE

# Installs splash screen and wallpaper
sudo mv /usr/share/plymouth/themes/pix/splash.png /usr/share/plymouth/themes/pix/splash.png.bk
sudo cp $SPLASH_FILE /usr/share/plymouth/themes/pix/splash.png
sudo cp $SPLASH_FILE ~/Pictures/wp.png
pcmanfm --set-wallpaper ~/Pictures/wp.png

# Disable mouse cursor
echo "@unclutter -idle 0" > "~/.config/lxsession/LXDE-pi/autostart"
# Disable display to going blank
echo "@xset s off" >> "~/.config/lxsession/LXDE-pi/autostart"
echo "@xset -dpms" >> "~/.config/lxsession/LXDE-pi/autostart"
echo "@xset s noblank" >> "~/.config/lxsession/LXDE-pi/autostart"

# Configure Raspberry Pi as a WLAN access point
sudo apt-get -y install hostapd dnsmasq

sudo systemctl stop hostapd
sudo systemctl stop dnsmasq

# Configure static IP for wlan0 in dhcpcd.conf (idempotent)
if ! grep -q "interface wlan0" /etc/dhcpcd.conf; then
sudo tee -a /etc/dhcpcd.conf > /dev/null <<'EOT'

interface wlan0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
EOT
fi

# Backup and write dnsmasq config
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.orig 2>/dev/null || true
sudo tee /etc/dnsmasq.conf > /dev/null <<'EOT'
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
EOT

# Write hostapd configuration
sudo tee /etc/hostapd/hostapd.conf > /dev/null <<'EOT'
country_code=DE
interface=wlan0
ssid=CoffeePyAP
hw_mode=g
channel=7
ieee80211n=1
wmm_enabled=1
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=CoffeePy123
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOT

# Point hostapd to its config file
if ! grep -q '^DAEMON_CONF="/etc/hostapd/hostapd.conf"' /etc/default/hostapd 2>/dev/null; then
    sudo sed -i 's|#DAEMON_CONF=""|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd 2>/dev/null || \
    echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' | sudo tee -a /etc/default/hostapd > /dev/null
fi

# Enable IP forwarding
if ! grep -q '^net.ipv4.ip_forward=1' /etc/sysctl.conf; then
    sudo sed -i 's/^#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf 2>/dev/null || \
    echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf > /dev/null
fi
sudo sysctl -p > /dev/null

# Configure NAT (masquerade) from wlan0 to eth0 and save rules
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
sudo sh -c "iptables-save > /etc/iptables.ipv4.nat"

# Ensure iptables rules are restored on boot via /etc/rc.local (idempotent)
if [ ! -f /etc/rc.local ]; then
  sudo tee /etc/rc.local > /dev/null <<'EOT'
#!/bin/sh -e
iptables-restore < /etc/iptables.ipv4.nat
exit 0
EOT
  sudo chmod +x /etc/rc.local
else
  sudo sed -i '/iptables-restore < \/etc\/iptables.ipv4.nat/d' /etc/rc.local || true
  sudo sed -i '/^exit 0/i iptables-restore < /etc/iptables.ipv4.nat' /etc/rc.local
fi

# Enable and start services
sudo systemctl unmask hostapd
sudo systemctl enable hostapd
sudo systemctl enable dnsmasq
sudo systemctl restart dhcpcd
sudo systemctl start hostapd
sudo systemctl start dnsmasq

pip install -r ./requirements.txt --break-system-packages

echo "Restart is required..."
