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

#---------------------------------------------------
#  Your Configurations.
#---------------------------------------------------

BASE_SQL_CONN=mysql://$MYSQL_CINDER_USER:$MYSQL_CINDER_PASSWORD@$MYSQL_HOST

unset OS_USERNAME
unset OS_AUTH_KEY
unset OS_AUTH_TENANT
unset OS_STRATEGY
unset OS_AUTH_STRATEGY
unset OS_AUTH_URL
unset SERVICE_TOKEN
unset SERVICE_ENDPOINT
unset http_proxy
unset https_proxy
unset ftp_proxy

KEYSTONE_AUTH_HOST=$KEYSTONE_HOST
KEYSTONE_AUTH_PORT=35357
KEYSTONE_AUTH_PROTOCOL=http
KEYSTONE_SERVICE_HOST=$KEYSTONE_HOST
KEYSTONE_SERVICE_PORT=5000
KEYSTONE_SERVICE_PROTOCOL=http
SERVICE_ENDPOINT=http://$KEYSTONE_HOST:35357/v2.0

#---------------------------------------------------
# Clear Front installation
#---------------------------------------------------

DEBIAN_FRONTEND=noninteractive \
apt-get --option \
"Dpkg::Options::=--force-confold" --assume-yes \
install -y --force-yes mysql-client

nkill cinder
[[ -d $DEST/cinder ]] && cp -rf $TOPDIR/openstacksource/cinder/etc/cinder/* $DEST/cinder/etc/cinder/
mysql_cmd "DROP DATABASE IF EXISTS cinder;"

############################################################
#
# Install some basic used debs.
#
############################################################



apt-get install -y --force-yes openssh-server build-essential git \
python-dev python-setuptools python-pip \
libxml2-dev libxslt-dev tgt lvm2 \
unzip python-mysqldb mysql-client memcached openssl expect \
iputils-arping \
python-lxml kvm gawk iptables ebtables sqlite3 sudo kvm \
vlan curl socat python-mox  \
python-migrate \
iscsitarget iscsitarget-dkms open-iscsi \
python-requests


service ssh restart

#---------------------------------------------------
# Copy source code to DEST Dir
#---------------------------------------------------

[[ ! -d $DEST ]] && mkdir -p $DEST
if [[ ! -d $DEST/cinder ]]; then
    [[ ! -d $DEST/cinder ]] && cp -rf $TOPDIR/openstacksource/cinder $DEST/
    [[ ! -d $DEST/keystone ]] && cp -rf $TOPDIR/openstacksource/keystone $DEST/
    install_keystone
    install_cinder
fi


#################################################
#
# Change configuration file.
#
#################################################

[[ -d /etc/cinder ]] && rm -rf /etc/cinder/*
mkdir -p /etc/cinder
cp -rf $TOPDIR/openstacksource/cinder/etc/cinder/* /etc/cinder/

file=/etc/cinder/api-paste.ini
sed -i "s,auth_host = 127.0.0.1,auth_host = $KEYSTONE_HOST,g" $file
sed -i "s,%SERVICE_TENANT_NAME%,$SERVICE_TENANT_NAME,g" $file
sed -i "s,%SERVICE_USER%,cinder,g" $file
sed -i "s,%SERVICE_PASSWORD%,$KEYSTONE_CINDER_SERVICE_PASSWORD,g" $file

file=/etc/cinder/rootwrap.conf
sed -i "s,filters_path=.*,filters_path=/etc/cinder/rootwrap.d,g" $file

file=/etc/cinder/cinder.conf

mkdir -p /opt/stack/data/cinder
rm -rf /etc/cinder/cinder.conf*
cat <<"EOF">$file
[DEFAULT]
rabbit_password = %RABBITMQ_PASSWORD%
rabbit_host = %RABBITMQ_HOST%
state_path = /opt/stack/data/cinder
osapi_volume_extension = cinder.api.openstack.volume.contrib.standard_extensions
root_helper = sudo /usr/local/bin/cinder-rootwrap /etc/cinder/rootwrap.conf
api_paste_config = /etc/cinder/api-paste.ini
sql_connection = mysql://%MYSQL_CINDER_USER%:%MYSQL_CINDER_PASSWORD%@%MYSQL_HOST%/cinder?charset=utf8
iscsi_helper = tgtadm
volume_name_template = volume-%s
volume_group = %VOLUME_GROUP%
verbose = True
auth_strategy = keystone
EOF
sed -i "s,%RABBITMQ_PASSWORD%,$RABBITMQ_PASSWORD,g" $file
sed -i "s,%RABBITMQ_HOST%,$RABBITMQ_HOST,g" $file
sed -i "s,%MYSQL_CINDER_USER%,$MYSQL_CINDER_USER,g" $file
sed -i "s,%MYSQL_CINDER_PASSWORD%,$MYSQL_CINDER_PASSWORD,g" $file
sed -i "s,%MYSQL_HOST%,$MYSQL_HOST,g" $file
sed -i "s,%VOLUME_GROUP%,$VOLUME_GROUP,g" $file

file=/etc/tgt/targets.conf
sed -i "/cinder/g" $file
echo "include /etc/tgt/conf.d/cinder.conf" >> $file
echo "include /opt/stack/data/cinder/volumes/*" >> $file
cp -rf /etc/cinder/cinder.conf /etc/tgt/conf.d/

############################################################
# Create the volume storage.
############################################################


pvcreate -ff $VOLUME_DISK
vgcreate cinder-volumes $VOLUME_DISK


############################################################
# Start up the services.
############################################################


cat <<"EOF" > /root/cinder.sh
#!/bin/bash
mkdir -p /var/log/cinder
python /opt/stack/cinder/bin/cinder-volume --config-file /etc/cinder/cinder.conf>/var/log/cinder/cinder-volume.log 2>&1 &
EOF

chmod +x /root/cinder.sh
/root/cinder.sh
rm -rf /tmp/pip*
rm -rf /tmp/tmp*

set +o xtrace
