# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2011 X.commerce, a business unit of eBay Inc.
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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

"""Implementation of SQLAlchemy backend."""

import datetime
import uuid
import warnings
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from sqlalchemy.sql.expression import literal_column
from sqlalchemy.sql.expression import desc
from sqlalchemy.sql import func

from monitor.common import sqlalchemyutils
from monitor import db
from monitor.db.sqlalchemy import models
from monitor.db.sqlalchemy.session import get_session
from monitor import exception
from monitor import flags
from monitor.openstack.common import log as logging
from monitor.openstack.common import timeutils
from monitor.openstack.common import uuidutils


FLAGS = flags.FLAGS

LOG = logging.getLogger(__name__)


def is_admin_context(context):
    """Indicates if the request context is an administrator."""
    if not context:
        warnings.warn(_('Use of empty request context is deprecated'),
                      DeprecationWarning)
        raise Exception('die')
    return context.is_admin


def is_user_context(context):
    """Indicates if the request context is a normal user."""
    if not context:
        return False
    if context.is_admin:
        return False
    if not context.user_id or not context.project_id:
        return False
    return True


def authorize_project_context(context, project_id):
    """Ensures a request has permission to access the given project."""
    if is_user_context(context):
        if not context.project_id:
            raise exception.NotAuthorized()
        elif context.project_id != project_id:
            raise exception.NotAuthorized()


def authorize_user_context(context, user_id):
    """Ensures a request has permission to access the given user."""
    if is_user_context(context):
        if not context.user_id:
            raise exception.NotAuthorized()
        elif context.user_id != user_id:
            raise exception.NotAuthorized()


def authorize_quota_class_context(context, class_name):
    """Ensures a request has permission to access the given quota class."""
    if is_user_context(context):
        if not context.quota_class:
            raise exception.NotAuthorized()
        elif context.quota_class != class_name:
            raise exception.NotAuthorized()


def require_admin_context(f):
    """Decorator to require admin request context.

    The first argument to the wrapped function must be the context.

    """

    def wrapper(*args, **kwargs):
        if not is_admin_context(args[0]):
            raise exception.AdminRequired()
        return f(*args, **kwargs)
    return wrapper


def require_context(f):
    """Decorator to require *any* user or admin context.

    This does no authorization for user or project access matching, see
    :py:func:`authorize_project_context` and
    :py:func:`authorize_user_context`.

    The first argument to the wrapped function must be the context.

    """

    def wrapper(*args, **kwargs):
        if not is_admin_context(args[0]) and not is_user_context(args[0]):
            raise exception.NotAuthorized()
        return f(*args, **kwargs)
    return wrapper


