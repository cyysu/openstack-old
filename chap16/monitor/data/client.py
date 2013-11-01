
from monitorclient.v1 import client

ec = client.Client('monitor','keystone_monitor_password','service','http://10.239.52.12:5000/v2.0/')

#ret = ec.monitors.pas_host_select()
ret = ec.monitors.list()

print ret
