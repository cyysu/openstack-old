# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2011 X.commerce, a business unit of eBay Inc.
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# Copyright 2011 Piston Cloud Computing, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
SQLAlchemy models for monitor data.
"""

from sqlalchemy import Column, Integer, String, Text, schema, Float
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship, backref, object_mapper

from monitor.db.sqlalchemy.session import get_session

from monitor import exception
from monitor import flags
from monitor.openstack.common import timeutils

from monitor.openstack.common import log as logging
LOG = logging.getLogger(__name__)

import numpy

FLAGS = flags.FLAGS
BASE = declarative_base()


class VsmBase(object):
    """Base class for Vsm Models."""
    __table_args__ = {'mysql_engine': 'InnoDB'}
    __table_initialized__ = False
    created_at = Column(DateTime, default=timeutils.utcnow)
    updated_at = Column(DateTime, onupdate=timeutils.utcnow)
    deleted_at = Column(DateTime)
    deleted = Column(Boolean, default=False)
    metadata = None

    def save(self, session=None):
        """Save this object."""
        if not session:
            session = get_session()
        session.add(self)
        try:
            session.flush()
        except IntegrityError, e:
            if str(e).endswith('is not unique'):
                raise exception.Duplicate(str(e))
            else:
                raise

    def delete(self, session=None):
        """Delete this object."""
        self.deleted = True
        self.deleted_at = timeutils.utcnow()
        self.save(session=session)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __iter__(self):
        self._i = iter(object_mapper(self).columns)
        return self

    def next(self):
        n = self._i.next().name
        return n, getattr(self, n)

    def update(self, values):
        """Make the model object behave like a dict."""
        for k, v in values.iteritems():
            setattr(self, k, v)

    def iteritems(self):
        """Make the model object behave like a dict.

        Includes attributes from joins."""
        local = dict(self)
        joined = dict([(k, v) for k, v in self.__dict__.iteritems()
                      if not k[0] == '_'])
        local.update(joined)
        return local.iteritems()


class Service(BASE, VsmBase):
    """Represents a running service on a host."""

    __tablename__ = 'services'
    id = Column(Integer, primary_key=True)
    host = Column(String(255))  # , ForeignKey('hosts.id'))
    binary = Column(String(255))
    topic = Column(String(255))
    report_count = Column(Integer, nullable=False, default=0)
    disabled = Column(Boolean, default=False)
    availability_zone = Column(String(255), default='monitor')


class VsmNode(BASE, VsmBase):
    """Represents a running monitor service on a host."""

    __tablename__ = 'monitor_nodes'
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey('services.id'), nullable=True)


class ServiceManage(BASE, VsmBase):
    """Represents a block storage device that can be attached to a vm."""
    __tablename__ = 'servicemanages'
    id = Column(String(36), primary_key=True)

    @property
    def name(self):
        return FLAGS.servicemanage_name_template % self.id

    ec2_id = Column(Integer)
    user_id = Column(String(255))
    project_id = Column(String(255))

    snapshot_id = Column(String(36))

    host = Column(String(255))  # , ForeignKey('hosts.id'))
    size = Column(Integer)
    availability_zone = Column(String(255))  # TODO(vish): foreign key?
    instance_uuid = Column(String(36))
    mountpoint = Column(String(255))
    attach_time = Column(String(255))  # TODO(vish): datetime
    status = Column(String(255))  # TODO(vish): enum?
    attach_status = Column(String(255))  # TODO(vish): enum

    scheduled_at = Column(DateTime)
    launched_at = Column(DateTime)
    terminated_at = Column(DateTime)

    display_name = Column(String(255))
    display_description = Column(String(255))

    provider_location = Column(String(255))
    provider_auth = Column(String(255))

    servicemanage_type_id = Column(String(36))
    source_volid = Column(String(36))


class ServiceManageMetadata(BASE, VsmBase):
    """Represents a metadata key/value pair for a servicemanage."""
    __tablename__ = 'servicemanage_metadata'
    id = Column(Integer, primary_key=True)
    key = Column(String(255))
    value = Column(String(255))
    servicemanage_id = Column(String(36), ForeignKey('servicemanages.id'), nullable=False)
    servicemanage = relationship(ServiceManage, backref="servicemanage_metadata",
                          foreign_keys=servicemanage_id,
                          primaryjoin='and_('
                          'ServiceManageMetadata.servicemanage_id == ServiceManage.id,'
                          'ServiceManageMetadata.deleted == False)')


class ServiceManageTypes(BASE, VsmBase):
    """Represent possible servicemanage_types of servicemanages offered."""
    __tablename__ = "servicemanage_types"
    id = Column(String(36), primary_key=True)
    name = Column(String(255))

    servicemanages = relationship(ServiceManage,
                           backref=backref('servicemanage_type', uselist=False),
                           foreign_keys=id,
                           primaryjoin='and_('
                           'ServiceManage.servicemanage_type_id == ServiceManageTypes.id, '
                           'ServiceManageTypes.deleted == False)')


class ServiceManageTypeExtraSpecs(BASE, VsmBase):
    """Represents additional specs as key/value pairs for a servicemanage_type."""
    __tablename__ = 'servicemanage_type_extra_specs'
    id = Column(Integer, primary_key=True)
    key = Column(String(255))
    value = Column(String(255))
    servicemanage_type_id = Column(String(36),
                            ForeignKey('servicemanage_types.id'),
                            nullable=False)
    servicemanage_type = relationship(
        ServiceManageTypes,
        backref="extra_specs",
        foreign_keys=servicemanage_type_id,
        primaryjoin='and_('
        'ServiceManageTypeExtraSpecs.servicemanage_type_id == ServiceManageTypes.id,'
        'ServiceManageTypeExtraSpecs.deleted == False)'
    )


class ServiceManageGlanceMetadata(BASE, VsmBase):
    """Glance metadata for a bootable servicemanage."""
    __tablename__ = 'servicemanage_glance_metadata'
    id = Column(Integer, primary_key=True, nullable=False)
    servicemanage_id = Column(String(36), ForeignKey('servicemanages.id'))
    snapshot_id = Column(String(36), ForeignKey('snapshots.id'))
    key = Column(String(255))
    value = Column(Text)
    servicemanage = relationship(ServiceManage, backref="servicemanage_glance_metadata",
                          foreign_keys=servicemanage_id,
                          primaryjoin='and_('
                          'ServiceManageGlanceMetadata.servicemanage_id == ServiceManage.id,'
                          'ServiceManageGlanceMetadata.deleted == False)')


class Quota(BASE, VsmBase):
    """Represents a single quota override for a project.

    If there is no row for a given project id and resource, then the
    default for the quota class is used.  If there is no row for a
    given quota class and resource, then the default for the
    deployment is used. If the row is present but the hard limit is
    Null, then the resource is unlimited.
    """

    __tablename__ = 'quotas'
    id = Column(Integer, primary_key=True)

    project_id = Column(String(255), index=True)

    resource = Column(String(255))
    hard_limit = Column(Integer, nullable=True)


class QuotaClass(BASE, VsmBase):
    """Represents a single quota override for a quota class.

    If there is no row for a given quota class and resource, then the
    default for the deployment is used.  If the row is present but the
    hard limit is Null, then the resource is unlimited.
    """

    __tablename__ = 'quota_classes'
    id = Column(Integer, primary_key=True)

    class_name = Column(String(255), index=True)

    resource = Column(String(255))
    hard_limit = Column(Integer, nullable=True)


class QuotaUsage(BASE, VsmBase):
    """Represents the current usage for a given resource."""

    __tablename__ = 'quota_usages'
    id = Column(Integer, primary_key=True)

    project_id = Column(String(255), index=True)
    resource = Column(String(255))

    in_use = Column(Integer)
    reserved = Column(Integer)

    @property
    def total(self):
        return self.in_use + self.reserved

    until_refresh = Column(Integer, nullable=True)


class Reservation(BASE, VsmBase):
    """Represents a resource reservation for quotas."""

    __tablename__ = 'reservations'
    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False)

    usage_id = Column(Integer, ForeignKey('quota_usages.id'), nullable=False)

    project_id = Column(String(255), index=True)
    resource = Column(String(255))

    delta = Column(Integer)
    expire = Column(DateTime, nullable=False)


class Snapshot(BASE, VsmBase):
    """Represents a block storage device that can be attached to a VM."""
    __tablename__ = 'snapshots'
    id = Column(String(36), primary_key=True)

    @property
    def name(self):
        return FLAGS.snapshot_name_template % self.id

    @property
    def servicemanage_name(self):
        return FLAGS.servicemanage_name_template % self.servicemanage_id

    user_id = Column(String(255))
    project_id = Column(String(255))

    servicemanage_id = Column(String(36))
    status = Column(String(255))
    progress = Column(String(255))
    servicemanage_size = Column(Integer)

    display_name = Column(String(255))
    display_description = Column(String(255))

    provider_location = Column(String(255))

    servicemanage = relationship(ServiceManage, backref="snapshots",
                          foreign_keys=servicemanage_id,
                          primaryjoin='and_('
                          'Snapshot.servicemanage_id == ServiceManage.id,'
                          'Snapshot.deleted == False)')


class SnapshotMetadata(BASE, VsmBase):
    """Represents a metadata key/value pair for a snapshot."""
    __tablename__ = 'snapshot_metadata'
    id = Column(Integer, primary_key=True)
    key = Column(String(255))
    value = Column(String(255))
    snapshot_id = Column(String(36),
                         ForeignKey('snapshots.id'),
                         nullable=False)
    snapshot = relationship(Snapshot, backref="snapshot_metadata",
                            foreign_keys=snapshot_id,
                            primaryjoin='and_('
                            'SnapshotMetadata.snapshot_id == Snapshot.id,'
                            'SnapshotMetadata.deleted == False)')


class IscsiTarget(BASE, VsmBase):
    """Represents an iscsi target for a given host."""
    __tablename__ = 'iscsi_targets'
    __table_args__ = (schema.UniqueConstraint("target_num", "host"),
                      {'mysql_engine': 'InnoDB'})
    id = Column(Integer, primary_key=True)
    target_num = Column(Integer)
    host = Column(String(255))
    servicemanage_id = Column(String(36), ForeignKey('servicemanages.id'), nullable=True)
    servicemanage = relationship(ServiceManage,
                          backref=backref('iscsi_target', uselist=False),
                          foreign_keys=servicemanage_id,
                          primaryjoin='and_(IscsiTarget.servicemanage_id==ServiceManage.id,'
                          'IscsiTarget.deleted==False)')


class Migration(BASE, VsmBase):
    """Represents a running host-to-host migration."""
    __tablename__ = 'migrations'
    id = Column(Integer, primary_key=True, nullable=False)
    # NOTE(tr3buchet): the ____compute variables are instance['host']
    source_compute = Column(String(255))
    dest_compute = Column(String(255))
    # NOTE(tr3buchet): dest_host, btw, is an ip address
    dest_host = Column(String(255))
    old_instance_type_id = Column(Integer())
    new_instance_type_id = Column(Integer())
    instance_uuid = Column(String(255),
                           ForeignKey('instances.uuid'),
                           nullable=True)
    #TODO(_cerberus_): enum
    status = Column(String(255))


class SMFlavors(BASE, VsmBase):
    """Represents a flavor for SM servicemanages."""
    __tablename__ = 'sm_flavors'
    id = Column(Integer(), primary_key=True)
    label = Column(String(255))
    description = Column(String(255))


class SMBackendConf(BASE, VsmBase):
    """Represents the connection to the backend for SM."""
    __tablename__ = 'sm_backend_config'
    id = Column(Integer(), primary_key=True)
    flavor_id = Column(Integer, ForeignKey('sm_flavors.id'), nullable=False)
    sr_uuid = Column(String(255))
    sr_type = Column(String(255))
    config_params = Column(String(2047))


class SMServiceManage(BASE, VsmBase):
    __tablename__ = 'sm_servicemanage'
    id = Column(String(36), ForeignKey(ServiceManage.id), primary_key=True)
    backend_id = Column(Integer, ForeignKey('sm_backend_config.id'),
                        nullable=False)
    vdi_uuid = Column(String(255))


class Backup(BASE, VsmBase):
    """Represents a backup of a servicemanage to Swift."""
    __tablename__ = 'backups'
    id = Column(String(36), primary_key=True)

    @property
    def name(self):
        return FLAGS.backup_name_template % self.id

    user_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=False)

    servicemanage_id = Column(String(36), nullable=False)
    host = Column(String(255))
    availability_zone = Column(String(255))
    display_name = Column(String(255))
    display_description = Column(String(255))
    container = Column(String(255))
    status = Column(String(255))
    fail_reason = Column(String(255))
    service_metadata = Column(String(255))
    service = Column(String(255))
    size = Column(Integer)
    object_count = Column(Integer)


def register_models():
    """Register Models and create metadata.

    Called from monitor.db.sqlalchemy.__init__ as part of loading the driver,
    it will never need to be called explicitly elsewhere unless the
    connection is lost and needs to be reestablished.
    """
    from sqlalchemy import create_engine
    models = (Backup,
              Migration,
              Service,
              SMBackendConf,
              SMFlavors,
              SMServiceManage,
              ServiceManage,
              ServiceManageMetadata,
              SnapshotMetadata,
              ServiceManageTypeExtraSpecs,
              ServiceManageTypes,
              ServiceManageGlanceMetadata,
              )
    engine = create_engine(FLAGS.sql_connection, echo=False)
    for model in models:
        model.metadata.create_all(engine)


class ComputeNode(BASE, VsmBase):
    """Represents a running compute service on a host."""

    __tablename__ = 'compute_nodes'
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey('services.id'), nullable=True)
    service = relationship(Service,
                           backref=backref('compute_node'),
                           foreign_keys=service_id,
                           primaryjoin='and_('
                                'ComputeNode.service_id == Service.id,'
                                'ComputeNode.deleted == False)')

    vcpus = Column(Integer)
    memory_mb = Column(Integer)
    local_gb = Column(Integer)
    vcpus_used = Column(Integer)
    memory_mb_used = Column(Integer)
    local_gb_used = Column(Integer)

    # Free Ram, amount of activity (resize, migration, boot, etc) and
    # the number of running VM's are a good starting point for what's
    # important when making scheduling decisions.
    #
    # NOTE(sandy): We'll need to make this extensible for other schedulers.
    free_ram_mb = Column(Integer)
    free_disk_gb = Column(Integer)
    current_workload = Column(Integer)

    # Note(masumotok): Expected Strings example:
    #
    # '{"arch":"x86_64",
    #   "model":"Nehalem",
    #   "topology":{"sockets":1, "threads":2, "cores":3},
    #   "features":["tdtscp", "xtpr"]}'
    #
    # Points are "json translatable" and it must have all dictionary keys
    # above, since it is copied from <cpu> tag of getCapabilities()
    # (See libvirt.virtConnection).
    cpu_info = Column(Text, nullable=True)
    disk_available_least = Column(Integer)
    cpu_utilization = Column(Float(), default=0.0)


class Device(BASE, VsmBase):
    """This table store the information about device on host"""

    __tablename__ = 'devices'
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey('services.id'), nullable=False)
    service = relationship(Service,
                            backref=backref('device'),
                            foreign_keys=service_id,
                            primaryjoin='and_('
                                'Device.service_id == Service.id,'
                                'Device.deleted == False)')

    name = Column(String(length=255), nullable=False)
    total_capacity_gb = Column(Float(), nullable=False)
    free_capacity_gb = Column(Float())
    device_type = Column(String(length=255))
    interface_type = Column(String(length=255))
    fs_type = Column(String(length=255))
    mount_point = Column(String(length=255))
    state = Column(String(length=255), default="down")


class OsdState(BASE, VsmBase):
    """This table maintains the information about osd."""

    __tablename__ = 'osd_states'
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey('services.id'), nullable=False)
    service = relationship(Service,
                            backref=backref('osd_state'),
                            foreign_keys=service_id,
                            primaryjoin='and_('
                                'OsdState.service_id == Service.id,'
                                'OsdState.deleted == False)')

    device_id = Column(Integer, ForeignKey('devices.id'), nullable=False)
    device = relationship(Device,
                            backref=backref('osd_state'),
                            foreign_keys=device_id,
                            primaryjoin='and_('
                                'OsdState.device_id == Device.id,'
                                'OsdState.deleted == False)')
    
    osd_name = Column(String(length=255), unique=True, nullable=False)
    cluster_id = Column(Integer)
    state = Column(String(length=255), default="down")

class CrushMap(BASE, VsmBase):
    """The table mainly store the content of crush map which encoded as text."""

    __tablename__ = 'crushmaps'
    id = Column(Integer, primary_key=True, nullable=False)
    content = Column(Text, nullable=True)

   
class Recipe(BASE, VsmBase):
    """This table store the recipes."""

    __tablename__ = 'recipes'
    id = Column(Integer, primary_key=True, nullable=False)
    recipe_name = Column(String(length=255), unique=True, nullable=False)
    pg_num = Column(Integer, nullable=False)
    pgp_num = Column(Integer, nullable=False)
    size = Column(Integer, nullable=False)
    min_size = Column(Integer)
    crush_ruleset = Column(Integer, nullable=False)
    crash_replay_interval = Column(Integer)


class VsmCapacityManage(BASE, VsmBase):
    """ Monitor user capacity management

    To manage the capacity that Monitor user can use. The most important in
    this table is capacity_quota_mb and capacity_used_mb which denote
    the quota storage and used storage respectively.
    """

    __tablename__ = 'monitor_capacity_manages'
    id = Column(Integer, primary_key=True, nullable=False)
    name = Column(String(length=255), nullable=False)
    capacity_quota_mb = Column(Integer)
    capacity_used_mb = Column(Integer)
    testyr = Column(Integer)