def require_servicemanage_exists(f):
    """Decorator to require the specified servicemanage to exist.

    Requires the wrapped function to use context and servicemanage_id as
    their first two arguments.
    """

    def wrapper(context, servicemanage_id, *args, **kwargs):
        db.servicemanage_get(context, servicemanage_id)
        return f(context, servicemanage_id, *args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


def require_snapshot_exists(f):
    """Decorator to require the specified snapshot to exist.

    Requires the wrapped function to use context and snapshot_id as
    their first two arguments.
    """

    def wrapper(context, snapshot_id, *args, **kwargs):
        db.api.snapshot_get(context, snapshot_id)
        return f(context, snapshot_id, *args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


def model_query(context, *args, **kwargs):
    """Query helper that accounts for context's `read_deleted` field.

    :param context: context to query under
    :param session: if present, the session to use
    :param read_deleted: if present, overrides context's read_deleted field.
    :param project_only: if present and context is user-type, then restrict
            query to match the context's project_id.
    """
    session = kwargs.get('session') or get_session()
    read_deleted = kwargs.get('read_deleted') or context.read_deleted
    project_only = kwargs.get('project_only')

    query = session.query(*args)

    if read_deleted == 'no':
        query = query.filter_by(deleted=False)
    elif read_deleted == 'yes':
        pass  # omit the filter to include deleted and active
    elif read_deleted == 'only':
        query = query.filter_by(deleted=True)
    else:
        raise Exception(
            _("Unrecognized read_deleted value '%s'") % read_deleted)

    if project_only and is_user_context(context):
        query = query.filter_by(project_id=context.project_id)

    return query


def exact_filter(query, model, filters, legal_keys):
    """Applies exact match filtering to a query.

    Returns the updated query.  Modifies filters argument to remove
    filters consumed.

    :param query: query to apply filters to
    :param model: model object the query applies to, for IN-style
                  filtering
    :param filters: dictionary of filters; values that are lists,
                    tuples, sets, or frozensets cause an 'IN' test to
                    be performed, while exact matching ('==' operator)
                    is used for other values
    :param legal_keys: list of keys to apply exact filtering to
    """

    filter_dict = {}

    # Walk through all the keys
    for key in legal_keys:
        # Skip ones we're not filtering on
        if key not in filters:
            continue

        # OK, filtering on this key; what value do we search for?
        value = filters.pop(key)

        if isinstance(value, (list, tuple, set, frozenset)):
            # Looking for values in a list; apply to query directly
            column_attr = getattr(model, key)
            query = query.filter(column_attr.in_(value))
        else:
            # OK, simple exact match; save for later
            filter_dict[key] = value

    # Apply simple exact matches
    if filter_dict:
        query = query.filter_by(**filter_dict)

    return query


###################


@require_admin_context
def service_destroy(context, service_id):
    session = get_session()
    with session.begin():
        service_ref = service_get(context, service_id, session=session)
        service_ref.delete(session=session)


@require_admin_context
def service_get(context, service_id, session=None):
    result = model_query(
        context,
        models.Service,
        session=session).\
        filter_by(id=service_id).\
        first()
    if not result:
        raise exception.ServiceNotFound(service_id=service_id)

    return result


@require_admin_context
def service_get_all(context, disabled=None):
    query = model_query(context, models.Service)
    if disabled is not None:
        query = query.filter_by(disabled=disabled)

    return query.all()


@require_admin_context
def service_get_all_by_topic(context, topic):
    return model_query(
        context, models.Service, read_deleted="no").\
        filter_by(disabled=False).\
        filter_by(topic=topic).\
        all()


@require_admin_context
def service_get_by_host_and_topic(context, host, topic):
    result = model_query(
        context, models.Service, read_deleted="no").\
        filter_by(disabled=False).\
        filter_by(host=host).\
        filter_by(topic=topic).\
        first()
    if not result:
        raise exception.ServiceNotFound(service_id=None)
    return result


@require_admin_context
def service_get_all_by_host(context, host):
    return model_query(
        context, models.Service, read_deleted="no").\
        filter_by(host=host).\
        all()

@require_admin_context
def service_get_all_bmc_by_host(context, host):
    result = model_query(context, models.Service, read_deleted="no").\
                options(joinedload('compute_node')).\
                filter_by(host=host).\
                filter_by(topic="monitor-bmc").\
                all()

    if not result:
        raise exception.VsmHostNotFound(host=host)

    return result


@require_admin_context
def _service_get_all_topic_subquery(context, session, topic, subq, label):
    sort_value = getattr(subq.c, label)
    return model_query(context, models.Service,
                       func.coalesce(sort_value, 0),
                       session=session, read_deleted="no").\
        filter_by(topic=topic).\
        filter_by(disabled=False).\
        outerjoin((subq, models.Service.host == subq.c.host)).\
        order_by(sort_value).\
        all()


@require_admin_context
def service_get_all_servicemanage_sorted(context):
    session = get_session()
    with session.begin():
        topic = FLAGS.servicemanage_topic
        label = 'servicemanage_gigabytes'
        subq = model_query(context, models.ServiceManage.host,
                           func.sum(models.ServiceManage.size).label(label),
                           session=session, read_deleted="no").\
            group_by(models.ServiceManage.host).\
            subquery()
        return _service_get_all_topic_subquery(context,
                                               session,
                                               topic,
                                               subq,
                                               label)


@require_admin_context
def service_get_by_args(context, host, binary):
    result = model_query(context, models.Service).\
        filter_by(host=host).\
        filter_by(binary=binary).\
        first()

    if not result:
        raise exception.HostBinaryNotFound(host=host, binary=binary)

    return result


@require_admin_context
def service_create(context, values):
    service_ref = models.Service()
    service_ref.update(values)
    if not FLAGS.enable_new_services:
        service_ref.disabled = True
    service_ref.save()
    return service_ref


@require_admin_context
def service_update(context, service_id, values):
    session = get_session()
    with session.begin():
        service_ref = service_get(context, service_id, session=session)
        service_ref.update(values)
        service_ref.save(session=session)


###################
def convert_datetimes(values, *datetime_keys):
    for key in values:
        if key in datetime_keys and isinstance(values[key], basestring):
            values[key] = timeutils.parse_strtime(values[key])
    return values

@require_admin_context
def compute_node_get(context, compute_id, session=None):
    result = model_query(context, models.ComputeNode, session=session).\
                     filter_by(id=compute_id).\
                     first()

    if not result:
        raise exception.VsmHostNotFound(host=compute_id)

    return result


@require_admin_context
def compute_node_get_all(context, session=None):
    return model_query(context, models.ComputeNode, session=session).\
                    options(joinedload('service')).\
                    all()


def _get_host_utilization(context, host, ram_mb, disk_gb):
    """Compute the current utilization of a given host."""
    instances = instance_get_all_by_host(context, host)
    vms = len(instances)
    free_ram_mb = ram_mb - FLAGS.reserved_host_memory_mb
    free_disk_gb = disk_gb - (FLAGS.reserved_host_disk_mb * 1024)

    work = 0
    for instance in instances:
        free_ram_mb -= instance.memory_mb
        free_disk_gb -= instance.root_gb
        free_disk_gb -= instance.ephemeral_gb
        if instance.vm_state in [vm_states.BUILDING, vm_states.REBUILDING,
                                 vm_states.MIGRATING, vm_states.RESIZING]:
            work += 1
    return dict(free_ram_mb=free_ram_mb,
                free_disk_gb=free_disk_gb,
                current_workload=work,
                running_vms=vms)


def _adjust_compute_node_values_for_utilization(context, values, session):
    service_ref = service_get(context, values['service_id'], session=session)
    host = service_ref['host']
    ram_mb = values['memory_mb']
    disk_gb = values['local_gb']
    #values.update(_get_host_utilization(context, host, ram_mb, disk_gb))


@require_admin_context
def compute_node_create(context, values, session=None):
    """Creates a new ComputeNode and populates the capacity fields
    with the most recent data."""
    if not session:
        session = get_session()

    _adjust_compute_node_values_for_utilization(context, values, session)
    with session.begin(subtransactions=True):
        compute_node_ref = models.ComputeNode()
        session.add(compute_node_ref)
        compute_node_ref.update(values)
    return compute_node_ref


@require_admin_context
def compute_node_update(context, compute_id, values, auto_adjust):
    """Creates a new ComputeNode and populates the capacity fields
    with the most recent data."""
    session = get_session()
    if auto_adjust:
        _adjust_compute_node_values_for_utilization(context, values, session)
    with session.begin(subtransactions=True):
        values['updated_at'] = timeutils.utcnow()
        convert_datetimes(values, 'created_at', 'deleted_at', 'updated_at')
        compute_ref = compute_node_get(context, compute_id, session=session)
        for (key, value) in values.iteritems():
            compute_ref[key] = value
        compute_ref.save(session=session)


def compute_node_get_by_host(context, host):
    """Get all capacity entries for the given host."""
    session = get_session()
    with session.begin():
        service = session.query(models.Service).\
                            filter_by(host=host, binary="monitor-bmc").first()
        node = session.query(models.ComputeNode).\
                             options(joinedload('service')).\
                             filter_by(deleted=False,service_id=service.id)
        return node.first()


def compute_node_utilization_update(context, host, free_ram_mb_delta=0,
                          free_disk_gb_delta=0, work_delta=0, vm_delta=0):
    """Update a specific ComputeNode entry by a series of deltas.
    Do this as a single atomic action and lock the row for the
    duration of the operation. Requires that ComputeNode record exist."""
    session = get_session()
    compute_node = None
    with session.begin(subtransactions=True):
        compute_node = session.query(models.ComputeNode).\
                              options(joinedload('service')).\
                              filter(models.Service.host == host).\
                              filter_by(deleted=False).\
                              with_lockmode('update').\
                              first()
        if compute_node is None:
            raise exception.NotFound(_("No ComputeNode for %(host)s") %
                                     locals())

        # This table thingy is how we get atomic UPDATE x = x + 1
        # semantics.
        table = models.ComputeNode.__table__
        if free_ram_mb_delta != 0:
            compute_node.free_ram_mb = table.c.free_ram_mb + free_ram_mb_delta
        if free_disk_gb_delta != 0:
            compute_node.free_disk_gb = (table.c.free_disk_gb +
                                         free_disk_gb_delta)
        if work_delta != 0:
            compute_node.current_workload = (table.c.current_workload +
                                             work_delta)
        if vm_delta != 0:
            compute_node.running_vms = table.c.running_vms + vm_delta
    return compute_node


def compute_node_utilization_set(context, host, free_ram_mb=None,
                                 free_disk_gb=None, work=None, vms=None):
    """Like compute_node_utilization_update() modify a specific host
    entry. But this function will set the metrics absolutely
    (vs. a delta update).
    """
    session = get_session()
    compute_node = None
    with session.begin(subtransactions=True):
        compute_node = session.query(models.ComputeNode).\
                              options(joinedload('service')).\
                              filter(models.Service.host == host).\
                              filter_by(deleted=False).\
                              with_lockmode('update').\
                              first()
        if compute_node is None:
            raise exception.NotFound(_("No ComputeNode for %(host)s") %
                                     locals())

        if free_ram_mb != None:
            compute_node.free_ram_mb = free_ram_mb
        if free_disk_gb != None:
            compute_node.free_disk_gb = free_disk_gb
        if work != None:
            compute_node.current_workload = work
        if vms != None:
            compute_node.running_vms = vms

    return compute_node


###################
# Standby Table
@require_admin_context
def standby_service_create(context, values, session=None):
    """Creates a new standby service  ."""
    if not session:
        session = get_session()

    with session.begin(subtransactions=True):
        standbyServiceRef = models.StandbyService()
        session.add(standbyServiceRef)
        standbyServiceRef.update(values)
        standbyServiceRef.save(session=session)

    return standbyServiceRef

@require_admin_context
def standby_service_get_by_hostname(context, host_name, session=None):
    """get a standby service  ."""
    if not session:
        session = get_session()

    with session.begin():
        result = session.query(models.StandbyService).\
                             filter(models.StandbyService.host_name == host_name).\
                             filter_by(deleted=False)
        return result.first()

@require_admin_context
def standby_service_get_all(context, session=None):
    result = model_query(context, models.StandbyService, session=session).all()
    if not result:
        raise exception.ServiceNotFound("")
    return result

@require_admin_context
def standby_service_update(context, host_name, values, session=None):
    """update a standby service ."""
    session = get_session()
    values['updated_at'] = timeutils.utcnow()
    convert_datetimes(values, 'created_at', 'deleted_at', 'updated_at')

    with session.begin():
        result = session.query(models.StandbyService).\
                             filter(models.StandbyService.host_name == host_name).\
                             filter_by(deleted=False)
        standbyServiceRef = result.first()
        standbyServiceRef.update(values)
        standbyServiceRef.save(session=session)

@require_admin_context
def standby_setting_get_by_id(context, id, session=None):
    """get standby setting by id"""
    result = model_query(context, models.StandbySetting, session=session).\
            filter_by(id=id).first()
    if not result:
        raise exception.NotFound("setting")

    return result

@require_admin_context
def standby_setting_update_by_id(context, id, data, session=None):
    """get standby setting by id"""
    session = get_session()
    with session.begin():
        result = model_query(context, models.StandbySetting, session=session).\
                filter_by(id=id).first()
        if not result:
            raise exception.NotFound("setting")
        result.update(data)
        result.save(session=session)

    return result
###################


def _metadata_refs(metadata_dict, meta_class):
    metadata_refs = []
    if metadata_dict:
        for k, v in metadata_dict.iteritems():
            metadata_ref = meta_class()
            metadata_ref['key'] = k
            metadata_ref['value'] = v
            metadata_refs.append(metadata_ref)
    return metadata_refs


def _dict_with_extra_specs(inst_type_query):
    """Takes an instance, servicemanage, or instance type query returned
    by sqlalchemy and returns it as a dictionary, converting the
    extra_specs entry from a list of dicts:

    'extra_specs' : [{'key': 'k1', 'value': 'v1', ...}, ...]

    to a single dict:

    'extra_specs' : {'k1': 'v1'}

    """
    inst_type_dict = dict(inst_type_query)
    extra_specs = dict([(x['key'], x['value'])
                        for x in inst_type_query['extra_specs']])
    inst_type_dict['extra_specs'] = extra_specs
    return inst_type_dict


###################


@require_admin_context
def iscsi_target_count_by_host(context, host):
    return model_query(context, models.IscsiTarget).\
        filter_by(host=host).\
        count()


@require_admin_context
def iscsi_target_create_safe(context, values):
    iscsi_target_ref = models.IscsiTarget()

    for (key, value) in values.iteritems():
        iscsi_target_ref[key] = value
    try:
        iscsi_target_ref.save()
        return iscsi_target_ref
    except IntegrityError:
        return None


###################


@require_context
def quota_get(context, project_id, resource, session=None):
    result = model_query(context, models.Quota, session=session,
                         read_deleted="no").\
        filter_by(project_id=project_id).\
        filter_by(resource=resource).\
        first()

    if not result:
        raise exception.ProjectQuotaNotFound(project_id=project_id)

    return result


@require_context
def quota_get_all_by_project(context, project_id):
    authorize_project_context(context, project_id)

    rows = model_query(context, models.Quota, read_deleted="no").\
        filter_by(project_id=project_id).\
        all()

    result = {'project_id': project_id}
    for row in rows:
        result[row.resource] = row.hard_limit

    return result


@require_admin_context
def quota_create(context, project_id, resource, limit):
    quota_ref = models.Quota()
    quota_ref.project_id = project_id
    quota_ref.resource = resource
    quota_ref.hard_limit = limit
    quota_ref.save()
    return quota_ref


@require_admin_context
def quota_update(context, project_id, resource, limit):
    session = get_session()
    with session.begin():
        quota_ref = quota_get(context, project_id, resource, session=session)
        quota_ref.hard_limit = limit
        quota_ref.save(session=session)


@require_admin_context
def quota_destroy(context, project_id, resource):
    session = get_session()
    with session.begin():
        quota_ref = quota_get(context, project_id, resource, session=session)
        quota_ref.delete(session=session)


###################


@require_context
def quota_class_get(context, class_name, resource, session=None):
    result = model_query(context, models.QuotaClass, session=session,
                         read_deleted="no").\
        filter_by(class_name=class_name).\
        filter_by(resource=resource).\
        first()

    if not result:
        raise exception.QuotaClassNotFound(class_name=class_name)

    return result


@require_context
def quota_class_get_all_by_name(context, class_name):
    authorize_quota_class_context(context, class_name)

    rows = model_query(context, models.QuotaClass, read_deleted="no").\
        filter_by(class_name=class_name).\
        all()

    result = {'class_name': class_name}
    for row in rows:
        result[row.resource] = row.hard_limit

    return result


@require_admin_context
def quota_class_create(context, class_name, resource, limit):
    quota_class_ref = models.QuotaClass()
    quota_class_ref.class_name = class_name
    quota_class_ref.resource = resource
    quota_class_ref.hard_limit = limit
    quota_class_ref.save()
    return quota_class_ref


@require_admin_context
def quota_class_update(context, class_name, resource, limit):
    session = get_session()
    with session.begin():
        quota_class_ref = quota_class_get(context, class_name, resource,
                                          session=session)
        quota_class_ref.hard_limit = limit
        quota_class_ref.save(session=session)


@require_admin_context
def quota_class_destroy(context, class_name, resource):
    session = get_session()
    with session.begin():
        quota_class_ref = quota_class_get(context, class_name, resource,
                                          session=session)
        quota_class_ref.delete(session=session)


@require_admin_context
def quota_class_destroy_all_by_name(context, class_name):
    session = get_session()
    with session.begin():
        quota_classes = model_query(context, models.QuotaClass,
                                    session=session, read_deleted="no").\
            filter_by(class_name=class_name).\
            all()

        for quota_class_ref in quota_classes:
            quota_class_ref.delete(session=session)


###################


@require_context
def quota_usage_get(context, project_id, resource, session=None):
    result = model_query(context, models.QuotaUsage, session=session,
                         read_deleted="no").\
        filter_by(project_id=project_id).\
        filter_by(resource=resource).\
        first()

    if not result:
        raise exception.QuotaUsageNotFound(project_id=project_id)

    return result


@require_context
def quota_usage_get_all_by_project(context, project_id):
    authorize_project_context(context, project_id)

    rows = model_query(context, models.QuotaUsage, read_deleted="no").\
        filter_by(project_id=project_id).\
        all()

    result = {'project_id': project_id}
    for row in rows:
        result[row.resource] = dict(in_use=row.in_use, reserved=row.reserved)

    return result


@require_admin_context
def quota_usage_create(context, project_id, resource, in_use, reserved,
                       until_refresh, session=None):
    quota_usage_ref = models.QuotaUsage()
    quota_usage_ref.project_id = project_id
    quota_usage_ref.resource = resource
    quota_usage_ref.in_use = in_use
    quota_usage_ref.reserved = reserved
    quota_usage_ref.until_refresh = until_refresh
    quota_usage_ref.save(session=session)

    return quota_usage_ref


###################


@require_context
def reservation_get(context, uuid, session=None):
    result = model_query(context, models.Reservation, session=session,
                         read_deleted="no").\
        filter_by(uuid=uuid).first()

    if not result:
        raise exception.ReservationNotFound(uuid=uuid)

    return result


@require_context
def reservation_get_all_by_project(context, project_id):
    authorize_project_context(context, project_id)

    rows = model_query(context, models.Reservation, read_deleted="no").\
        filter_by(project_id=project_id).all()

    result = {'project_id': project_id}
    for row in rows:
        result.setdefault(row.resource, {})
        result[row.resource][row.uuid] = row.delta

    return result


@require_admin_context
def reservation_create(context, uuid, usage, project_id, resource, delta,
                       expire, session=None):
    reservation_ref = models.Reservation()
    reservation_ref.uuid = uuid
    reservation_ref.usage_id = usage['id']
    reservation_ref.project_id = project_id
    reservation_ref.resource = resource
    reservation_ref.delta = delta
    reservation_ref.expire = expire
    reservation_ref.save(session=session)
    return reservation_ref


@require_admin_context
def reservation_destroy(context, uuid):
    session = get_session()
    with session.begin():
        reservation_ref = reservation_get(context, uuid, session=session)
        reservation_ref.delete(session=session)


###################


# NOTE(johannes): The quota code uses SQL locking to ensure races don't
# cause under or over counting of resources. To avoid deadlocks, this
# code always acquires the lock on quota_usages before acquiring the lock
# on reservations.

def _get_quota_usages(context, session, project_id):
    # Broken out for testability
    rows = model_query(context, models.QuotaUsage,
                       read_deleted="no",
                       session=session).\
        filter_by(project_id=project_id).\
        with_lockmode('update').\
        all()
    return dict((row.resource, row) for row in rows)


@require_context
def quota_reserve(context, resources, quotas, deltas, expire,
                  until_refresh, max_age, project_id=None):
    elevated = context.elevated()
    session = get_session()
    with session.begin():
        if project_id is None:
            project_id = context.project_id

        # Get the current usages
        usages = _get_quota_usages(context, session, project_id)

        # Handle usage refresh
        work = set(deltas.keys())
        while work:
            resource = work.pop()

            # Do we need to refresh the usage?
            refresh = False
            if resource not in usages:
                usages[resource] = quota_usage_create(elevated,
                                                      project_id,
                                                      resource,
                                                      0, 0,
                                                      until_refresh or None,
                                                      session=session)
                refresh = True
            elif usages[resource].in_use < 0:
                # Negative in_use count indicates a desync, so try to
                # heal from that...
                refresh = True
            elif usages[resource].until_refresh is not None:
                usages[resource].until_refresh -= 1
                if usages[resource].until_refresh <= 0:
                    refresh = True
            elif max_age and (usages[resource].updated_at -
                              timeutils.utcnow()).seconds >= max_age:
                refresh = True

            # OK, refresh the usage
            if refresh:
                # Grab the sync routine
                sync = resources[resource].sync

                updates = sync(elevated, project_id, session)
                for res, in_use in updates.items():
                    # Make sure we have a destination for the usage!
                    if res not in usages:
                        usages[res] = quota_usage_create(elevated,
                                                         project_id,
                                                         res,
                                                         0, 0,
                                                         until_refresh or None,
                                                         session=session)

                    # Update the usage
                    usages[res].in_use = in_use
                    usages[res].until_refresh = until_refresh or None

                    # Because more than one resource may be refreshed
                    # by the call to the sync routine, and we don't
                    # want to double-sync, we make sure all refreshed
                    # resources are dropped from the work set.
                    work.discard(res)

                    # NOTE(Vek): We make the assumption that the sync
                    #            routine actually refreshes the
                    #            resources that it is the sync routine
                    #            for.  We don't check, because this is
                    #            a best-effort mechanism.

        # Check for deltas that would go negative
        unders = [resource for resource, delta in deltas.items()
                  if delta < 0 and
                  delta + usages[resource].in_use < 0]

        # Now, let's check the quotas
        # NOTE(Vek): We're only concerned about positive increments.
        #            If a project has gone over quota, we want them to
        #            be able to reduce their usage without any
        #            problems.
        overs = [resource for resource, delta in deltas.items()
                 if quotas[resource] >= 0 and delta >= 0 and
                 quotas[resource] < delta + usages[resource].total]

        # NOTE(Vek): The quota check needs to be in the transaction,
        #            but the transaction doesn't fail just because
        #            we're over quota, so the OverQuota raise is
        #            outside the transaction.  If we did the raise
        #            here, our usage updates would be discarded, but
        #            they're not invalidated by being over-quota.

        # Create the reservations
        if not overs:
            reservations = []
            for resource, delta in deltas.items():
                reservation = reservation_create(elevated,
                                                 str(uuid.uuid4()),
                                                 usages[resource],
                                                 project_id,
                                                 resource, delta, expire,
                                                 session=session)
                reservations.append(reservation.uuid)

                # Also update the reserved quantity
                # NOTE(Vek): Again, we are only concerned here about
                #            positive increments.  Here, though, we're
                #            worried about the following scenario:
                #
                #            1) User initiates resize down.
                #            2) User allocates a new instance.
                #            3) Resize down fails or is reverted.
                #            4) User is now over quota.
                #
                #            To prevent this, we only update the
                #            reserved value if the delta is positive.
                if delta > 0:
                    usages[resource].reserved += delta

        # Apply updates to the usages table
        for usage_ref in usages.values():
            usage_ref.save(session=session)

    if unders:
        LOG.warning(_("Change will make usage less than 0 for the following "
                      "resources: %(unders)s") % locals())
    if overs:
        usages = dict((k, dict(in_use=v['in_use'], reserved=v['reserved']))
                      for k, v in usages.items())
        raise exception.OverQuota(overs=sorted(overs), quotas=quotas,
                                  usages=usages)

    return reservations


def _quota_reservations(session, context, reservations):
    """Return the relevant reservations."""

    # Get the listed reservations
    return model_query(context, models.Reservation,
                       read_deleted="no",
                       session=session).\
        filter(models.Reservation.uuid.in_(reservations)).\
        with_lockmode('update').\
        all()


@require_context
def reservation_commit(context, reservations, project_id=None):
    session = get_session()
    with session.begin():
        usages = _get_quota_usages(context, session, project_id)

        for reservation in _quota_reservations(session, context, reservations):
            usage = usages[reservation.resource]
            if reservation.delta >= 0:
                usage.reserved -= reservation.delta
            usage.in_use += reservation.delta

            reservation.delete(session=session)

        for usage in usages.values():
            usage.save(session=session)


@require_context
def reservation_rollback(context, reservations, project_id=None):
    session = get_session()
    with session.begin():
        usages = _get_quota_usages(context, session, project_id)

        for reservation in _quota_reservations(session, context, reservations):
            usage = usages[reservation.resource]
            if reservation.delta >= 0:
                usage.reserved -= reservation.delta

            reservation.delete(session=session)

        for usage in usages.values():
            usage.save(session=session)


@require_admin_context
def quota_destroy_all_by_project(context, project_id):
    session = get_session()
    with session.begin():
        quotas = model_query(context, models.Quota, session=session,
                             read_deleted="no").\
            filter_by(project_id=project_id).\
            all()

        for quota_ref in quotas:
            quota_ref.delete(session=session)

        quota_usages = model_query(context, models.QuotaUsage,
                                   session=session, read_deleted="no").\
            filter_by(project_id=project_id).\
            all()

        for quota_usage_ref in quota_usages:
            quota_usage_ref.delete(session=session)

        reservations = model_query(context, models.Reservation,
                                   session=session, read_deleted="no").\
            filter_by(project_id=project_id).\
            all()

        for reservation_ref in reservations:
            reservation_ref.delete(session=session)


@require_admin_context
def reservation_expire(context):
    session = get_session()
    with session.begin():
        current_time = timeutils.utcnow()
        results = model_query(context, models.Reservation, session=session,
                              read_deleted="no").\
            filter(models.Reservation.expire < current_time).\
            all()

        if results:
            for reservation in results:
                if reservation.delta >= 0:
                    reservation.usage.reserved -= reservation.delta
                    reservation.usage.save(session=session)

                reservation.delete(session=session)


###################


@require_admin_context
def servicemanage_allocate_iscsi_target(context, servicemanage_id, host):
    session = get_session()
    with session.begin():
        iscsi_target_ref = model_query(context, models.IscsiTarget,
                                       session=session, read_deleted="no").\
            filter_by(servicemanage=None).\
            filter_by(host=host).\
            with_lockmode('update').\
            first()

        # NOTE(vish): if with_lockmode isn't supported, as in sqlite,
        #             then this has concurrency issues
        if not iscsi_target_ref:
            raise db.NoMoreTargets()

        iscsi_target_ref.servicemanage_id = servicemanage_id
        session.add(iscsi_target_ref)

    return iscsi_target_ref.target_num


@require_admin_context
def servicemanage_attached(context, servicemanage_id, instance_uuid, mountpoint):
    if not uuidutils.is_uuid_like(instance_uuid):
        raise exception.InvalidUUID(uuid=instance_uuid)

    session = get_session()
    with session.begin():
        servicemanage_ref = servicemanage_get(context, servicemanage_id, session=session)
        servicemanage_ref['status'] = 'in-use'
        servicemanage_ref['mountpoint'] = mountpoint
        servicemanage_ref['attach_status'] = 'attached'
        servicemanage_ref['instance_uuid'] = instance_uuid
        servicemanage_ref.save(session=session)


@require_context
def servicemanage_create(context, values):
    values['servicemanage_metadata'] = _metadata_refs(values.get('metadata'),
                                               models.ServiceManageMetadata)
    servicemanage_ref = models.ServiceManage()
    if not values.get('id'):
        values['id'] = str(uuid.uuid4())
    servicemanage_ref.update(values)

    session = get_session()
    with session.begin():
        servicemanage_ref.save(session=session)

    return servicemanage_get(context, values['id'], session=session)


@require_admin_context
def servicemanage_data_get_for_host(context, host, session=None):
    result = model_query(context,
                         func.count(models.ServiceManage.id),
                         func.sum(models.ServiceManage.size),
                         read_deleted="no",
                         session=session).\
        filter_by(host=host).\
        first()

    # NOTE(vish): convert None to 0
    return (result[0] or 0, result[1] or 0)


@require_admin_context
def servicemanage_data_get_for_project(context, project_id, session=None):
    result = model_query(context,
                         func.count(models.ServiceManage.id),
                         func.sum(models.ServiceManage.size),
                         read_deleted="no",
                         session=session).\
        filter_by(project_id=project_id).\
        first()

    # NOTE(vish): convert None to 0
    return (result[0] or 0, result[1] or 0)


@require_admin_context
def servicemanage_destroy(context, servicemanage_id):
    session = get_session()
    with session.begin():
        session.query(models.ServiceManage).\
            filter_by(id=servicemanage_id).\
            update({'status': 'deleted',
                    'deleted': True,
                    'deleted_at': timeutils.utcnow(),
                    'updated_at': literal_column('updated_at')})
        session.query(models.IscsiTarget).\
            filter_by(servicemanage_id=servicemanage_id).\
            update({'servicemanage_id': None})
        session.query(models.ServiceManageMetadata).\
            filter_by(servicemanage_id=servicemanage_id).\
            update({'deleted': True,
                    'deleted_at': timeutils.utcnow(),
                    'updated_at': literal_column('updated_at')})


@require_admin_context
def servicemanage_detached(context, servicemanage_id):
    session = get_session()
    with session.begin():
        servicemanage_ref = servicemanage_get(context, servicemanage_id, session=session)
        servicemanage_ref['status'] = 'available'
        servicemanage_ref['mountpoint'] = None
        servicemanage_ref['attach_status'] = 'detached'
        servicemanage_ref['instance_uuid'] = None
        servicemanage_ref.save(session=session)


@require_context
def _servicemanage_get_query(context, session=None, project_only=False):
    return model_query(context, models.ServiceManage, session=session,
                       project_only=project_only).\
        options(joinedload('servicemanage_metadata')).\
        options(joinedload('servicemanage_type'))


@require_context
def servicemanage_get(context, servicemanage_id, session=None):
    result = _servicemanage_get_query(context, session=session, project_only=True).\
        filter_by(id=servicemanage_id).\
        first()

    if not result:
        raise exception.ServiceManageNotFound(servicemanage_id=servicemanage_id)

    return result


@require_admin_context
def servicemanage_get_all(context, marker, limit, sort_key, sort_dir):
    query = _servicemanage_get_query(context)

    marker_servicemanage = None
    if marker is not None:
        marker_servicemanage = servicemanage_get(context, marker)

    query = sqlalchemyutils.paginate_query(query, models.ServiceManage, limit,
                                           [sort_key, 'created_at', 'id'],
                                           marker=marker_servicemanage,
                                           sort_dir=sort_dir)

    return query.all()


@require_admin_context
def servicemanage_get_all_by_host(context, host):
    return _servicemanage_get_query(context).filter_by(host=host).all()


@require_admin_context
def servicemanage_get_all_by_instance_uuid(context, instance_uuid):
    result = model_query(context, models.ServiceManage, read_deleted="no").\
        options(joinedload('servicemanage_metadata')).\
        options(joinedload('servicemanage_type')).\
        filter_by(instance_uuid=instance_uuid).\
        all()

    if not result:
        return []

    return result


@require_context
def servicemanage_get_all_by_project(context, project_id, marker, limit, sort_key,
                              sort_dir):
    authorize_project_context(context, project_id)
    query = _servicemanage_get_query(context).filter_by(project_id=project_id)

    marker_servicemanage = None
    if marker is not None:
        marker_servicemanage = servicemanage_get(context, marker)

    query = sqlalchemyutils.paginate_query(query, models.ServiceManage, limit,
                                           [sort_key, 'created_at', 'id'],
                                           marker=marker_servicemanage,
                                           sort_dir=sort_dir)

    return query.all()


@require_admin_context
def servicemanage_get_iscsi_target_num(context, servicemanage_id):
    result = model_query(context, models.IscsiTarget, read_deleted="yes").\
        filter_by(servicemanage_id=servicemanage_id).\
        first()

    if not result:
        raise exception.ISCSITargetNotFoundForServiceManage(servicemanage_id=servicemanage_id)

    return result.target_num


@require_context
def servicemanage_update(context, servicemanage_id, values):
    session = get_session()
    metadata = values.get('metadata')
    if metadata is not None:
        servicemanage_metadata_update(context,
                               servicemanage_id,
                               values.pop('metadata'),
                               delete=True)
    with session.begin():
        servicemanage_ref = servicemanage_get(context, servicemanage_id, session=session)
        servicemanage_ref.update(values)
        servicemanage_ref.save(session=session)
        return servicemanage_ref


####################

def _servicemanage_metadata_get_query(context, servicemanage_id, session=None):
    return model_query(context, models.ServiceManageMetadata,
                       session=session, read_deleted="no").\
        filter_by(servicemanage_id=servicemanage_id)


@require_context
@require_servicemanage_exists
def servicemanage_metadata_get(context, servicemanage_id):
    rows = _servicemanage_metadata_get_query(context, servicemanage_id).all()
    result = {}
    for row in rows:
        result[row['key']] = row['value']

    return result


@require_context
@require_servicemanage_exists
def servicemanage_metadata_delete(context, servicemanage_id, key):
    _servicemanage_metadata_get_query(context, servicemanage_id).\
        filter_by(key=key).\
        update({'deleted': True,
                'deleted_at': timeutils.utcnow(),
                'updated_at': literal_column('updated_at')})


@require_context
@require_servicemanage_exists
def servicemanage_metadata_get_item(context, servicemanage_id, key, session=None):
    result = _servicemanage_metadata_get_query(context, servicemanage_id, session=session).\
        filter_by(key=key).\
        first()

    if not result:
        raise exception.ServiceManageMetadataNotFound(metadata_key=key,
                                               servicemanage_id=servicemanage_id)
    return result


@require_context
@require_servicemanage_exists
def servicemanage_metadata_update(context, servicemanage_id, metadata, delete):
    session = get_session()

    # Set existing metadata to deleted if delete argument is True
    if delete:
        original_metadata = servicemanage_metadata_get(context, servicemanage_id)
        for meta_key, meta_value in original_metadata.iteritems():
            if meta_key not in metadata:
                meta_ref = servicemanage_metadata_get_item(context, servicemanage_id,
                                                    meta_key, session)
                meta_ref.update({'deleted': True})
                meta_ref.save(session=session)

    meta_ref = None

    # Now update all existing items with new values, or create new meta objects
    for meta_key, meta_value in metadata.items():

        # update the value whether it exists or not
        item = {"value": meta_value}

        try:
            meta_ref = servicemanage_metadata_get_item(context, servicemanage_id,
                                                meta_key, session)
        except exception.ServiceManageMetadataNotFound as e:
            meta_ref = models.ServiceManageMetadata()
            item.update({"key": meta_key, "servicemanage_id": servicemanage_id})

        meta_ref.update(item)
        meta_ref.save(session=session)

    return metadata


###################


@require_context
def snapshot_create(context, values):
    values['snapshot_metadata'] = _metadata_refs(values.get('metadata'),
                                                 models.SnapshotMetadata)
    snapshot_ref = models.Snapshot()
    if not values.get('id'):
        values['id'] = str(uuid.uuid4())
    snapshot_ref.update(values)

    session = get_session()
    with session.begin():
        snapshot_ref.save(session=session)

    return snapshot_get(context, values['id'], session=session)


@require_admin_context
def snapshot_destroy(context, snapshot_id):
    session = get_session()
    with session.begin():
        session.query(models.Snapshot).\
            filter_by(id=snapshot_id).\
            update({'status': 'deleted',
                    'deleted': True,
                    'deleted_at': timeutils.utcnow(),
                    'updated_at': literal_column('updated_at')})


@require_context
def snapshot_get(context, snapshot_id, session=None):
    result = model_query(context, models.Snapshot, session=session,
                         project_only=True).\
        filter_by(id=snapshot_id).\
        first()

    if not result:
        raise exception.SnapshotNotFound(snapshot_id=snapshot_id)

    return result


@require_admin_context
def snapshot_get_all(context):
    return model_query(context, models.Snapshot).all()


@require_context
def snapshot_get_all_for_servicemanage(context, servicemanage_id):
    return model_query(context, models.Snapshot, read_deleted='no',
                       project_only=True).\
        filter_by(servicemanage_id=servicemanage_id).all()


@require_context
def snapshot_get_all_by_project(context, project_id):
    authorize_project_context(context, project_id)
    return model_query(context, models.Snapshot).\
        filter_by(project_id=project_id).\
        all()


@require_context
def snapshot_data_get_for_project(context, project_id, session=None):
    authorize_project_context(context, project_id)
    result = model_query(context,
                         func.count(models.Snapshot.id),
                         func.sum(models.Snapshot.servicemanage_size),
                         read_deleted="no",
                         session=session).\
        filter_by(project_id=project_id).\
        first()

    # NOTE(vish): convert None to 0
    return (result[0] or 0, result[1] or 0)


@require_context
def snapshot_update(context, snapshot_id, values):
    session = get_session()
    with session.begin():
        snapshot_ref = snapshot_get(context, snapshot_id, session=session)
        snapshot_ref.update(values)
        snapshot_ref.save(session=session)

####################


def _snapshot_metadata_get_query(context, snapshot_id, session=None):
    return model_query(context, models.SnapshotMetadata,
                       session=session, read_deleted="no").\
        filter_by(snapshot_id=snapshot_id)


@require_context
@require_snapshot_exists
def snapshot_metadata_get(context, snapshot_id):
    rows = _snapshot_metadata_get_query(context, snapshot_id).all()
    result = {}
    for row in rows:
        result[row['key']] = row['value']

    return result


@require_context
@require_snapshot_exists
def snapshot_metadata_delete(context, snapshot_id, key):
    _snapshot_metadata_get_query(context, snapshot_id).\
        filter_by(key=key).\
        update({'deleted': True,
                'deleted_at': timeutils.utcnow(),
                'updated_at': literal_column('updated_at')})


@require_context
@require_snapshot_exists
def snapshot_metadata_get_item(context, snapshot_id, key, session=None):
    result = _snapshot_metadata_get_query(context,
                                          snapshot_id,
                                          session=session).\
        filter_by(key=key).\
        first()

    if not result:
        raise exception.SnapshotMetadataNotFound(metadata_key=key,
                                                 snapshot_id=snapshot_id)
    return result


@require_context
@require_snapshot_exists
def snapshot_metadata_update(context, snapshot_id, metadata, delete):
    session = get_session()

    # Set existing metadata to deleted if delete argument is True
    if delete:
        original_metadata = snapshot_metadata_get(context, snapshot_id)
        for meta_key, meta_value in original_metadata.iteritems():
            if meta_key not in metadata:
                meta_ref = snapshot_metadata_get_item(context, snapshot_id,
                                                      meta_key, session)
                meta_ref.update({'deleted': True})
                meta_ref.save(session=session)

    meta_ref = None

    # Now update all existing items with new values, or create new meta objects
    for meta_key, meta_value in metadata.items():

        # update the value whether it exists or not
        item = {"value": meta_value}

        try:
            meta_ref = snapshot_metadata_get_item(context, snapshot_id,
                                                  meta_key, session)
        except exception.SnapshotMetadataNotFound as e:
            meta_ref = models.SnapshotMetadata()
            item.update({"key": meta_key, "snapshot_id": snapshot_id})

        meta_ref.update(item)
        meta_ref.save(session=session)

    return metadata

###################


@require_admin_context
def migration_create(context, values):
    migration = models.Migration()
    migration.update(values)
    migration.save()
    return migration


@require_admin_context
def migration_update(context, id, values):
    session = get_session()
    with session.begin():
        migration = migration_get(context, id, session=session)
        migration.update(values)
        migration.save(session=session)
        return migration


@require_admin_context
def migration_get(context, id, session=None):
    result = model_query(context, models.Migration, session=session,
                         read_deleted="yes").\
        filter_by(id=id).\
        first()

    if not result:
        raise exception.MigrationNotFound(migration_id=id)

    return result


@require_admin_context
def migration_get_by_instance_and_status(context, instance_uuid, status):
    result = model_query(context, models.Migration, read_deleted="yes").\
        filter_by(instance_uuid=instance_uuid).\
        filter_by(status=status).\
        first()

    if not result:
        raise exception.MigrationNotFoundByStatus(instance_id=instance_uuid,
                                                  status=status)

    return result


@require_admin_context
def migration_get_all_unconfirmed(context, confirm_window, session=None):
    confirm_window = timeutils.utcnow() - datetime.timedelta(
        seconds=confirm_window)

    return model_query(context, models.Migration, session=session,
                       read_deleted="yes").\
        filter(models.Migration.updated_at <= confirm_window).\
        filter_by(status="finished").\
        all()


##################


@require_admin_context
def servicemanage_type_create(context, values):
    """Create a new instance type. In order to pass in extra specs,
    the values dict should contain a 'extra_specs' key/value pair:

    {'extra_specs' : {'k1': 'v1', 'k2': 'v2', ...}}

    """
    if not values.get('id'):
        values['id'] = str(uuid.uuid4())

    session = get_session()
    with session.begin():
        try:
            servicemanage_type_get_by_name(context, values['name'], session)
            raise exception.ServiceManageTypeExists(id=values['name'])
        except exception.ServiceManageTypeNotFoundByName:
            pass
        try:
            servicemanage_type_get(context, values['id'], session)
            raise exception.ServiceManageTypeExists(id=values['id'])
        except exception.ServiceManageTypeNotFound:
            pass
        try:
            values['extra_specs'] = _metadata_refs(values.get('extra_specs'),
                                                   models.ServiceManageTypeExtraSpecs)
            servicemanage_type_ref = models.ServiceManageTypes()
            servicemanage_type_ref.update(values)
            servicemanage_type_ref.save()
        except Exception, e:
            raise exception.DBError(e)
        return servicemanage_type_ref


@require_context
def servicemanage_type_get_all(context, inactive=False, filters=None):
    """
    Returns a dict describing all servicemanage_types with name as key.
    """
    filters = filters or {}

    read_deleted = "yes" if inactive else "no"
    rows = model_query(context, models.ServiceManageTypes,
                       read_deleted=read_deleted).\
        options(joinedload('extra_specs')).\
        order_by("name").\
        all()

    # TODO(sirp): this patern of converting rows to a result with extra_specs
    # is repeated quite a bit, might be worth creating a method for it
    result = {}
    for row in rows:
        result[row['name']] = _dict_with_extra_specs(row)

    return result


@require_context
def servicemanage_type_get(context, id, session=None):
    """Returns a dict describing specific servicemanage_type"""
    result = model_query(context, models.ServiceManageTypes, session=session).\
        options(joinedload('extra_specs')).\
        filter_by(id=id).\
        first()

    if not result:
        raise exception.ServiceManageTypeNotFound(servicemanage_type_id=id)

    return _dict_with_extra_specs(result)


@require_context
def servicemanage_type_get_by_name(context, name, session=None):
    """Returns a dict describing specific servicemanage_type"""
    result = model_query(context, models.ServiceManageTypes, session=session).\
        options(joinedload('extra_specs')).\
        filter_by(name=name).\
        first()

    if not result:
        raise exception.ServiceManageTypeNotFoundByName(servicemanage_type_name=name)
    else:
        return _dict_with_extra_specs(result)


@require_admin_context
def servicemanage_type_destroy(context, id):
    servicemanage_type_get(context, id)

    session = get_session()
    with session.begin():
        session.query(models.ServiceManageTypes).\
            filter_by(id=id).\
            update({'deleted': True,
                    'deleted_at': timeutils.utcnow(),
                    'updated_at': literal_column('updated_at')})
        session.query(models.ServiceManageTypeExtraSpecs).\
            filter_by(servicemanage_type_id=id).\
            update({'deleted': True,
                    'deleted_at': timeutils.utcnow(),
                    'updated_at': literal_column('updated_at')})


@require_context
def servicemanage_get_active_by_window(context,
                                begin,
                                end=None,
                                project_id=None):
    """Return servicemanages that were active during window."""
    session = get_session()
    query = session.query(models.ServiceManage)

    query = query.filter(or_(models.ServiceManage.deleted_at == None,
                             models.ServiceManage.deleted_at > begin))
    if end:
        query = query.filter(models.ServiceManage.created_at < end)
    if project_id:
        query = query.filter_by(project_id=project_id)

    return query.all()


####################


def _servicemanage_type_extra_specs_query(context, servicemanage_type_id, session=None):
    return model_query(context, models.ServiceManageTypeExtraSpecs, session=session,
                       read_deleted="no").\
        filter_by(servicemanage_type_id=servicemanage_type_id)


@require_context
def servicemanage_type_extra_specs_get(context, servicemanage_type_id):
    rows = _servicemanage_type_extra_specs_query(context, servicemanage_type_id).\
        all()

    result = {}
    for row in rows:
        result[row['key']] = row['value']

    return result


@require_context
def servicemanage_type_extra_specs_delete(context, servicemanage_type_id, key):
    _servicemanage_type_extra_specs_query(context, servicemanage_type_id).\
        filter_by(key=key).\
        update({'deleted': True,
                'deleted_at': timeutils.utcnow(),
                'updated_at': literal_column('updated_at')})


@require_context
def servicemanage_type_extra_specs_get_item(context, servicemanage_type_id, key,
                                     session=None):
    result = _servicemanage_type_extra_specs_query(
        context, servicemanage_type_id, session=session).\
        filter_by(key=key).\
        first()

    if not result:
        raise exception.ServiceManageTypeExtraSpecsNotFound(
            extra_specs_key=key,
            servicemanage_type_id=servicemanage_type_id)

    return result


@require_context
def servicemanage_type_extra_specs_update_or_create(context, servicemanage_type_id,
                                             specs):
    session = get_session()
    spec_ref = None
    for key, value in specs.iteritems():
        try:
            spec_ref = servicemanage_type_extra_specs_get_item(
                context, servicemanage_type_id, key, session)
        except exception.ServiceManageTypeExtraSpecsNotFound, e:
            spec_ref = models.ServiceManageTypeExtraSpecs()
        spec_ref.update({"key": key, "value": value,
                         "servicemanage_type_id": servicemanage_type_id,
                         "deleted": False})
        spec_ref.save(session=session)
    return specs


####################


@require_context
@require_servicemanage_exists
def servicemanage_glance_metadata_get(context, servicemanage_id, session=None):
    """Return the Glance metadata for the specified servicemanage."""
    if not session:
        session = get_session()

    return session.query(models.ServiceManageGlanceMetadata).\
        filter_by(servicemanage_id=servicemanage_id).\
        filter_by(deleted=False).all()


@require_context
@require_snapshot_exists
def servicemanage_snapshot_glance_metadata_get(context, snapshot_id, session=None):
    """Return the Glance metadata for the specified snapshot."""
    if not session:
        session = get_session()

    return session.query(models.ServiceManageGlanceMetadata).\
        filter_by(snapshot_id=snapshot_id).\
        filter_by(deleted=False).all()


@require_context
@require_servicemanage_exists
def servicemanage_glance_metadata_create(context, servicemanage_id, key, value,
                                  session=None):
    """
    Update the Glance metadata for a servicemanage by adding a new key:value pair.
    This API does not support changing the value of a key once it has been
    created.
    """
    if session is None:
        session = get_session()

    with session.begin():
        rows = session.query(models.ServiceManageGlanceMetadata).\
            filter_by(servicemanage_id=servicemanage_id).\
            filter_by(key=key).\
            filter_by(deleted=False).all()

        if len(rows) > 0:
            raise exception.GlanceMetadataExists(key=key,
                                                 servicemanage_id=servicemanage_id)

        vol_glance_metadata = models.ServiceManageGlanceMetadata()
        vol_glance_metadata.servicemanage_id = servicemanage_id
        vol_glance_metadata.key = key
        vol_glance_metadata.value = value

        vol_glance_metadata.save(session=session)

    return


@require_context
@require_snapshot_exists
def servicemanage_glance_metadata_copy_to_snapshot(context, snapshot_id, servicemanage_id,
                                            session=None):
    """
    Update the Glance metadata for a snapshot by copying all of the key:value
    pairs from the originating servicemanage. This is so that a servicemanage created from
    the snapshot will retain the original metadata.
    """
    if session is None:
        session = get_session()

    metadata = servicemanage_glance_metadata_get(context, servicemanage_id, session=session)
    with session.begin():
        for meta in metadata:
            vol_glance_metadata = models.ServiceManageGlanceMetadata()
            vol_glance_metadata.snapshot_id = snapshot_id
            vol_glance_metadata.key = meta['key']
            vol_glance_metadata.value = meta['value']

            vol_glance_metadata.save(session=session)


@require_context
@require_servicemanage_exists
def servicemanage_glance_metadata_copy_from_servicemanage_to_servicemanage(context,
                                                      src_servicemanage_id,
                                                      servicemanage_id,
                                                      session=None):
    """
    Update the Glance metadata for a servicemanage by copying all of the key:value
    pairs from the originating servicemanage. This is so that a servicemanage created from
    the servicemanage (clone) will retain the original metadata.
    """
    if session is None:
        session = get_session()

    metadata = servicemanage_glance_metadata_get(context,
                                          src_servicemanage_id,
                                          session=session)
    with session.begin():
        for meta in metadata:
            vol_glance_metadata = models.ServiceManageGlanceMetadata()
            vol_glance_metadata.servicemanage_id = servicemanage_id
            vol_glance_metadata.key = meta['key']
            vol_glance_metadata.value = meta['value']

            vol_glance_metadata.save(session=session)


@require_context
@require_servicemanage_exists
def servicemanage_glance_metadata_copy_to_servicemanage(context, servicemanage_id, snapshot_id,
                                          session=None):
    """
    Update the Glance metadata from a servicemanage (created from a snapshot) by
    copying all of the key:value pairs from the originating snapshot. This is
    so that the Glance metadata from the original servicemanage is retained.
    """
    if session is None:
        session = get_session()

    metadata = servicemanage_snapshot_glance_metadata_get(context, snapshot_id,
                                                   session=session)
    with session.begin():
        for meta in metadata:
            vol_glance_metadata = models.ServiceManageGlanceMetadata()
            vol_glance_metadata.servicemanage_id = servicemanage_id
            vol_glance_metadata.key = meta['key']
            vol_glance_metadata.value = meta['value']

            vol_glance_metadata.save(session=session)


@require_context
def servicemanage_glance_metadata_delete_by_servicemanage(context, servicemanage_id):
    session = get_session()
    session.query(models.ServiceManageGlanceMetadata).\
        filter_by(servicemanage_id=servicemanage_id).\
        filter_by(deleted=False).\
        update({'deleted': True,
                'deleted_at': timeutils.utcnow(),
                'updated_at': literal_column('updated_at')})


@require_context
def servicemanage_glance_metadata_delete_by_snapshot(context, snapshot_id):
    session = get_session()
    session.query(models.ServiceManageGlanceMetadata).\
        filter_by(snapshot_id=snapshot_id).\
        filter_by(deleted=False).\
        update({'deleted': True,
                'deleted_at': timeutils.utcnow(),
                'updated_at': literal_column('updated_at')})


####################


@require_admin_context
def sm_backend_conf_create(context, values):
    backend_conf = models.SMBackendConf()
    backend_conf.update(values)
    backend_conf.save()
    return backend_conf


@require_admin_context
def sm_backend_conf_update(context, sm_backend_id, values):
    session = get_session()
    with session.begin():
        backend_conf = model_query(context, models.SMBackendConf,
                                   session=session,
                                   read_deleted="yes").\
            filter_by(id=sm_backend_id).\
            first()

        if not backend_conf:
            raise exception.NotFound(
                _("No backend config with id %(sm_backend_id)s") % locals())

        backend_conf.update(values)
        backend_conf.save(session=session)
    return backend_conf


@require_admin_context
def sm_backend_conf_delete(context, sm_backend_id):
    # FIXME(sirp): for consistency, shouldn't this just mark as deleted with
    # `purge` actually deleting the record?
    session = get_session()
    with session.begin():
        model_query(context, models.SMBackendConf, session=session,
                    read_deleted="yes").\
            filter_by(id=sm_backend_id).\
            delete()


@require_admin_context
def sm_backend_conf_get(context, sm_backend_id):
    result = model_query(context, models.SMBackendConf, read_deleted="yes").\
        filter_by(id=sm_backend_id).\
        first()

    if not result:
        raise exception.NotFound(_("No backend config with id "
                                   "%(sm_backend_id)s") % locals())

    return result


@require_admin_context
def sm_backend_conf_get_by_sr(context, sr_uuid):
    return model_query(context, models.SMBackendConf, read_deleted="yes").\
        filter_by(sr_uuid=sr_uuid).\
        first()


@require_admin_context
def sm_backend_conf_get_all(context):
    return model_query(context, models.SMBackendConf, read_deleted="yes").\
        all()


####################


def _sm_flavor_get_query(context, sm_flavor_label, session=None):
    return model_query(context, models.SMFlavors, session=session,
                       read_deleted="yes").\
        filter_by(label=sm_flavor_label)


@require_admin_context
def sm_flavor_create(context, values):
    sm_flavor = models.SMFlavors()
    sm_flavor.update(values)
    sm_flavor.save()
    return sm_flavor


@require_admin_context
def sm_flavor_update(context, sm_flavor_label, values):
    sm_flavor = sm_flavor_get(context, sm_flavor_label)
    sm_flavor.update(values)
    sm_flavor.save()
    return sm_flavor


@require_admin_context
def sm_flavor_delete(context, sm_flavor_label):
    session = get_session()
    with session.begin():
        _sm_flavor_get_query(context, sm_flavor_label).delete()


@require_admin_context
def sm_flavor_get(context, sm_flavor_label):
    result = _sm_flavor_get_query(context, sm_flavor_label).first()

    if not result:
        raise exception.NotFound(
            _("No sm_flavor called %(sm_flavor)s") % locals())

    return result


@require_admin_context
def sm_flavor_get_all(context):
    return model_query(context, models.SMFlavors, read_deleted="yes").all()


###############################


def _sm_servicemanage_get_query(context, servicemanage_id, session=None):
    return model_query(context, models.SMServiceManage, session=session,
                       read_deleted="yes").\
        filter_by(id=servicemanage_id)


def sm_servicemanage_create(context, values):
    sm_servicemanage = models.SMServiceManage()
    sm_servicemanage.update(values)
    sm_servicemanage.save()
    return sm_servicemanage


def sm_servicemanage_update(context, servicemanage_id, values):
    sm_servicemanage = sm_servicemanage_get(context, servicemanage_id)
    sm_servicemanage.update(values)
    sm_servicemanage.save()
    return sm_servicemanage


def sm_servicemanage_delete(context, servicemanage_id):
    session = get_session()
    with session.begin():
        _sm_servicemanage_get_query(context, servicemanage_id, session=session).delete()


def sm_servicemanage_get(context, servicemanage_id):
    result = _sm_servicemanage_get_query(context, servicemanage_id).first()

    if not result:
        raise exception.NotFound(
            _("No sm_servicemanage with id %(servicemanage_id)s") % locals())

    return result


def sm_servicemanage_get_all(context):
    return model_query(context, models.SMServiceManage, read_deleted="yes").all()


###############################


@require_context
def backup_get(context, backup_id, session=None):
    result = model_query(context, models.Backup,
                         session=session, project_only=True).\
        filter_by(id=backup_id).\
        first()

    if not result:
        raise exception.BackupNotFound(backup_id=backup_id)

    return result


@require_admin_context
def backup_get_all(context):
    return model_query(context, models.Backup).all()


@require_admin_context
def backup_get_all_by_host(context, host):
    return model_query(context, models.Backup).filter_by(host=host).all()


@require_context
def backup_get_all_by_project(context, project_id):
    authorize_project_context(context, project_id)

    return model_query(context, models.Backup).\
        filter_by(project_id=project_id).all()


@require_context
def backup_create(context, values):
    backup = models.Backup()
    if not values.get('id'):
        values['id'] = str(uuid.uuid4())
    backup.update(values)
    backup.save()
    return backup


@require_context
def backup_update(context, backup_id, values):
    session = get_session()
    with session.begin():
        backup = model_query(context, models.Backup,
                             session=session, read_deleted="yes").\
            filter_by(id=backup_id).first()

        if not backup:
            raise exception.BackupNotFound(
                _("No backup with id %(backup_id)s") % locals())

        backup.update(values)
        backup.save(session=session)
    return backup


@require_admin_context
def backup_destroy(context, backup_id):
    session = get_session()
    with session.begin():
        session.query(models.Backup).\
            filter_by(id=backup_id).\
            update({'status': 'deleted',
                    'deleted': True,
                    'deleted_at': timeutils.utcnow(),
                    'updated_at': literal_column('updated_at')})

