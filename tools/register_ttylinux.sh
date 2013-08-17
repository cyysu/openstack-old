#!/bin/bash
set -e
set +o xtrace

TOPDIR=$(cd $(dirname "$0") && pwd)
source /root/localrc
ADMIN_PASSWORD=$KEYSTONE_GLANCE_SERVICE_PASSWORD
ADMIN_USER=glance
ADMIN_TENANT=service
KEYSTONE_HOST=$KEYSTONE_HOST

TOKEN=`curl -s -d  "{\"auth\":{\"passwordCredentials\": {\"username\": \"$ADMIN_USER\", \"password\": \"$ADMIN_PASSWORD\"}, \"tenantName\": \"$ADMIN_TENANT\"}}" -H "Content-type: application/json" http://$KEYSTONE_HOST:5000/v2.0/tokens | python -c "import sys; import json; tok = json.loads(sys.stdin.read()); print tok['access']['token']['id'];"`
echo $TOKEN

glance --os-auth-token $TOKEN --os-image-url http://$GLANCE_HOST:9292 image-create --name "ttylinux.img" --public --container-format ami --disk-format ami  < "${TOPDIR}/ttylinux.img"
glance image-update --property hw_disk_bus=ide `glance index | grep ttylinux | awk '{print $1}'`

set -o xtrace
