#!/bin/bash

set -e
set -o xtrace

TOPDIR=$(cd $(dirname "$0") && pwd)
TEMP=`mktemp`;
rm -rfv $TEMP >/dev/null;
mkdir -p $TEMP;
source $TOPDIR/localrc
source $TOPDIR/tools/function
DEST=/opt/stack/

###########################################################
#
#  Your Configurations.
#
###########################################################

HOST_IP=$1
if [[ $# -eq 0 ]]; then
    echo "Error: You should put your IP address."
    echo "Use: ./nova-compute.sh HOST_IP"
    exit
fi


DEBIAN_FRONTEND=noninteractive \
apt-get --option \
"Dpkg::Options::=--force-confold" --assume-yes \
install -y --force-yes mysql-client

nkill nova-compute
nkill nova-novncproxy
nkill nova-xvpvncproxy

############################################################
#
# Install some basic used debs.
#
############################################################


apt-get install -y --force-yes openssh-server build-essential git \
python-dev python-setuptools python-pip \
libxml2-dev libxslt-dev tgt lvm2 python-pam python-lxml \
python-iso8601 python-sqlalchemy python-migrate \
unzip python-mysqldb mysql-client memcached openssl expect \
iputils-arping python-xattr \
python-lxml kvm gawk iptables ebtables sqlite3 sudo kvm \
vlan curl socat python-mox  \
python-migrate python-gflags python-greenlet python-libxml2 \
iscsitarget iscsitarget-dkms open-iscsi build-essential libxml2 libxml2-dev \
libxslt1.1 libxslt1-dev vlan gnutls-bin \
libgnutls-dev cdbs debhelper libncurses5-dev \
libreadline-dev libavahi-client-dev libparted0-dev \
libdevmapper-dev libudev-dev libpciaccess-dev \
libcap-ng-dev libnl-3-dev libapparmor-dev \
python-all-dev libxen-dev policykit-1 libyajl-dev \
libpcap0.8-dev libnuma-dev radvd libxml2-utils \
libnl-route-3-200 libnl-route-3-dev libnuma1 numactl \
libnuma-dbg libnuma-dev dh-buildinfo expect \
make fakeroot dkms openvswitch-switch openvswitch-datapath-dkms \
ebtables iptables iputils-ping iputils-arping sudo python-boto \
python-iso8601 python-routes python-suds python-netaddr \
 python-greenlet python-kombu python-eventlet \
python-sqlalchemy python-mysqldb python-pyudev python-qpid dnsmasq-base \
dnsmasq-utils vlan python-qpid websockify \
python-stevedore python-docutils python-requests \
libvirt-bin python-prettytable python-cheetah \
python-requests alembic python-libvirt \
mongodb-clients mongodb \
mongodb-server mongodb-dev python-pymongo


[[ -e /usr/include/libxml ]] && rm -rf /usr/include/libxml
ln -s /usr/include/libxml2/libxml /usr/include/libxml
[[ -e /usr/include/netlink ]] && rm -rf /usr/include/netlink
ln -s /usr/include/libnl3/netlink /usr/include/netlink

service ssh restart


#---------------------------------------------------
# Copy source code to DEST Dir
#---------------------------------------------------

[[ ! -d $DEST ]] && mkdir -p $DEST
install_nova


if [[ ! -d /etc/nova ]] ; then
    scp -pr $NOVA_HOST:/etc/nova /etc/
    sed -i "s,my_ip=.*,my_ip=$HOST_IP,g" /etc/nova/nova.conf
    sed -i "s,VNCSERVER_PROXYCLIENT_ADDRESS=.*,VNCSERVER_PROXYCLIENT_ADDRESS=$HOST_IP,g" /etc/nova/nova.conf
fi

mkdir -p $DEST/data/nova/instances/

#---------------------------------------------------
# Ceilometer Service
#---------------------------------------------------

cat <<"EOF" > /root/nova-compute.sh
#!/bin/bash

nkill nova-compute
nkill nova-novncproxy
nkill nova-xvpvncproxy

mkdir -p /var/log/nova
cd /opt/stack/noVNC/
python ./utils/nova-novncproxy --config-file /etc/nova/nova.conf --web . >/var/log/nova/nova-novncproxy.log 2>&1 &

python /opt/stack/nova/bin/nova-xvpvncproxy --config-file /etc/nova/nova.conf >/var/log/nova/nova-xvpvncproxy.log 2>&1 &


nohup python /opt/stack/nova/bin/nova-compute --config-file=/etc/nova/nova.conf >/var/log/nova/nova-compute.log 2>&1 &

EOF

chmod +x /root/nova-compute.sh
/root/nova-compute.sh
rm -rf /tmp/pip*
rm -rf /tmp/tmp*

set +o xtrace
