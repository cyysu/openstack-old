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

BASE_SQL_CONN=mysql://$MYSQL_NOVA_USER:$MYSQL_NOVA_PASSWORD@$MYSQL_HOST

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
KEYSTONE_PROTOCOL=http

#---------------------------------------------------
# Clear Front installation
#---------------------------------------------------

DEBIAN_FRONTEND=noninteractive \
apt-get --option \
"Dpkg::Options::=--force-confold" --assume-yes \
install -y --force-yes mysql-client
nkill swift-proxy-server
[[ -d /etc/swift ]] && rm -rf /etc/swift/*
[[ -d $DEST/swift ]] && cp -rf $TOPDIR/openstacksource/swift/etc/* $DEST/swift/etc/
mysql_cmd "DROP DATABASE IF EXISTS swift;"

############################################################
#
# Install some basic used debs.
#
############################################################


DEBIAN_FRONTEND=noninteractive \
apt-get --option "Dpkg::Options::=--force-confold" --assume-yes \
install -y --force-yes openssh-server build-essential git \
curl gcc git git-core libxml2-dev libxslt-dev \
memcached openssl expect mysql-client unzip \
python-pam python-lxml memcached \
python-dev python-setuptools python-pip \
python-iso8601 python-prettytable python-requests \
python-coverage python-nose python-setuptools \
python-simplejson python-xattr sqlite3 \
xfsprogs python-eventlet python-greenlet \
python-pastedeploy python-netifaces


[[ -e /usr/include/libxml ]] && rm -rf /usr/include/libxml
ln -s /usr/include/libxml2/libxml /usr/include/libxml


#---------------------------------------------------
# Copy source code to DEST Dir
#---------------------------------------------------

[[ ! -d $DEST ]] && mkdir -p $DEST
if [[ ! -d $DEST/swift ]]; then
    [[ ! -d $DEST/swift ]] && cp -rf $TOPDIR/openstacksource/swift $DEST/
    [[ ! -d $DEST/swift3 ]] && cp -rf $TOPDIR/openstacksource/swift3 $DEST/
    [[ ! -d $DEST/keystone ]] && cp -rf $TOPDIR/openstacksource/keystone $DEST/


    for dep in WebOb greenlet eventlet \
        PasteDeploy paste repoze.lru routes \
        decorator Tempita sqlalchemy sqlalchemy-migrate \
        passlib lxml iso8601 \
        prettytable simplejson \
        requests oslo.config python-keystoneclient
    do
        ls $TOPDIR/pip/ > $TEMP/ret
        dep_file=`cat $TEMP/ret | grep -i "$dep"`
        old_path=`pwd`; cd $TOPDIR/pip/
        pip install ./$dep_file
        cd $old_path
    done

    for dep in greenlet eventlet PasteDeploy \
               xattr netifaces simplejson \
               oslo.config python-keystoneclient \
               python-swiftclient
    do
        ls $TOPDIR/pip/ > $TEMP/ret
        dep_file=`cat $TEMP/ret | grep -i "$dep"`
        old_path=`pwd`; cd $TOPDIR/pip/
        pip install ./$dep_file
        cd $old_path
    done

    source_install swift
    source_install swift3
    source_install keystone
fi

#---------------------------------------------------
# Create User in Swift
#---------------------------------------------------

export SERVICE_TOKEN=$ADMIN_TOKEN
export SERVICE_ENDPOINT=http://$KEYSTONE_HOST:35357/v2.0

get_tenant SERVICE_TENANT service
get_role ADMIN_ROLE admin


if [[ `keystone user-list | grep swift | wc -l` -eq 0 ]]; then
SWIFT_USER=$(get_id keystone user-create \
    --name=swift \
    --pass="$KEYSTONE_SWIFT_SERVICE_PASSWORD" \
    --tenant_id $SERVICE_TENANT \
    --email=swift@example.com)
keystone user-role-add \
    --tenant_id $SERVICE_TENANT \
    --user_id $SWIFT_USER \
    --role_id $ADMIN_ROLE
SWIFT_SERVICE=$(get_id keystone service-create \
    --name=swift \
    --type="object-store" \
    --description="Swift Service")
keystone endpoint-create \
    --region RegionOne \
    --service_id $SWIFT_SERVICE \
    --publicurl "http://$SWIFT_HOST:8080/v1/AUTH_\$(tenant_id)s" \
    --adminurl "http://$SWIFT_HOST:8080/v1" \
    --internalurl "http://$SWIFT_HOST:8080/v1/AUTH_\$(tenant_id)s"


S3_SERVICE=$(get_id keystone service-create \
    --name=s3 \
    --type=s3 \
    --description="S3")
keystone endpoint-create \
    --region RegionOne \
    --service_id $S3_SERVICE \
    --publicurl "http://$SWIFT_HOST:$S3_SERVICE_PORT" \
    --adminurl "http://$SWIFT_HOST:$S3_SERVICE_PORT" \
    --internalurl "http://$SWIFT_HOST:$S3_SERVICE_PORT"
fi

unset SERVICE_TOKEN
unset SERVICE_ENDPOINT


#---------------------------------------------------
# Create glance user in Linux-System
#---------------------------------------------------



if [[ `cat /etc/passwd | grep swift | wc -l` -eq 0 ]] ; then
    groupadd swift
    useradd -g swift swift
fi


#---------------------------------------------------
# Swift Configurations
#---------------------------------------------------

[[ -d /etc/swift ]] && rm -rf /etc/swift
mkdir -p /etc/swift
cat >/etc/swift/swift.conf <<EOF
[swift-hash]
swift_hash_path_suffix = `od -t x8 -N 8 -A n </dev/random`
EOF


sed -i 's/127.0.0.1/0.0.0.0/g' /etc/memcached.conf
service memcached restart

#---------------------------------------------------
# Change configurations for Swift
#---------------------------------------------------

cp -rf $TOPDIR/templates/proxy-server.conf /etc/swift/
file=/etc/swift/proxy-server.conf

sed -i "s,%KEYSTONE_AUTH_PORT%,$KEYSTONE_AUTH_PORT,g" $file
sed -i "s,%KEYSTONE_HOST%,$KEYSTONE_HOST,g" $file
sed -i "s,%KEYSTONE_PROTOCOL%,$KEYSTONE_PROTOCOL,g" $file
sed -i "s,%AUTH_TOKEN%,$ADMIN_TOKEN,g" $file
sed -i "s,%ADMIN_TOKEN%,$ADMIN_TOKEN,g" $file
sed -i "s,%SERVICE_TENANT_NAME%,$SERVICE_TENANT_NAME,g" $file
sed -i "s,%SERVICE_USER%,swift,g" $file
sed -i "s,%SERVICE_PASSWORD%,$KEYSTONE_SWIFT_SERVICE_PASSWORD,g" $file

#---------------------------------------------------
# Change Rights
#---------------------------------------------------

mkdir -p /etc/swift/keystone-signing
chown -R swift:swift /etc/swift
mkdir -p /var/log/swift
chown -R swift:swift /var/log/swift


#---------------------------------------------------
# Build Rings
#---------------------------------------------------


swift-ring-builder object.builder create 18 3 1
swift-ring-builder container.builder create 18 3 1
swift-ring-builder account.builder create 18 3 1

swift-ring-builder object.builder add z1-${SWIFT_NODE_IP}:6010/sdb1 100
swift-ring-builder container.builder add z1-${SWIFT_NODE_IP}:6011/sdb1 100
swift-ring-builder account.builder add z1-${SWIFT_NODE_IP}:6012/sdb1 100

swift-ring-builder account.builder
swift-ring-builder container.builder
swift-ring-builder object.builder

swift-ring-builder object.builder rebalance
swift-ring-builder container.builder rebalance
swift-ring-builder account.builder rebalance


#---------------------------------------------------
# Build Rings
#---------------------------------------------------

mkdir -p /var/log/swift
chown -R swift /var/log/swift
cat <<"EOF" > /root/start.sh
#!/bin/bash
cd /opt/stack/swift
nohup ./bin/swift-proxy-server /etc/swift/proxy-server.conf -v > /var/log/swift/swift.log 2>&1 &

EOF

chmod +x /root/start.sh
/root/start.sh

rm -rf /tmp/pip*; rm -rf /tmp/tmp*
set +o xtrace
