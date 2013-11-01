#!/bin/bash

# This script is used to build local repo for UBUNTU 11.10
# Put your debs in /tmp/debs, It will ok.
# Such as find . -name "*.deb" | xargs -i cp -rf {} /tmp/debs/
# Build result is put in: /media/ubuntu
#
# USEAGE: local file.
# version=`lsb_release -c -s`
# echo "deb file:///media/ubuntu/ $version main" >> /etc/apt/sources.list
#
# USEAGE: http service
# $HOST_IP is the host who provides apt-get source for other hosts.
# Make sure $HOST_IP has installed apache2 service.
# DO THIS:
# scp -pr /media/ubuntu $HOST_IP:/var/www/
# version=`lsb_release -c -s`
# echo "deb http://$HOST_IP/ubuntu $version main" >> /etc/apt/sources.list

set -e
set -o xtrace

pkgs=${1:-/tmp/debs}
version=`lsb_release -c -s`
mkdir -p /media/sda7/Backup/Ubuntu/Packages
mkdir -p /media/sda7/Backup/Ubuntu/dists/$version/main/binary-amd64
mkdir -p /media/sda7/Backup/Ubuntu/dists/$version/main/binary-i386
cp -rf $pkgs/*.deb /media/sda7/Backup/Ubuntu/Packages >/dev/null
cd /media/sda7/Backup/Ubuntu/
dpkg-scanpackages Packages /dev/null | gzip > dists/$version/main/binary-amd64/Packages.gz
dpkg-scanpackages Packages /dev/null | gzip > dists/$version/main/binary-i386/Packages.gz
mv /media/sda7/Backup/Ubuntu/ /media/ubuntu
rm -rf /media/sda7

set +o xtrace
